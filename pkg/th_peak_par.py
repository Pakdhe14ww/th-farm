#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runner PARALEL TokenHarbor dengan solver peak.fo.

Menggantikan SolveGate (kuota habis) dengan api.peak.fo, dan memakai runner
paralel bawaan `th_par.py` (thread + worker, 1 akun = 1 IP dari pool).

Pakai:
    TH_SKIP_PRECHECK=1 python th_peak_par.py --target 1000 --workers 8
"""
import argparse, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
TH_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, TH_DIR)

KEYS_FILE = os.environ.get("PEAK_KEYS_FILE",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "peak_keys.txt"))
SITEKEY = "0x4AAAAAADBuC8Knz1EJZx9-"
PEAK_URL = "https://api.peak.fo/solve"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

KEYS = [l.strip() for l in open(KEYS_FILE) if l.strip()] if os.path.exists(KEYS_FILE) else []
ROT = {"i": 0}
STATS = {"call": 0, "ok": 0, "flag": 0, "err": 0, "dead": []}

# ── inbox: tempmail.cloud (JALUR GUEST — tanpa register, tanpa Gmail) ──
# POST /api/mailboxes -> 201 {"mailbox":{"email"},"token"}  (token = kunci baca)
# WAJIB kirim header browser (User-Agent + Origin + Referer): tanpa itu
# GET /api/messages balas 403. Lihat skill `tempmail-inbox`.
#
# Cadangan: mailgen (domain sendiri, catch-all) kalau tempmail.cloud down.
# Provider temp-mail lama (MAIL_API di th.py) sudah mati: 109 attempt gagal
# `$11`/403 SEBELUM connect — semuanya mati di get_temp_email().
import json as _json
import re as _re
import threading as _th
import urllib.request as _url

TM_URL = "https://tempmail.cloud/api/mailboxes"
TM_MSG = "https://tempmail.cloud/api/messages"
TM_DOM = os.environ.get("TH_TM_DOMAIN", "tempmail.cloud")
_BROWSER_HDRS = {
    "User-Agent": UA,
    "Origin": "https://tempmail.cloud",
    "Referer": "https://tempmail.cloud/",
}
_tm_boxes = {}          # email -> token (kunci baca)
_tm_lock = _th.Lock()
TM_STATS = {"box": 0, "box_err": 0, "poll": 0, "hit": 0, "direct": 0}


def _tm_pick_proxy():
    """Proxy sekali-pakai dari pool TH (BD) untuk bikin/baca inbox.

    tempmail.cloud memblokir IP box ini (`428 browser_session_required`).
    Lewat proxy (PS/BD) endpoint balas 201 konsisten. Proxy yang sama dipakai
    untuk bikin inbox DAN membacanya, lalu dibuang (1 inbox = 1 proxy).
    """
    try:
        import th as _th
        if not _th.list_proxy:
            _th.load_proxy_list()
        if _th.list_proxy:
            px = _th.list_proxy.pop()
            url = px.get("http") or px.get("https") or ""
            return url.replace("http://", "").replace("https://", "")
    except Exception:
        pass
    return None


def _tm_urlopen(url, *, data=None, headers=None, proxy=None, timeout=30):
    """urlopen dengan proxy opsional (http/https)."""
    if not proxy:
        req = _url.Request(url, data=data, method="POST" if data else "GET", headers=headers or {})
        return _url.urlopen(req, timeout=timeout)
    op = _url.build_opener(_url.ProxyHandler({"http": "http://" + proxy,
                                              "https": "http://" + proxy}))
    req = _url.Request(url, data=data, method="POST" if data else "GET", headers=headers or {})
    return op.open(req, timeout=timeout)


_tm_prox = {}           # email -> proxy yang dipakai (1 inbox = 1 proxy)


def _tm_new_email():
    """Bikin inbox guest tempmail.cloud. Return email (token+proxy disimpan).

    WAJIB lewat proxy: dari IP box endpoint balas `428 browser_session_required`
    (tempmail.cloud memblokir IP ini). Lewat proxy -> 201 konsisten.
    """
    import random as _r
    for attempt in range(4):
        proxy = _tm_pick_proxy()
        try:
            local = "th" + "".join(_r.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=9))
            body = _json.dumps({"localPart": local, "password": "Str0ngPw!2345x",
                                "domain": TM_DOM}).encode()
            h = dict(_BROWSER_HDRS); h["Content-Type"] = "application/json"
            with _tm_urlopen(TM_URL, data=body, headers=h, proxy=proxy) as r:
                d = _json.loads(r.read())
            em = ((d.get("mailbox") or {}).get("email") or "").strip()
            tok = d.get("token") or ""
            if em and tok:
                with _tm_lock:
                    _tm_boxes[em] = tok
                    _tm_prox[em] = proxy
                    TM_STATS["box"] += 1
                if not proxy:
                    TM_STATS["direct"] += 1
                return em
        except Exception:
            continue
    TM_STATS["box_err"] += 1
    return None


def _tm_messages(email):
    """Ambil semua pesan inbox (butuh header browser, else 403)."""
    with _tm_lock:
        tok = _tm_boxes.get(email)
        proxy = _tm_prox.get(email)
    if not tok:
        return []
    try:
        with _tm_urlopen(TM_MSG, headers=dict(_BROWSER_HDRS,
                                             **{"Authorization": "Bearer " + tok}),
                         proxy=proxy) as r:
            return (_json.loads(r.read()).get("messages") or [])
    except Exception:
        return []


def _tm_verify_link(email, timeout=280):
    """Cari link verifikasi TokenHarbor di inbox tempmail.cloud."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        blob = ""
        for msg in _tm_messages(email):
            mid = msg.get("id")
            # PENTING: isi PENUH dulu, `preview` BELAKANGAN.
            # `preview` cuma ~150 char dan MEMOTONG token di tengah; kalau preview
            # ditaruh depan, regex menangkap token buntung -> link balas
            # `dashboard?verify=invalid` (bug terukur 2026-10-03).
            if mid:
                try:
                    with _tm_lock:
                        tok = _tm_boxes.get(email)
                        proxy = _tm_prox.get(email)
                    with _tm_urlopen(f"{TM_MSG}/{mid}", headers=dict(
                            _BROWSER_HDRS, **{"Authorization": "Bearer " + tok}),
                            proxy=proxy) as r:
                        d = _json.loads(r.read())
                    blob += (d.get("text") or "") + (d.get("html") or "")
                except Exception:
                    pass
            blob += (msg.get("subject") or "") + (msg.get("preview") or "")
            if blob:
                break
        if blob:
            TM_STATS["hit"] += 1
            b = (blob.replace("\\r\\n", "").replace("=\r\n", "").replace("=3D", "=")
                     .replace("\\/", "/").replace("&amp;", "&"))
            # minimal 40 char: token asli ~150 char, token buntung selalu lebih pendek
            m = _re.search(r"verify-email\?token=([A-Za-z0-9_\-]{40,})", b)
            if m:
                return f"https://tokenharbor.ai/verify-email?token={m.group(1)}"
        TM_STATS["poll"] += 1
        time.sleep(3)
    return None


def _mg_new_email():
    """Cari link verifikasi TokenHarbor di inbox mailgen."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        blob = ""
        try:
            for msg in _MG.messages(email):
                d = _MG.read(msg) or {}
                blob += (d.get("text") or "") + (d.get("html") or "") + (msg.get("raw") or "")
        except Exception:
            pass
        if blob:
            b = (blob.replace("\\r\\n", "").replace("=\r\n", "")
                     .replace("=3D", "=").replace("\\/", "/").replace("&amp;", "&"))
            m = _re.search(r"verify-email\?token=([A-Za-z0-9_\-]+)", b)
            if m:
                return f"https://tokenharbor.ai/verify-email?token={m.group(1)}"
            m2 = _re.search(r"https?://[^'\" >]*verify-email\?token=[A-Za-z0-9_\-]+", b)
            if m2:
                return m2.group(0)
        time.sleep(3)
    return None


def peak_solve(px, timeout=None):
    """Token Turnstile lewat peak.fo — task_type WAJIB `turnstiletaskproxyless`.

    JANGAN kirim proxy ke solver: `turnstiletask` + IP PS = 100% "Challenge was
    flagged". Token proxyless diterima gate TokenHarbor (terukur: asli -> 303,
    sampah -> "Bot check failed").
    """
    import json, urllib.error, urllib.request
    if not KEYS:
        return None
    n = len(KEYS)
    for attempt in range(n):
        k = KEYS[(ROT["i"] + attempt) % n]
        if k in STATS["dead"]:
            continue
        STATS["call"] += 1
        body = json.dumps({"task_type": "turnstiletaskproxyless",
                           "sitekey": SITEKEY,
                           "url": "https://tokenharbor.ai/login?mode=signup"}).encode()
        req = urllib.request.Request(
            PEAK_URL, data=body, method="POST",
            headers={"X-API-Key": k, "Content-Type": "application/json", "User-Agent": UA,
                     "Origin": "https://peak.fo", "Referer": "https://peak.fo/"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout or 120) as r:
                j = json.loads(r.read())
        except urllib.error.HTTPError as e:
            STATS["err"] += 1
            if e.code in (401, 402):
                STATS["dead"].append(k)
            continue
        except Exception:
            STATS["err"] += 1
            continue
        tok = ((j.get("data") or {}).get("token") or "")
        if j.get("success") and tok:
            STATS["ok"] += 1
            ROT["i"] = (ROT["i"] + 1) % n
            return tok
        err = str(j.get("error") or "")
        if "flagged" in err:
            STATS["flag"] += 1
            ROT["i"] = (ROT["i"] + 1) % n
            continue
        STATS["err"] += 1
        continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--attempts", type=int, default=None)
    ap.add_argument("--out", default=os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "akun.txt"))
    ap.add_argument("--pool", default=None)
    ap.add_argument("--email", choices=["tempmail", "mailgen"], default="tempmail",
                    help="provider inbox (default: tempmail.cloud guest)")
    a = ap.parse_args()

    print(f"peak.fo keys: {len(KEYS)}", flush=True)
    if not KEYS:
        print("tidak ada key peak.fo", flush=True)
        return 1

    import th
    th.solvegate_proxy = peak_solve
    th.SG_ON = True
    # inbox: tempmail.cloud (guest) — TANPA register/Gmail. Kalau gagal -> mailgen.
    if a.email == "mailgen":
        th.get_temp_email = _mg_new_email
        th.poll_verify_link = _mg_verify_link
        print("inbox: mailgen (domain sendiri)", flush=True)
    else:
        th.get_temp_email = _tm_new_email
        th.poll_verify_link = _tm_verify_link
        print(f"inbox: tempmail.cloud (guest) domain={TM_DOM}", flush=True)

    # teruskan ke th_par.py
    sys.argv = ["th_par.py", "--target", str(a.target), "--workers", str(a.workers),
                "--out", a.out]
    if a.attempts:
        sys.argv += ["--attempts", str(a.attempts)]
    if a.pool:
        sys.argv += ["--pool", a.pool]

    import th_par
    t0 = time.time()
    rc = th_par.main()
    print(f"\nsolver peak.fo: call={STATS['call']} ok={STATS['ok']} "
          f"flag={STATS['flag']} err={STATS['err']} key_mati={len(STATS['dead'])} "
          f"({time.time()-t0:.0f}s)", flush=True)
    print(f"inbox: box={TM_STATS['box']} box_err={TM_STATS['box_err']} "
          f"hit={TM_STATS['hit']} poll={TM_STATS['poll']} "
          f"tanpa_proxy={TM_STATS['direct']}", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
