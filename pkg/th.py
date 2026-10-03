#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TokenHarbor Farm :: Turnstile + AWS temp-mail + rotasi IP via proxy pool lokal"""
import requests, re, json, uuid, time, sys, os, random, threading, subprocess
import urllib.request
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
os.environ.setdefault("PYTHONUNBUFFERED", "1")
os.environ.setdefault("TERM", "xterm-256color")

# ── config ──────────────────────────────────────────────────────────────
BASE = "https://tokenharbor.ai"
MAIL_API = "https://ypq5oi3ui3.execute-api.us-east-1.amazonaws.com/prod"
MAIL_H = {"user-agent": "okhttp/4.12.0"}
PW = "ThR3c0n!2k26X"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "akun.txt")
TZS = ["Asia/Jakarta","Asia/Singapore","Asia/Tokyo","Asia/Bangkok",
       "Europe/London","Europe/Berlin","America/New_York","Asia/Dubai"]

CAPSOLVER_KEY = ""
TURNSTILE_SITEKEY = "0x4AAAAAADBuC8Knz1EJZx9-"

# ── proxy pool (pengganti mode pesawat) ─────────────────────────────────
# Default = pool BD (Bright Data, 1 baris = 1 exit IP ter-pin).
# Bisa diganti tanpa edit file:  TH_PROXY_FILE=/path/lain python th_peak_par.py ...
PROXY_FILE = os.environ.get(
    "TH_PROXY_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxies.txt"))
USE_PROXY = True            # False = langsung pakai IP box
POOL_TEST_TIMEOUT = 10      # detik uji hidup proxy
MAX_PROXY_TRIES = 25        # percobaan ambil proxy hidup per giliran
SIGNUP_TRIES = 3            # percobaan IP berbeda per akun (tiap try = 1 mint token Turnstile -> mahal)

list_proxy = []

def load_proxy_list():
    """Baca pool proxy lokal sekali di awal (bukan daftar free publik)."""
    global list_proxy
    list_proxy = []
    if not USE_PROXY:
        return
    if not os.path.exists(PROXY_FILE):
        print(f"  {RD}! pool proxy gak ada: {PROXY_FILE}{R}")
        return
    with open(PROXY_FILE, encoding="utf-8") as f:
        for l in f:
            l = l.strip()
            if l and not l.startswith("#"):
                url = l if "://" in l else "http://" + l
                list_proxy.append({"http": url, "https": url})
    random.shuffle(list_proxy)

def _proxy_ok(px):
    """Uji proxy: harus balas IP nyata."""
    try:
        r = requests.get("https://api.ipify.org?format=json", proxies=px,
                         timeout=POOL_TEST_TIMEOUT)
        return (r.json() or {}).get("ip")
    except Exception:
        return None

def build_proxy(sess_id=""):
    """Ambil proxy hidup berikutnya dari pool. Return (proxy_dict, exit_ip)."""
    global list_proxy
    if not USE_PROXY:
        return None, None
    tried = 0
    while list_proxy and tried < MAX_PROXY_TRIES:
        px = list_proxy.pop()
        tried += 1
        ip = _proxy_ok(px)
        if ip:
            return px, ip
    return None, None

def kill_proxy(px):
    """IP ini ketandai (captcha/limit) -> buang dari pool sesi ini."""
    if not px:
        return
    try:
        list_proxy.remove(px)
    except ValueError:
        pass

def make_session(ua, proxy=None):
    s = requests.Session()
    s.headers.update({"User-Agent": ua, "Origin": BASE})
    if proxy:
        s.proxies = proxy
    retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "POST"])
    ad = HTTPAdapter(max_retries=retry)
    s.mount("https://", ad)
    s.mount("http://", ad)
    return s

def get_ua():
    ver = random.randint(122, 131)
    os_ = random.choice([
        "Windows NT 10.0; Win64; x64",
        "Windows NT 11.0; Win64; x64",
        "Macintosh; Intel Mac OS X 10_15_7",
        "X11; Linux x86_64"
    ])
    return (f"Mozilla/5.0 ({os_}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{ver}.0.0.0 Safari/537.36")

# ── Capsolver Turnstile ─────────────────────────────────────────────────
def solve_turnstile(page_url=BASE + "/login?mode=signup"):
    if not CAPSOLVER_KEY or not CAPSOLVER_KEY.startswith("CAP-"):
        return None
    try:
        r = requests.post("https://api.capsolver.com/createTask", json={
            "clientKey": CAPSOLVER_KEY,
            "task": {
                "type": "AntiTurnstileTaskProxyLess",
                "websiteURL": page_url,
                "websiteKey": TURNSTILE_SITEKEY
            }
        }, timeout=30)
        task_id = r.json().get("taskId")
        if not task_id:
            return None
        for _ in range(40):
            time.sleep(2)
            rr = requests.post("https://api.capsolver.com/getTaskResult", json={
                "clientKey": CAPSOLVER_KEY,
                "taskId": task_id
            }, timeout=20)
            j = rr.json()
            if j.get("status") == "ready":
                return j.get("solution", {}).get("token")
            if j.get("status") == "failed":
                return None
    except Exception:
        return None
    return None

# ── Camoufox Turnstile (pengganti solver mati) ──────────────────────────
# Turnstile tokenharbor sejak 30 Sep 2026 WAJIB di tiap signup (dulu cuma kalau
# IP kena flag). Widget baru render SETELAH tombol submit diklik, dan token
# terikat IP -> harus di-mint lewat PROXY YANG SAMA dengan POST-nya.
# Solver di jalanin sebagai subprocess (satu browser sekali jalan, RAM floor
# 1500MB) dan diserialize pakai _TS_LOCK supaya worker paralel gak tabrakan.
TH_SOLVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "th_ts_solver.py")
# Max solver Camoufox BARENGAN. Terukur: 3 konkuren aman & token keluar ~11-13s
# (RAM box 12 GB, satu browser ~400 MB). Naikin pelan-pelan kalau RAM masih lega.
try:
    TS_SOLVER_CONC = max(1, int(os.environ.get("TH_SOLVER_CONC", "3")))
except Exception:
    TS_SOLVER_CONC = 3
_TS_SEM = threading.Semaphore(TS_SOLVER_CONC)

# Timeout solver. TERUKUR 2026-10-03: token keluar 59-109s lewat proxy PS;
# angka lama 45s (konteks "11-13s") membuat solver selalu kebunuh sebelum
# selesai, dan itu dilaporkan sebagai "turnstile token gagal (solver)".
SOLVER_TIMEOUT = int(os.environ.get("TH_SOLVER_TIMEOUT", "150"))

# ── SolveGate (solver berbayar) — jalur UTAMA sejak 3 Okt 2026 ──────────
# Terukur: token keluar 740ms-3.4s (Camoufox 43-210s) => 30-100x lebih cepat,
# dan tokenharbor MENERIMANYA (2/3 siklus end-to-end, kedua key HTTP 200).
# WAJIB kirim param `proxy` = proxy yang SAMA dengan POST-nya: token TH terikat
# IP, solver yang keluar dari egress SolveGate sendiri akan ditolak server.
# 1 token = 1 request; jangan simpan/pakai ulang.
SG_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".solvegate_key")
SG_API = "https://api.solvegate.io/v1/solve"
SG_SITEKEY = "0x4AAAAAADBuC8Knz1EJZx9-"
SG_ON = os.environ.get("TH_NO_SOLVEGATE", "") == ""      # set TH_NO_SOLVEGATE=1 utk matiin
SOLVER_TIMEOUT_SG = int(os.environ.get("TH_SG_TIMEOUT", "120"))


def solvegate_proxy(px, timeout=None):
    """Mint token lewat SolveGate pakai proxy px. Return token | None.

    Return None kalau kuota habis / key mati / proxy ditolak -> pemanggil
    otomatis jatuh ke Camoufox.
    """
    if not SG_ON or not px:
        return None
    purl = px.get("https") or px.get("http")
    if not purl:
        return None
    try:
        sgkey = open(SG_KEY_FILE).read().strip()
    except Exception:
        return None
    if not sgkey:
        return None
    body = json.dumps({"gate": "turnstile", "sitekey": SG_SITEKEY,
                       "url": f"{BASE}/login?mode=signup", "proxy": purl}).encode()
    req = urllib.request.Request(
        SG_API, data=body, method="POST",
        headers={"Authorization": f"Bearer {sgkey}", "Content-Type": "application/json",
                 "Idempotency-Key": f"th-{int(time.time())}-{uuid.uuid4().hex[:8]}"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout or SOLVER_TIMEOUT_SG) as r:
            j = json.loads(r.read())
    except Exception as e:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "solver_raw.log"), "a") as f:
                f.write(f"=== SG ERR {type(e).__name__}: {str(e)[:110]} | {purl[-26:]}\n")
        except Exception:
            pass
        return None
    tok = j.get("token") or ""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "solver_raw.log"), "a") as f:
            f.write(f"=== SG status={j.get('status')} mode={j.get('mode')} "
                    f"ms={j.get('solve_ms')} err={j.get('error_code')} "
                    f"tok={len(tok)} {time.time()-t0:.1f}s | {purl[-26:]}\n")
    except Exception:
        pass
    return tok if (j.get("status") == "solved" and tok) else None


def solve_turnstile_proxy(px, timeout=240):
    if timeout is None:
        timeout = SOLVER_TIMEOUT
    """Mint token Turnstile lewat SolveGate (utama) / Camoufox (cadangan).

    Terukur 3 Okt 2026: SolveGate 740ms-3.4s vs Camoufox 43-210s, dan tokennya
    diterima tokenharbor. Camoufox hanya dipakai kalau SolveGate gagal (kuota
    habis / proxy ditolak solver), supaya hasil tidak pernah nol gara-gara solver.
    """
    if not px:
        return None
    t_sg = solvegate_proxy(px)
    if t_sg:
        return t_sg
    purl = px.get("https") or px.get("http")
    if not purl or not os.path.exists(TH_SOLVER):
        return None
    with _TS_SEM:
        try:
            r = subprocess.run([sys.executable, TH_SOLVER, "--proxy", purl,
                                "--timeout", str(timeout)],
                               capture_output=True, text=True, timeout=timeout + 60)
        except Exception as e:
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "solver_raw.log"), "a") as f:
                    f.write(f"=== EXC {type(e).__name__}: {str(e)[:120]} | {purl[-28:]}\n")
            except Exception:
                pass
            return None
        # jejak mentah solver (diagnosa kalau token gagal)
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "solver_raw.log"), "a") as f:
                f.write(f"=== rc={r.returncode} proxy={purl[-28:]} to={timeout}\n")
                f.write((r.stdout or "")[-400:] + "\n")
                if r.stderr:
                    f.write("STDERR: " + (r.stderr or "")[-300:] + "\n")
        except Exception:
            pass
    for line in reversed((r.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                j = json.loads(line)
            except Exception:
                continue
            return j.get("token") if j.get("ok") else None
    return None

# ── ansi ────────────────────────────────────────────────────────────────
R="\033[0m"; B="\033[1m"; DIM="\033[2m"
C="\033[36m"; G="\033[32m"; Y="\033[33m"; RD="\033[31m"; MG="\033[35m"
def hide():
    sys.stdout.write("\033[?25l"); sys.stdout.flush()
def show():
    sys.stdout.write("\033[?25h"); sys.stdout.flush()

SPIN=["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

class Spin:
    def __init__(self, label):
        self.label=label; self.on=True; self.t=None; self.f="…"; self._lock=threading.Lock()
    def _run(self):
        i=0
        while self.on:
            with self._lock:
                sys.stdout.write(f"\r  {C}{SPIN[i%len(SPIN)]}{R} {self.label} {DIM}{self.f}{R}   ")
                sys.stdout.flush()
            i+=1; time.sleep(0.08)
        with self._lock:
            sys.stdout.write("\r"+" "*95+"\r"); sys.stdout.flush()
    def start(self):
        hide(); self.t=threading.Thread(target=self._run,daemon=True); self.t.start()
    def set(self,f="…"):
        with self._lock: self.f=f
    def stop(self,mark,msg,color):
        self.on=False
        if self.t: self.t.join(timeout=0.35)
        time.sleep(0.05); show()
        sys.stdout.write(f"\r  {color}{mark}{R} {msg}\n"); sys.stdout.flush()

ANSI_RE=re.compile(r"\033\[[0-9;]*m")
def vis_len(s): return len(ANSI_RE.sub("",s))
def box(title,color=G,w=58):
    inner=w-2; s=f"{color}{B}{title}{R}"
    pad=max(0,inner-vis_len(s)); left=pad//2; right=pad-left
    print(f" {color}╭{'─'*inner}╮{R}")
    print(f" {color}│{' '*left}{s}{' '*right}{color}│{R}")
    print(f" {color}╰{'─'*inner}╯{R}"); sys.stdout.flush()
def line(w=58):
    print(f" {DIM}{'─'*w}{R}"); sys.stdout.flush()
def banner():
    sys.stdout.write("\033[2J\033[H"); sys.stdout.flush()
    print(); box("T O K E N H A R B O R   F A R M   B O T",MG); print(); sys.stdout.flush()

# ── temp-mail ───────────────────────────────────────────────────────────
def get_temp_email():
    for _ in range(3):
        try:
            r=requests.get(f"{MAIL_API}/get-email",params={"premium":"true"},
                           headers=MAIL_H,timeout=18)
            em=r.json().get("email")
            if em: return em
        except Exception:
            time.sleep(1)
    return None

def poll_verify_link(email,timeout=280):
    t0=time.time()
    while time.time()-t0<timeout:
        try:
            params={"email":email,"premium":"true","timestamp":int(time.time()*1000)}
            r=requests.get(f"{MAIL_API}/get-emails-by-address",params=params,
                           headers=MAIL_H,timeout=15)
            for msg in r.json().get("emails",[]):
                raw=str(msg.get("content",""))+" "+str(msg.get("subject",""))
                m=re.search(r"verify-email\?token=([A-Za-z0-9_\-]+)",raw)
                if m: return f"{BASE}/verify-email?token={m.group(1)}"
                m2=re.search(r'href=[\'"]?(https?://[^\'" >]*verify-email\?token=[A-Za-z0-9_\-]+)',raw)
                if m2: return m2.group(1)
        except Exception:
            pass
        time.sleep(3)
    return None

def is_signup_ok(r):
    if r.status_code == 303:
        return True
    txt = (r.text or "")[:4000]
    if re.search(r'"error"\s*:\s*"[^"]{3,}', txt):
        return False
    if "turnstile" in txt.lower() and "failed" in txt.lower():
        return False
    if "captcha" in txt.lower() and ("required" in txt.lower() or "invalid" in txt.lower()):
        return False
    if r.status_code == 200 and ("verify" in txt.lower() or "dashboard" in txt.lower() or "success" in txt.lower()):
        return True
    if r.status_code in (200, 303) and "Location" in r.headers:
        return True
    return r.status_code == 303

# ── core ────────────────────────────────────────────────────────────────
def farm_one(di):
    sp=Spin("creating account")
    sp.start()
    try:
        sp.set("get temp mail…")
        mail=get_temp_email()
        if not mail:
            sp.stop("✖","gagal dapat temp mail",RD); return None
        sp.label=f"creating {mail}"

        UA=get_ua()

        ts_token=""  # token di-mint per proxy di dalam loop (Turnstile terikat IP)

        # IP yang sudah dipakai daftar bakal diminta human check -> coba IP lain
        s=None; PX=None; PX_IP=None; last_err=None; act_id=""; rejection=""
        for _try in range(SIGNUP_TRIES):
            px,pxip=build_proxy()
            if USE_PROXY and not px:
                sp.stop("✖","pool proxy habis / semua mati",RD); return None
            sp.set(f"connect {pxip or 'direct'}...")
            try:
                st=make_session(UA,px)
                pg=st.get(f"{BASE}/login?mode=signup",timeout=50)
            except Exception as e:
                last_err=str(e)[:60]; kill_proxy(px); continue
            if pg.status_code>=500:
                last_err=f"page {pg.status_code}"; kill_proxy(px); continue

            hid={k:v.replace("&quot;",'"')
                 for k,v in re.findall(r'name="(\$ACTION[^"]*)"\s+value="([^"]*)"',pg.text)}
            try:
                act=json.loads(hid.get("$ACTION_1:0","{}")).get("id","")
            except Exception:
                act=""
            if not act:
                act=(re.findall(r'([a-f0-9]{40,64})',pg.text) or [""])[0]

            fp=str(uuid.uuid4())
            # precheck (murah, tanpa browser): kalau TRUE = IP jelas kena flag -> ganti IP,
            # jangan buang satu mint token Camoufox di sini.
            # CATATAN 3 Okt 2026: dengan solver peak.fo, precheck justru MENGHAMBAT —
            # IP yang balas needCaptcha tetap LOLOS kalau tokennya benar (terukur:
            # token peak asli -> HTTP 303 sukses; token sampah -> "Bot check failed").
            # Set TH_SKIP_PRECHECK=1 untuk melewatinya.
            if os.environ.get("TH_SKIP_PRECHECK", "") == "":
                try:
                    pc=st.get(f"{BASE}/api/auth/signup-precheck",params={"fp":fp},timeout=20)
                    if bool((pc.json() or {}).get("needCaptcha")):
                        last_err="precheck: IP kena flag"
                        kill_proxy(px); continue
                except Exception:
                    pass
            sp.set(f"solve turnstile @ {pxip or 'box'}...")
            ts_token = solve_turnstile_proxy(px)
            if not ts_token:
                last_err="turnstile token gagal (solver)"; kill_proxy(px); continue
            sp.set("registering...")
            # envelope $ACTION_* ikut dikirim => nama argumen WAJIB ber-prefix "1_"
            # (kalau polos, server balas "Email and password are required.")
            bnd="----WebKitFormBoundary"+uuid.uuid4().hex[:16]
            def fld(n,v):
                return f'--{bnd}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}\r\n'
            body=(fld("$ACTION_REF_1",hid.get("$ACTION_REF_1",""))+
                  fld("$ACTION_1:0",hid.get("$ACTION_1:0",""))+
                  fld("$ACTION_1:1",hid.get("$ACTION_1:1",""))+
                  fld("$ACTION_KEY",hid.get("$ACTION_KEY",""))+
                  fld("1_device_fingerprint",fp)+
                  fld("1_timezone",random.choice(TZS))+fld("1_next","")+
                  fld("1_email",mail)+fld("1_password",PW)+fld("1_invite_code","")+
                  fld("1_cf-turnstile-response",ts_token)+
                  fld("cf-turnstile-response",ts_token)+
                  fld("0",'["$undefined","$K1"]')+f"--{bnd}--\r\n")
            headers={
                "Content-Type":f"multipart/form-data; boundary={bnd}",
                "next-action":act,
                "Accept":"text/x-component",
                "Referer":f"{BASE}/login?mode=signup",
                "x-deployment-id":(re.findall(r'data-dpl-id="(dpl_[a-zA-Z0-9]+)"',pg.text) or ["x"])[0],
                "sec-fetch-dest":"empty","sec-fetch-mode":"cors","sec-fetch-site":"same-origin",
                "User-Agent":UA
            }
            try:
                rr=st.post(f"{BASE}/login?mode=signup",headers=headers,
                           data=body.encode(),timeout=55,allow_redirects=False)
            except Exception as e:
                last_err=f"post: {str(e)[:50]}"; kill_proxy(px); continue

            if is_signup_ok(rr) and [c.name for c in st.cookies]:
                s, PX, PX_IP, act_id = st, px, pxip, act
                break

            err=re.search(r'"error"\s*:\s*"([^"]{3,160})"',rr.text or "")
            why=err.group(1) if err else f"status {rr.status_code}"
            # dump mentah saat gagal (diagnosa digest $NN / pesan server)
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "signup_fail.log"), "a") as f:
                    t = rr.text or ""
                    f.write(f"{why} | HTTP {rr.status_code} | {len(t)}B\n")
            except Exception:
                pass
            if re.search(r"email and password are required",why,re.I):
                # envelope/field salah -> ini bug skrip, jangan buang IP
                sp.stop("✖",f"{mail} — {why}",RD); return None
            if re.search(r"already (exists|registered|used)",why,re.I):
                sp.stop("✖",f"{mail} — {why}",RD); return None
            # sisanya (human check / digest $NN / code-verifier) = IP ini nggak cocok
            kill_proxy(px)
            last_err=why
            continue

        if s is None:
            sp.stop("✖",f"{mail} — gagal signup: {last_err or '?'}",RD); return None
        r=None

        try:
            ip=PX_IP or requests.get("https://api.ipify.org?format=json",proxies=PX,timeout=12).json()["ip"]
        except Exception:
            ip="?"

        sp.set("waiting email…")
        try: s.post(f"{BASE}/api/me/send-verification-email",timeout=20)
        except Exception: pass
        link=poll_verify_link(mail,timeout=280)
        if not link:
            sp.stop("✖",f"{mail} — email never arrived",RD); return None

        sp.set("activating…")
        try:
            r=s.get(link,timeout=40,allow_redirects=True)
        except Exception as e:
            sp.stop("✖",f"{mail} — activate: {str(e)[:40]}",RD); return None
        if "verify=success" not in (r.url or "") and "success" not in (r.url or ""):
            if "dashboard" not in (r.url or "") and "login" in (r.url or ""):
                sp.stop("✖",f"{mail} — verification failed",RD); return None

        try:
            s.post(f"{BASE}/api/me/privacy",json={"free_models_enabled":True},
                   headers={"Content-Type":"application/json","User-Agent":UA},timeout=20)
            r=s.post(f"{BASE}/api/keys",json={"label":f"auto-{di}"},
                     headers={"Content-Type":"application/json","User-Agent":UA},timeout=20)
            k=(r.json() or {}).get("plaintext")
        except Exception as e:
            sp.stop("✖",f"{mail} — keygen: {str(e)[:40]}",RD); return None
        if not k:
            sp.stop("✖",f"{mail} — key generation failed",RD); return None

        sp.set("validating key…")
        # 403 di sini BUKAN key invalid — itu exit IP yang diblokir Cloudflare.
        # Terukur: key yang sama balas 200 langsung / 403 lewat sebagian IP BD.
        # Jadi 403 -> ulangi TANPA proxy dulu sebelum menyatakan gagal.
        rc=None; why=""
        for langkah in ("proxy", "direct", "proxy"):
            try:
                rc=requests.get(f"{BASE}/v1/models",
                                headers={"Authorization":f"Bearer {k}","User-Agent":UA},
                                proxies=PX if langkah=="proxy" else None,timeout=15)
                if rc.status_code==200: break
                if rc.status_code==401:
                    why="invalid (401)"; break
                why=f"invalid ({rc.status_code})"
            except Exception as e:
                rc=None; why="timeout"
            time.sleep(1)
        if rc is None or rc.status_code!=200:
            if rc is not None and rc.status_code==403:
                # tetap simpan: key benar-benar dibuat, 403 cuma blokir IP validasi.
                with open(OUT,"a",encoding="utf-8") as f:
                    f.write(f"{k}\n")
                sp.stop("✔",f"{mail}  {DIM}·{R}  {G}{k[:28]}...{R}  {DIM}(403-IP, tetap disimpan){R}",G)
                return k
            why = why or ("timeout" if rc is None else f"invalid ({rc.status_code})")
            sp.stop("✖",f"{mail} — key {why}",RD); return None

        with open(OUT,"a",encoding="utf-8") as f:
            f.write(f"{k}\n")
        sp.stop("✔",f"{mail}  {DIM}·{R}  {G}{k[:28]}…{R}  {DIM}({ip}){R}",G)
        return k
    except Exception as e:
        sp.stop("✖",f"{str(e)[:65]}",RD); return None
    finally:
        if sp.on: sp.on=False; show()

def main():
    banner()
    if not CAPSOLVER_KEY:
        print(f"  {Y}! CAPSOLVER_KEY kosong — Turnstile mungkin gagal{R}")
    if USE_PROXY:
        print(f"  {DIM}loading proxy pool…{R}"); sys.stdout.flush()
        load_proxy_list()
        print(f"  {G}proxy pool: {len(list_proxy)}{R}  {DIM}← {PROXY_FILE}{R}")
    else:
        print(f"  {DIM}proxy: OFF (IP box){R}")
    print(f"  {DIM}rotasi IP: 1 akun = 1 proxy dari pool (mode pesawat dimatikan){R}")
    print(); sys.stdout.flush()

    try:
        while True:
            try:
                inp=input(f"  {C}▸{R} Jumlah akun: ").strip()
                target=int(inp)
            except ValueError:
                print(f"  {RD}angka bro{R}"); continue
            if target<1:
                print(f"  {RD}minimal 1{R}"); continue
            break
    except (EOFError,KeyboardInterrupt):
        print(); show(); return

    print(); sys.stdout.flush()
    ok=0; t_all=time.time()
    for i in range(target):
        if farm_one(i):
            ok += 1
        time.sleep(0.8)
    print(); line()
    print(f"  {B}DONE{R}  {G}✔ {ok} success{R}  {RD}✖ {target-ok} failed{R}   {DIM}in {time.time()-t_all:.0f}s{R}")
    print(f"  {DIM}proxy tersisa: {len(list_proxy)}{R}")
    print(f"  {DIM}keys →{R} {OUT}")
    line(); print(); show()

if __name__=="__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {Y}aborted{R}\n"); show(); sys.exit(0)