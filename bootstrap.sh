#!/usr/bin/env bash
set -Eeuo pipefail
REPO_ARCHIVE="https://github.com/FerdanaRizky/pdhost-tes/archive/refs/heads/main.tar.gz"
if [[ $EUID -ne 0 ]]; then
  echo "Jalankan sebagai root: curl -fsSL https://raw.githubusercontent.com/FerdanaRizky/pdhost-tes/main/bootstrap.sh | sudo bash" >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y curl tar
  elif command -v dnf >/dev/null 2>&1; then dnf install -y curl tar
  elif command -v yum >/dev/null 2>&1; then yum install -y curl tar
  elif command -v apk >/dev/null 2>&1; then apk add --no-cache curl tar
  else echo '[ERROR] Instal curl dan tar terlebih dahulu.' >&2; exit 2; fi
fi
if ! command -v tar >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y tar
  elif command -v dnf >/dev/null 2>&1; then dnf install -y tar
  elif command -v yum >/dev/null 2>&1; then yum install -y tar
  elif command -v apk >/dev/null 2>&1; then apk add --no-cache tar
  else echo '[ERROR] Instal tar terlebih dahulu.' >&2; exit 2; fi
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo '[INFO] Mengunduh source OpenHost Panel dari GitHub...'
curl -fsSL --retry 3 "$REPO_ARCHIVE" -o "$TMP/source.tar.gz"
tar -xzf "$TMP/source.tar.gz" -C "$TMP"
SOURCE="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
[[ -n "$SOURCE" && -f "$SOURCE/install.sh" ]] || { echo '[ERROR] Arsip source tidak valid.' >&2; exit 3; }
bash "$SOURCE/install.sh"
