#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runner PARALEL buat th.py (TokenHarbor farm).
N worker barengan, 1 akun = 1 IP dari pool. Output key nambah ke akun.txt (sama kayak th.py).

Pakai:
    python3 th_par.py --target 10 --workers 10 --attempts 20
"""
import os, sys, time, argparse, threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import th  # noqa: E402

LOG = os.path.join(HERE, "farm_par.log")
_lk = threading.Lock()
ANSI = th.ANSI_RE

# ── solver mati → matiin biar gak buang waktu tiap akun ─────────────────
th.CAPSOLVER_KEY = ""


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {ANSI.sub('', str(msg))}"
    with _lk:
        print(line, flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


class SilentSpin:
    """Duck-type pengganti th.Spin: no ANSI spinner, 1 baris log saat event selesai."""
    def __init__(self, label):
        self.label, self.on, self.f = label, True, ""
    def start(self): pass
    def set(self, f="..."): self.f = f
    def stop(self, mark, msg, color=""):
        self.on = False
        log(f"{mark} {msg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=10)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--attempts", type=int, default=None, help="total percobaan (default target*2)")
    ap.add_argument("--out", default=None,
                    help="file output key (default: akun.txt sebelah th.py). Pakai ini biar hasil run baru gak nyampur hasil lama")
    ap.add_argument("--pool", default=None,
                    help="file pool proxy (default: PROXY_FILE di th.py = LIVE/LIVE.txt)")
    a = ap.parse_args()
    attempts = a.attempts or a.target * 2

    if a.pool:
        th.PROXY_FILE = os.path.abspath(a.pool)

    if a.out:
        th.OUT = os.path.abspath(a.out)
        os.makedirs(os.path.dirname(th.OUT) or ".", exist_ok=True)

    th.Spin = SilentSpin
    th.load_proxy_list()
    log(f"PARALEL start | target={a.target} workers={a.workers} attempts={attempts} | pool={len(th.list_proxy)}")

    slots = list(range(attempts))
    slk = threading.Lock()
    st = {"ok": 0, "fail": 0, "id": 0}
    t0 = time.time()

    def take():
        with slk:
            if st["ok"] >= a.target or not slots:
                return None
            # pool kering -> berhenti, jangan buang attempt (tiap attempt bayar request temp-mail)
            if len(th.list_proxy) < a.workers:
                if not st.get("drained"):
                    st["drained"] = True
                    log(f"! pool kering (sisa {len(th.list_proxy)} < workers {a.workers}) — stop ambil attempt")
                return None
            st["id"] += 1
            return slots.pop(), st["id"]

    def worker(wid):
        while True:
            job = take()
            if job is None:
                return
            idx, keyid = job
            log(f"w{wid:02d} ▶ attempt #{idx+1} (key auto-{keyid})")
            try:
                k = th.farm_one(keyid)
            except Exception as e:
                k = None
                log(f"w{wid:02d} ✖ crash: {str(e)[:70]}")
            with slk:
                if k:
                    st["ok"] += 1
            log(f"w{wid:02d} {'✔' if k else '✖'} selesai | total sukses {st['ok']}/{a.target}")
            time.sleep(0.5)

    ths = [threading.Thread(target=worker, args=(i + 1,), daemon=True) for i in range(a.workers)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()

    dt = time.time() - t0
    log(f"DONE sukses={st['ok']}/{a.target} | gagal={st['id']-st['ok']} percobaan | {dt:.0f}s | pool sisa={len(th.list_proxy)}")
    log(f"keys → {th.OUT}")
    return 0 if st["ok"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())