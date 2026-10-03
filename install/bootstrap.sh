#!/usr/bin/env bash
# Bootstrap: pasang farm di VPS baru dengan SATU perintah.
#
#   curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install/bootstrap.sh | \
#     sudo REPO=OWNER/REPO GH_TOKEN=xxx bash
#
# Repo private -> pakai token di URL:
#   sudo GH_TOKEN=xxx bash -c "$(curl -fsSL -H 'Authorization: token xxx' \
#        -H 'Accept: application/vnd.github.raw' \
#        https://api.github.com/repos/OWNER/REPO/contents/install/bootstrap.sh)"
#
# Repo public -> cukup:  sudo REPO=owner/repo bash install_th_farm.sh
set -euo pipefail

REPO="${REPO:-Pakdhe14ww/th-farm}"
GH_TOKEN="${GH_TOKEN:-}"
BRANCH="${BRANCH:-main}"
DIR="${DIR:-/opt/thfarm}"

log() { printf '\033[36m[bootstrap]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[GAGAL]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "jalankan sebagai root (sudo)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ── ambil installer + paket lewat git (dan curl sebagai cadangan) ──
log "ambil paket dari $REPO"
if [ -n "$GH_TOKEN" ]; then
  GIT_URL="https://x-access-token:$GH_TOKEN@github.com/$REPO.git"
else
  GIT_URL="https://github.com/$REPO.git"
fi

ok=0
if command -v git >/dev/null 2>&1; then
  git clone --depth 1 --quiet "$GIT_URL" "$WORK/src" && ok=1
else
  log "git belum ada — pasang dulu"
  if command -v apt-get >/dev/null; then
    export DEBIAN_FRONTEND=noninteractive; apt-get update -qq
    apt-get install -y -qq git >/dev/null
  elif command -v dnf >/dev/null; then dnf install -y -q git >/dev/null
  elif command -v apk >/dev/null; then apk add --quiet git
  fi
  git clone --depth 1 --quiet "$GIT_URL" "$WORK/src" && ok=1
fi

if [ "$ok" -ne 1 ]; then
  log "git clone gagal — coba unduh mentah dari API"
  mkdir -p "$WORK/src/install" "$WORK/src/pkg"
  for f in install/install_th_farm.sh pkg/th.py pkg/th_par.py pkg/th_peak_par.py; do
    curl -fsSL --retry 3 -H "Authorization: token $GH_TOKEN" \
      -H "Accept: application/vnd.github.raw" \
      "https://api.github.com/repos/$REPO/contents/$f?ref=$BRANCH" \
      -o "$WORK/src/$f" || die "gagal unduh $f (cek REPO/GH_TOKEN)"
  done
  for f in proxies_all.txt peak_keys.txt; do
    curl -fsSL --retry 3 -H "Authorization: token $GH_TOKEN" \
      -H "Accept: application/vnd.github.raw" \
      "https://api.github.com/repos/$REPO/contents/$f?ref=$BRANCH" \
      -o "$WORK/src/$f" || log "  (lewati $f — tidak wajib)"
  done
fi

INST="$WORK/src/install/install_th_farm.sh"
[ -s "$INST" ] || die "installer tidak ketemu di repo ($REPO)"
chmod +x "$INST"

# ── paket lokal (dari clone) dipakai installer supaya tidak unduh ulang ──
log "jalankan installer"
REPO="$REPO" GH_TOKEN="$GH_TOKEN" DIR="$DIR" \
SRC_LOCAL="$WORK/src" \
  bash "$INST"
