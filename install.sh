#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo '[ERROR] Jalankan sebagai root: sudo bash install.sh'; exit 1; }
APP_DIR=/opt/openhost-panel
CONF_DIR=/etc/openhost-panel
BACKUP_DIR="/var/backups/openhost-panel-$(date +%Y%m%d-%H%M%S)"
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
. /etc/os-release || true
log(){ printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  log "Memasang dependensi Debian/Ubuntu"
  apt-get update
  apt-get install -y nginx python3 php-cli php-fpm php-curl php-mbstring php-xml curl ca-certificates tar certbot
elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
  if command -v dnf >/dev/null 2>&1; then PM=dnf; else PM=yum; fi
  log "Memasang dependensi RHEL-family"
  "$PM" install -y nginx python3 php-cli php-fpm php-mbstring php-xml curl ca-certificates tar certbot
else
  echo "[ERROR] Package manager tidak didukung otomatis: $PRETTY_NAME" >&2
  echo "Versi berikutnya dapat menambah adapter untuk distribusi lain." >&2
  exit 2
fi
command -v systemctl >/dev/null 2>&1 || { echo '[ERROR] systemd diperlukan oleh installer ini.' >&2; exit 2; }
for binary in nginx python3 php; do
  command -v "$binary" >/dev/null || { echo "[ERROR] Dependensi $binary tidak ditemukan." >&2; exit 2; }
done

FPM_UNIT="$(systemctl list-unit-files --type=service --no-legend 'php*-fpm.service' 2>/dev/null | awk 'NR==1 {print $1}')"
if [[ -n "$FPM_UNIT" ]]; then systemctl enable --now "$FPM_UNIT" || true; fi
if command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then systemctl enable --now php-fpm 2>/dev/null || true; fi
PHP_FPM=""
for _ in $(seq 1 15); do
  PHP_FPM="$(find /run/php /var/run/php /run/php-fpm /var/run/php-fpm -maxdepth 2 -type s \( -name 'php*-fpm.sock' -o -name '*.sock' \) 2>/dev/null | sort -V | tail -1 || true)"
  [[ -n "$PHP_FPM" ]] && break
  sleep 1
done
[[ -n "$PHP_FPM" ]] || { echo '[ERROR] Socket PHP-FPM tidak ditemukan. Periksa layanan dan konfigurasi pool.' >&2; exit 2; }

[[ -f "$SOURCE_DIR/public/index.php" && -f "$SOURCE_DIR/agent/agent.py" && -f "$SOURCE_DIR/systemd/openhost-agent.service" ]] || {
  echo '[ERROR] Source tidak lengkap. Gunakan bootstrap.sh atau ekstrak seluruh arsip repository.' >&2; exit 2;
}
php -l "$SOURCE_DIR/public/index.php" || { echo '[ERROR] Sintaks PHP tidak valid.' >&2; exit 3; }
python3 - "$SOURCE_DIR/agent/agent.py" <<'PY'
import sys
compile(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1], "exec")
print("Python agent syntax OK")
PY

mkdir -p "$BACKUP_DIR" "$APP_DIR" "$CONF_DIR" /var/www/openhost-sites /etc/nginx/conf.d /var/log/nginx
if [[ -d "$APP_DIR" ]]; then cp -a "$APP_DIR" "$BACKUP_DIR/app-before-update" 2>/dev/null || true; fi
if [[ -f /etc/nginx/conf.d/openhost-panel.conf ]]; then cp -a /etc/nginx/conf.d/openhost-panel.conf "$BACKUP_DIR/"; fi

# Copy code only. Preserve credentials, site data and the agent bearer token on upgrades.
mkdir -p "$APP_DIR/public" "$APP_DIR/agent" "$APP_DIR/data"
install -m 0644 "$SOURCE_DIR/public/index.php" "$APP_DIR/public/index.php"
install -m 0755 "$SOURCE_DIR/agent/agent.py" "$APP_DIR/agent/agent.py"
install -m 0644 "$SOURCE_DIR/systemd/openhost-agent.service" /etc/systemd/system/openhost-agent.service
chmod 755 "$APP_DIR" "$APP_DIR/public" "$APP_DIR/agent"
chmod 750 "$APP_DIR/data"

PHP_USER="$(ps -eo user=,comm= | awk '$2 ~ /^php-fpm/ && $1 != "root" {print $1; exit}')"
if [[ -z "$PHP_USER" ]]; then
  if id www-data >/dev/null 2>&1; then PHP_USER=www-data
  elif id apache >/dev/null 2>&1; then PHP_USER=apache
  elif id nginx >/dev/null 2>&1; then PHP_USER=nginx
  else echo '[ERROR] Tidak dapat mendeteksi pengguna PHP-FPM.' >&2; exit 2; fi
fi
chown root:root "$APP_DIR" "$APP_DIR/public" "$APP_DIR/agent"
chown "$PHP_USER:$PHP_USER" "$APP_DIR/data"
if [[ ! -s "$APP_DIR/data/admin.json" ]]; then
  HASH="$(php -r 'echo password_hash("admin", PASSWORD_DEFAULT);')"
  printf '{"username":"admin","hash":"%s","must_change":true}\n' "$HASH" > "$APP_DIR/data/admin.json"
fi
chown "$PHP_USER:$PHP_USER" "$APP_DIR/data/admin.json"
chmod 640 "$APP_DIR/data/admin.json"
if [[ ! -s "$CONF_DIR/agent.token" ]]; then
  (umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > "$CONF_DIR/agent.token")
fi
chown "root:$PHP_USER" "$CONF_DIR/agent.token"
chmod 640 "$CONF_DIR/agent.token"

cat > /etc/nginx/conf.d/openhost-panel.conf <<NGINX
server {
    listen 8888;
    listen [::]:8888;
    server_name _;
    root $APP_DIR/public;
    index index.php;
    server_tokens off;
    client_max_body_size 20m;
    location / { try_files \$uri \$uri/ /index.php?\$query_string; }
    location ~ \.php$ {
        try_files \$uri =404;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME \$document_root\$fastcgi_script_name;
        fastcgi_pass unix:$PHP_FPM;
    }
    location ~ /\. { deny all; }
    location ~* \.(bak|conf|ini|log|sql|sh)$ { deny all; }
}
NGINX
# This panel listens on 8888; leave the distribution's default virtual host untouched.
nginx -t || { echo "[ERROR] Konfigurasi Nginx tidak valid. Backup: $BACKUP_DIR" >&2; exit 3; }
systemctl daemon-reload
systemctl enable openhost-agent
if systemctl is-active --quiet openhost-agent; then systemctl restart openhost-agent; else systemctl start openhost-agent; fi
systemctl enable nginx
systemctl restart nginx

cat > /usr/local/sbin/openhost-update <<'UPDATE'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Jalankan: sudo openhost-update' >&2; exit 1; }
command -v curl >/dev/null || { echo 'curl tidak ditemukan.' >&2; exit 2; }
command -v tar >/dev/null || { echo 'tar tidak ditemukan.' >&2; exit 2; }
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL --retry 3 https://github.com/FerdanaRizky/pdhost-tes/archive/refs/heads/main.tar.gz -o "$TMP/source.tar.gz"
tar -xzf "$TMP/source.tar.gz" -C "$TMP"
DIR="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
[[ -n "$DIR" && -f "$DIR/install.sh" ]] || { echo 'Arsip source OpenHost tidak valid.' >&2; exit 3; }
bash "$DIR/install.sh"
UPDATE
chmod 750 /usr/local/sbin/openhost-update

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -n "$IP" ]] || IP=IP_SERVER
cat <<INFO

OpenHost Panel v0.7.0 terpasang.
URL: http://$IP:8888
Username: admin
Password awal: admin (wajib diganti saat login pertama)
Website root: /var/www/openhost-sites/DOMAIN/public
Agent: 127.0.0.1:8787 (hanya loopback)
Update setelah versi baru masuk GitHub: sudo openhost-update
Backup aplikasi: $BACKUP_DIR

Peringatan: aktifkan HTTPS dan batasi akses firewall sebelum penggunaan produksi.
INFO
