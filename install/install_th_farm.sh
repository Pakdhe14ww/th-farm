#!/usr/bin/env bash
# Pasang farm TokenHarbor di VPS baru — sekali jalan.
#
#   sudo REPO=owner/repo GH_TOKEN=xxx POOL_URL='https://…/pool.txt' \
#        PEAK_KEYS_B64=xxx bash install_th_farm.sh
#
# Hasil: layanan systemd `thfarm` yang auto-start + auto-restart.
set -euo pipefail

# ── setelan (bisa lewat env) ──────────────────────────────────────
REPO="${REPO:-}"                      # owner/repo  (di-clone lewat git)
GIT_URL="${GIT_URL:-}"                # atau URL git langsung (mirror/gitlab/ssh)
GH_TOKEN="${GH_TOKEN:-}"              # token untuk repo private
POOL_URL="${POOL_URL:-}"              # URL teks polos, 1 baris = 1 proxy
POOL_FILE="${POOL_FILE:-}"            # atau: sudah ada di disk
PEAK_KEYS_B64="${PEAK_KEYS_B64:-}"    # key solver peak.fo (base64, 1 key/baris)
PEAK_KEYS_FILE="${PEAK_KEYS_FILE:-}"
TARGET="${TARGET:-5000}"              # jumlah key yang diburu
WORKERS="${WORKERS:-20}"
ATTEMPTS="${ATTEMPTS:-100000}"
DIR="${DIR:-/opt/thfarm}"             # tempat pasang
SERVICE="${SERVICE:-thfarm}"
PY_BIN="${PY_BIN:-python3}"

log() { printf '\033[36m[%s]\033[0m %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { printf '\033[31m[GAGAL]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "jalankan sebagai root (sudo)"

# ── 1. dependensi sistem ─────────────────────────────────────────
log "pasang dependensi sistem"
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates >/dev/null
elif command -v dnf >/dev/null; then
  dnf install -y -q python3 python3-pip git curl ca-certificates >/dev/null
elif command -v apk >/dev/null; then
  apk add --quiet python3 py3-pip git curl ca-certificates
else
  die "distro tidak dikenal (butuh apt/dnf/apk)"
fi
command -v git >/dev/null || die "butuh git"
command -v systemctl >/dev/null || die "butuh systemd"

# ── 2. venv + paket python ───────────────────────────────────────
mkdir -p "$DIR"
log "siapkan venv di $DIR/venv"
[ -x "$DIR/venv/bin/python" ] || "$PY_BIN" -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install --quiet --upgrade pip
"$DIR/venv/bin/pip" install --quiet requests

# ── 3. ambil paket farm ──────────────────────────────────────────
mkdir -p "$DIR/pkg"
if [ -z "$GIT_URL" ] && [ -n "$REPO" ]; then
  if [ -n "$GH_TOKEN" ]; then GIT_URL="https://x-access-token:$GH_TOKEN@github.com/$REPO.git"
  else GIT_URL="https://github.com/$REPO.git"; fi
fi
if [ -n "$GIT_URL" ]; then
  # git clone: paling sederhana, sekali ambil semua (pkg + pool).
  log "git clone ${GIT_URL%%@*}…"
  SRC="$DIR/src"
  rm -rf "$SRC"
  git clone --depth 1 --quiet "$GIT_URL" "$SRC" \
    || die "git clone gagal (cek REPO/GH_TOKEN/GIT_URL)"
  cp -f "$SRC"/pkg/*.py "$DIR/pkg/" 2>/dev/null || true
  [ -s "$SRC/proxies_all.txt" ] && cp -f "$SRC/proxies_all.txt" "$DIR/pkg/_pool.txt" || true
  # jangan simpan token di remote
  git -C "$SRC" remote set-url origin "https://github.com/$REPO.git" 2>/dev/null || true
  log "  dapat: $(ls "$SRC/pkg" 2>/dev/null | tr '\n' ' ')"
fi
for f in th.py th_par.py th_peak_par.py; do
  [ -s "$DIR/pkg/$f" ] || die "pkg/$f tidak ada — isi REPO= atau taruh manual di $DIR/pkg/"
done
log "paket siap: $(ls "$DIR/pkg" | tr '\n' ' ')"

# ── 4. konfigurasi ───────────────────────────────────────────────
log "tulis konfigurasi"
if [ -n "$PEAK_KEYS_B64" ]; then
  printf '%s' "$PEAK_KEYS_B64" | base64 -d > "$DIR/pkg/peak_keys.txt"
elif [ -n "$PEAK_KEYS_FILE" ]; then
  cp "$PEAK_KEYS_FILE" "$DIR/pkg/peak_keys.txt"
fi
[ -s "$DIR/pkg/peak_keys.txt" ] || die "key solver kosong (isi PEAK_KEYS_B64 / PEAK_KEYS_FILE)"
log "  peak_keys.txt : $(grep -c . "$DIR/pkg/peak_keys.txt") key"

if [ -s "$DIR/pkg/_pool.txt" ]; then
  log "pakai pool dari clone git"
  cp "$DIR/pkg/_pool.txt" "$DIR/pkg/proxies.txt"
elif [ -n "$POOL_URL" ]; then
  # sumber bebas: teks polos, 1 baris = 1 proxy
  log "unduh pool dari POOL_URL"
  curl -fsSL --retry 3 --max-time 300 "$POOL_URL" -o "$DIR/pkg/proxies.txt" \
    || log "PERINGATAN: unduh pool gagal"
elif [ -n "$REPO" ] && [ -n "$GH_TOKEN" ]; then
  # berkas repo private (pool besar tidak muat di secret Actions: batas 48 KB)
  log "ambil pool dari $REPO (proxies_all.txt)"
  curl -fsSL --retry 3 --max-time 300 \
    -H "Authorization: token $GH_TOKEN" -H "Accept: application/vnd.github.raw" \
    "https://api.github.com/repos/$REPO/contents/proxies_all.txt" \
    -o "$DIR/pkg/proxies.txt" || log "PERINGATAN: unduh pool gagal"
elif [ -n "$POOL_FILE" ]; then
  log "pakai POOL_FILE lokal"
  cp "$POOL_FILE" "$DIR/pkg/proxies.txt"
fi
[ -s "$DIR/pkg/proxies.txt" ] || die "pool proxy kosong (isi POOL_URL / POOL_FILE)"
log "  proxies.txt   : $(grep -c . "$DIR/pkg/proxies.txt") proxy"
chmod 600 "$DIR/pkg/peak_keys.txt" "$DIR/pkg/proxies.txt"

# ── 5. layanan systemd ───────────────────────────────────────────
UNIT=/etc/systemd/system/$SERVICE.service
log "pasang layanan $SERVICE"
cat > "$UNIT" <<UNIT_EOF
[Unit]
Description=Farm TokenHarbor ($TARGET key)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR/pkg
Environment=TH_SKIP_PRECHECK=1
Environment=PYTHONUNBUFFERED=1
ExecStart=$DIR/venv/bin/python -u th_peak_par.py \\
  --target $TARGET --workers $WORKERS --attempts $ATTEMPTS \\
  --out $DIR/akun.txt --pool $DIR/pkg/proxies.txt
Restart=always
RestartSec=30
StandardOutput=append:$DIR/farm.log
StandardError=append:$DIR/farm.log

[Install]
WantedBy=multi-user.target
UNIT_EOF

systemctl daemon-reload
systemctl enable --quiet "$SERVICE" >/dev/null 2>&1 || true
systemctl restart "$SERVICE"
sleep 12
systemctl is-active --quiet "$SERVICE" || {
  log "layanan tidak hidup — 25 baris terakhir log:"; tail -25 "$DIR/farm.log" || true; exit 1; }

# ── 6. ringkasan ─────────────────────────────────────────────────
echo
log "SELESAI. Layanan '$SERVICE' jalan."
cat <<EOF

  status : systemctl status $SERVICE
  log    : tail -f $DIR/farm.log
  key    : wc -l $DIR/akun.txt
  berhenti: systemctl stop $SERVICE

  target $TARGET key · $WORKERS worker · paket dari ${REPO:-lokal}
EOF
echo
tail -3 "$DIR/farm.log" 2>/dev/null || true
