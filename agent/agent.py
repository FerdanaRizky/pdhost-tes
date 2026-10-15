#!/usr/bin/env python3
"""OpenHost Panel privileged agent: fixed allow-listed operations only."""
import glob, http.server, json, os, platform, re, secrets, shutil, subprocess, tempfile, time
from urllib.parse import urlparse, parse_qs

VERSION = "0.7.0"
TOKEN_FILE = "/etc/openhost-panel/agent.token"
SITE_ROOT = "/var/www/openhost-sites"
FILE_MAX_BYTES = 1024 * 1024
CONF_DIR = "/etc/nginx/conf.d"
STARTED = time.monotonic()
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))+$")

def read_token():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f: return f.read().strip()
    except OSError: return ""

def valid_domain(value):
    if not isinstance(value, str): return False
    value = value.strip().lower().rstrip(".")
    if not DOMAIN_RE.fullmatch(value): return False
    return not value.endswith((".localhost", ".local", ".test", ".invalid", ".example"))

def human_bytes(value):
    value = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB": return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"

MONITORED_SERVICES = {'nginx': 'nginx', 'mariadb': 'mariadb', 'mysql': 'mysql', 'redis': 'redis-server', 'ssh': 'ssh'}

def list_services():
    systemctl = shutil.which('systemctl')
    if not systemctl:
        return {'ok': False, 'error': 'systemctl tidak tersedia pada sistem ini.'}
    items = []
    for key, unit in MONITORED_SERVICES.items():
        probe = subprocess.run([systemctl, 'show', unit, '--property=LoadState', '--value'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5, check=False)
        if probe.returncode != 0 or probe.stdout.strip() in ('', 'not-found'):
            items.append({'id': key, 'unit': unit, 'installed': False, 'state': 'not-installed'})
            continue
        state = subprocess.run([systemctl, 'is-active', unit], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5, check=False).stdout.strip() or 'unknown'
        enabled = subprocess.run([systemctl, 'is-enabled', unit], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5, check=False).stdout.strip() or 'unknown'
        items.append({'id': key, 'unit': unit, 'installed': True, 'state': state, 'enabled': enabled})
    return {'ok': True, 'services': items}

def get_metrics():
    mem_total = mem_available = 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    value = int(rest.strip().split()[0]) * 1024
                    if key == "MemTotal": mem_total = value
                    else: mem_available = value
    except (OSError, ValueError, IndexError): pass
    disk = shutil.disk_usage("/")
    try:
        with open("/proc/loadavg", encoding="utf-8") as f: load = float(f.read().split()[0])
    except (OSError, ValueError, IndexError): load = 0.0
    try:
        with open("/proc/uptime", encoding="utf-8") as f: uptime = int(float(f.read().split()[0]))
    except (OSError, ValueError, IndexError): uptime = int(time.monotonic() - STARTED)
    try:
        release = {}
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip().split("=", 1); release[k] = v.strip('"')
        os_name = release.get("PRETTY_NAME", platform.system())
    except OSError: os_name = platform.system()
    used = max(0, mem_total - mem_available)
    cpus = max(1, os.cpu_count() or 1)
    return {"ok":True,"version":VERSION,"hostname":platform.node(),"os":os_name,"kernel":platform.release(),
      "architecture":platform.machine(),"load_1m":round(load,2),
      "cpu_load_percent_estimate":round(min(100.0,load/cpus*100),1),
      "memory_total_human":human_bytes(mem_total),"memory_used_human":human_bytes(used),
      "memory_percent":round(used/mem_total*100,1) if mem_total else 0,
      "disk_total_human":human_bytes(disk.total),"disk_used_human":human_bytes(disk.used),
      "disk_percent":round(disk.used/disk.total*100,1) if disk.total else 0,
      "uptime_seconds":uptime,
      "uptime_human":f"{uptime//86400}d {(uptime%86400)//3600}h {(uptime%3600)//60}m"}

def run_checked(args, timeout=20):
    return subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout,check=False)

def find_php_fpm_socket():
    candidates=[]
    for pattern in ("/run/php/php*-fpm.sock","/var/run/php/php*-fpm.sock","/run/php-fpm/*.sock","/var/run/php-fpm/*.sock"):
        candidates.extend(glob.glob(pattern))
    candidates=sorted(set(p for p in candidates if os.path.exists(p)))
    return candidates[-1] if candidates else ""

def list_sites():
    os.makedirs(CONF_DIR,exist_ok=True); out=[]
    for path in sorted(glob.glob(os.path.join(CONF_DIR,"oh-*.conf"))):
        domain=os.path.basename(path)[3:-5]
        if valid_domain(domain): out.append({"domain":domain,"root":os.path.join(SITE_ROOT,domain,"public"),"enabled":True})
    for path in sorted(glob.glob(os.path.join(CONF_DIR,"oh-*.conf.disabled"))):
        domain=os.path.basename(path)[3:-14]
        if valid_domain(domain): out.append({"domain":domain,"root":os.path.join(SITE_ROOT,domain,"public"),"enabled":False})
    return out

def site_config(domain, root, socket):
    return ("# Managed by OpenHost Panel. Do not edit manually.\nserver {\n"
      "    listen 80;\n    listen [::]:80;\n"
      f"    server_name {domain};\n    root {root};\n"
      "    index index.php index.html;\n    server_tokens off;\n"
      f"    access_log /var/log/nginx/{domain}.access.log;\n    error_log /var/log/nginx/{domain}.error.log;\n"
      "    location ^~ /.well-known/acme-challenge/ { try_files $uri =404; }\n"
      "    location / { try_files $uri $uri/ /index.php?$query_string; }\n"
      "    location ~ \\.php$ {\n        try_files $uri =404;\n        include fastcgi_params;\n"
      "        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;\n"
      f"        fastcgi_pass unix:{socket};\n    }}\n"
      "    location ~ /\\. { deny all; }\n}\n")

def site_config_ssl(domain, root, socket):
    cert = f'/etc/letsencrypt/live/{domain}/fullchain.pem'
    key = f'/etc/letsencrypt/live/{domain}/privkey.pem'
    return ("# Managed by OpenHost Panel. Do not edit manually.\n"
      "server {\n    listen 80;\n    listen [::]:80;\n"
      f"    server_name {domain};\n    root {root};\n"
      "    location ^~ /.well-known/acme-challenge/ { try_files $uri =404; }\n"
      "    location / { return 301 https://$host$request_uri; }\n}\n"
      "server {\n    listen 443 ssl;\n    listen [::]:443 ssl;\n"
      f"    server_name {domain};\n    root {root};\n"
      f"    ssl_certificate {cert};\n    ssl_certificate_key {key};\n"
      "    ssl_protocols TLSv1.2 TLSv1.3;\n    ssl_session_cache shared:OpenHostSSL:10m;\n"
      "    index index.php index.html;\n    server_tokens off;\n"
      f"    access_log /var/log/nginx/{domain}.access.log;\n    error_log /var/log/nginx/{domain}.error.log;\n"
      "    location / { try_files $uri $uri/ /index.php?$query_string; }\n"
      "    location ~ \\.php$ {\n        try_files $uri =404;\n        include fastcgi_params;\n"
      "        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;\n"
      f"        fastcgi_pass unix:{socket};\n    }}\n"
      "    location ~ /\\. { deny all; }\n}\n")

def create_site(domain):
    domain=domain.strip().lower().rstrip(".") if isinstance(domain,str) else ""
    if not valid_domain(domain): return {"ok":False,"error":"Domain tidak valid. Gunakan FQDN seperti example.com."}
    os.makedirs(CONF_DIR,exist_ok=True); os.makedirs(SITE_ROOT,exist_ok=True)
    conf=os.path.join(CONF_DIR,"oh-"+domain+".conf")
    if os.path.lexists(conf) or os.path.lexists(conf+".disabled"): return {"ok":False,"error":"Domain sudah terdaftar di OpenHost."}
    socket=find_php_fpm_socket()
    if not socket: return {"ok":False,"error":"Socket PHP-FPM tidak ditemukan. Pastikan PHP-FPM aktif."}
    parent=os.path.join(SITE_ROOT,domain); root=os.path.join(parent,"public")
    if os.path.lexists(parent): return {"ok":False,"error":"Direktori website sudah ada. Periksa sebelum membuat domain ini."}
    temp_conf=None
    try:
        os.makedirs(root,mode=0o755,exist_ok=False)
        with open(os.path.join(root,"index.php"),"x",encoding="utf-8") as f:
            f.write("<?php\nhttp_response_code(200);\necho '<h1>Website berhasil dibuat oleh OpenHost Panel</h1>';\n")
        os.chmod(os.path.join(root,"index.php"),0o644)
        fd,temp_conf=tempfile.mkstemp(prefix=".openhost-",dir=CONF_DIR,text=True)
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            f.write(site_config(domain,root,socket)); f.flush(); os.fsync(f.fileno())
        os.chmod(temp_conf,0o644); os.replace(temp_conf,conf); temp_conf=None
        test=run_checked(["/usr/sbin/nginx","-t"])
        if test.returncode:
            os.unlink(conf)
            return {"ok":False,"error":"Validasi Nginx gagal; konfigurasi dibatalkan. "+test.stdout[-900:]}
        reloaded=run_checked(["/bin/systemctl","reload","nginx"])
        if reloaded.returncode:
            os.unlink(conf)
            return {"ok":False,"error":"Reload Nginx gagal; konfigurasi dibatalkan. "+reloaded.stdout[-700:]}
        return {"ok":True,"domain":domain,"root":root,"message":"Website dibuat. Pastikan DNS mengarah ke IP server."}
    except Exception as exc:
        try:
            if os.path.exists(conf): os.unlink(conf)
            if temp_conf and os.path.exists(temp_conf): os.unlink(temp_conf)
        except OSError: pass
        return {"ok":False,"error":"Gagal membuat website: "+str(exc)[:250]}

def ssl_status(domain):
    domain = domain.strip().lower().rstrip('.') if isinstance(domain, str) else ''
    if not valid_domain(domain): return {'ok': False, 'error': 'Domain tidak valid.'}
    cert = f'/etc/letsencrypt/live/{domain}/fullchain.pem'
    key = f'/etc/letsencrypt/live/{domain}/privkey.pem'
    return {'ok': True, 'domain': domain, 'issued': os.path.isfile(cert) and os.path.isfile(key),
            'certbot_installed': bool(shutil.which('certbot')), 'enabled': os.path.isfile(os.path.join(CONF_DIR, 'oh-' + domain + '.conf')) and os.path.isfile(cert) and os.path.isfile(key)}

def enable_ssl(domain, email):
    domain = domain.strip().lower().rstrip('.') if isinstance(domain, str) else ''
    email = email.strip() if isinstance(email, str) else ''
    if not valid_domain(domain): return {'ok': False, 'error': 'Domain tidak valid.'}
    if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        return {'ok': False, 'error': 'Alamat email untuk notifikasi sertifikat tidak valid.'}
    conf = os.path.join(CONF_DIR, 'oh-' + domain + '.conf')
    root = os.path.join(SITE_ROOT, domain, 'public')
    if not os.path.isfile(conf) or not os.path.isdir(root) or os.path.islink(root):
        return {'ok': False, 'error': 'Website OpenHost aktif tidak ditemukan. Buat atau aktifkan website terlebih dahulu.'}
    certbot = shutil.which('certbot')
    if not certbot: return {'ok': False, 'error': 'Certbot belum terpasang. Instal paket certbot lalu coba lagi.'}
    cert = f'/etc/letsencrypt/live/{domain}/fullchain.pem'
    key = f'/etc/letsencrypt/live/{domain}/privkey.pem'
    if not (os.path.isfile(cert) and os.path.isfile(key)):
        result = subprocess.run([certbot, 'certonly', '--webroot', '-w', root, '-d', domain,
            '--email', email, '--agree-tos', '--non-interactive', '--keep-until-expiring', '--no-eff-email'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300, check=False)
        if result.returncode:
            return {'ok': False, 'error': 'Penerbitan sertifikat gagal. Pastikan DNS A/AAAA mengarah ke server, port 80 dapat diakses, dan email benar. ' + result.stdout[-700:]}
    socket = find_php_fpm_socket()
    if not socket: return {'ok': False, 'error': 'Sertifikat terbit, tetapi socket PHP-FPM tidak ditemukan; konfigurasi website belum diubah.'}
    old = open(conf, encoding='utf-8').read()
    fd, temp_conf = tempfile.mkstemp(prefix='.openhost-ssl-', dir=CONF_DIR, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(site_config_ssl(domain, root, socket))
            stream.flush(); os.fsync(stream.fileno())
        os.chmod(temp_conf, 0o644)
        os.replace(temp_conf, conf); temp_conf = ''
        test = run_checked(['/usr/sbin/nginx', '-t'])
        if test.returncode:
            fd, rollback = tempfile.mkstemp(prefix='.openhost-rollback-', dir=CONF_DIR, text=True)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream: stream.write(old)
            os.chmod(rollback, 0o644); os.replace(rollback, conf)
            return {'ok': False, 'error': 'Validasi Nginx gagal; konfigurasi HTTP dikembalikan. ' + test.stdout[-600:]}
        reload = run_checked(['/bin/systemctl', 'reload', 'nginx'])
        if reload.returncode:
            fd, rollback = tempfile.mkstemp(prefix='.openhost-rollback-', dir=CONF_DIR, text=True)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream: stream.write(old)
            os.chmod(rollback, 0o644); os.replace(rollback, conf)
            run_checked(['/bin/systemctl', 'reload', 'nginx'])
            return {'ok': False, 'error': 'Reload Nginx gagal; konfigurasi HTTP dikembalikan.'}
        hook_dir = '/etc/letsencrypt/renewal-hooks/deploy'
        os.makedirs(hook_dir, mode=0o755, exist_ok=True)
        hook = os.path.join(hook_dir, 'openhost-nginx-reload')
        hook_content = '#!/bin/sh\n/usr/bin/systemctl reload nginx\n'
        if not os.path.exists(hook):
            with open(hook, 'x', encoding='utf-8') as stream: stream.write(hook_content)
            os.chmod(hook, 0o755)
        return {'ok': True, 'domain': domain, 'message': 'SSL aktif. Certbot dapat memperpanjang sertifikat; deploy hook akan reload Nginx.'}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'ok': False, 'error': 'Gagal mengaktifkan SSL: ' + str(exc)[:250]}
    finally:
        if temp_conf and os.path.exists(temp_conf):
            try: os.unlink(temp_conf)
            except OSError: pass

def disable_site(domain):
    domain=domain.strip().lower().rstrip(".") if isinstance(domain,str) else ""
    if not valid_domain(domain): return {"ok":False,"error":"Domain tidak valid."}
    conf=os.path.join(CONF_DIR,"oh-"+domain+".conf"); disabled=conf+".disabled"
    if not os.path.isfile(conf): return {"ok":False,"error":"Website aktif tidak ditemukan."}
    try:
        os.replace(conf,disabled)
        test=run_checked(["/usr/sbin/nginx","-t"])
        if test.returncode:
            os.replace(disabled,conf)
            return {"ok":False,"error":"Validasi gagal; perubahan dikembalikan. "+test.stdout[-700:]}
        reloaded=run_checked(["/bin/systemctl","reload","nginx"])
        if reloaded.returncode:
            os.replace(disabled,conf)
            return {"ok":False,"error":"Reload gagal; perubahan dikembalikan. "+reloaded.stdout[-700:]}
        return {"ok":True,"domain":domain,"message":"Virtual host dinonaktifkan; file tetap disimpan."}
    except Exception as exc:
        if os.path.exists(disabled) and not os.path.exists(conf):
            try: os.replace(disabled,conf)
            except OSError: pass
        return {"ok":False,"error":"Gagal menonaktifkan website: "+str(exc)[:200]}


def resolve_site_path(domain, rel_path=""):
    """Resolve a relative path strictly inside one OpenHost site's public directory."""
    domain = domain.strip().lower().rstrip(".") if isinstance(domain, str) else ""
    if not valid_domain(domain):
        raise ValueError("Domain tidak valid.")
    base = os.path.join(SITE_ROOT, domain, "public")
    if not os.path.isdir(base) or os.path.islink(base):
        raise ValueError("Document root website tidak ditemukan.")
    base_real = os.path.realpath(base)
    if os.path.commonpath([os.path.realpath(SITE_ROOT), base_real]) != os.path.realpath(SITE_ROOT):
        raise ValueError("Lokasi website tidak diizinkan.")
    if not isinstance(rel_path, str) or "\x00" in rel_path or rel_path.startswith(("/", "\\")):
        raise ValueError("Path tidak valid.")
    parts = [p for p in rel_path.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." or p.startswith(".openhost-") for p in parts):
        raise ValueError("Path traversal tidak diizinkan.")
    current = base
    for part in parts:
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise ValueError("Akses ke symbolic link tidak diizinkan.")
        if os.path.lexists(current):
            resolved = os.path.realpath(current)
            if os.path.commonpath([base_real, resolved]) != base_real:
                raise ValueError("Path keluar dari document root.")
    return base_real, current, "/".join(parts)

def list_site_files(domain, rel_path=""):
    base, target, relative = resolve_site_path(domain, rel_path)
    if not os.path.isdir(target):
        raise ValueError("Path bukan direktori.")
    items = []
    with os.scandir(target) as entries:
        for entry in sorted(entries, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())):
            if entry.is_symlink():
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                is_dir = entry.is_dir(follow_symlinks=False)
                if not is_dir and not entry.is_file(follow_symlinks=False):
                    continue
                items.append({
                    "name": entry.name,
                    "path": "/".join(filter(None, [relative, entry.name])),
                    "directory": is_dir,
                    "size": info.st_size,
                    "modified": int(info.st_mtime),
                })
            except OSError:
                continue
    return {"ok": True, "domain": domain, "path": relative, "items": items}

def read_site_file(domain, rel_path):
    base, target, relative = resolve_site_path(domain, rel_path)
    if not os.path.isfile(target) or os.path.islink(target):
        raise ValueError("File tidak ditemukan atau bukan file biasa.")
    size = os.path.getsize(target)
    if size > FILE_MAX_BYTES:
        raise ValueError("File terlalu besar untuk editor web (maksimum 1 MiB).")
    with open(target, "rb") as f:
        raw = f.read(FILE_MAX_BYTES + 1)
    if b"\x00" in raw:
        raise ValueError("File biner tidak dapat diedit dari editor teks.")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("File bukan teks UTF-8 yang didukung.")
    return {"ok": True, "domain": domain, "path": relative, "content": content, "size": size}

def write_site_file(domain, rel_path, content, create_only=False):
    if not isinstance(content, str):
        raise ValueError("Konten file tidak valid.")
    encoded = content.encode("utf-8")
    if len(encoded) > FILE_MAX_BYTES:
        raise ValueError("Konten terlalu besar (maksimum 1 MiB).")
    base, target, relative = resolve_site_path(domain, rel_path)
    if not relative or os.path.isdir(target):
        raise ValueError("Pilih nama file yang valid.")
    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        raise ValueError("Direktori tujuan tidak ditemukan.")
    if os.path.islink(target):
        raise ValueError("Symbolic link tidak dapat ditulis.")
    if create_only and os.path.lexists(target):
        raise ValueError("File sudah ada.")
    if os.path.lexists(target) and not os.path.isfile(target):
        raise ValueError("Tujuan bukan file biasa.")
    fd, temp_path = tempfile.mkstemp(prefix=".openhost-tmp-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp_path, 0o644)
        if os.path.islink(target):
            raise ValueError("Symbolic link tidak dapat ditulis.")
        os.replace(temp_path, target)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return {"ok": True, "domain": domain, "path": relative, "message": "File berhasil disimpan."}

def create_site_directory(domain, rel_path):
    base, target, relative = resolve_site_path(domain, rel_path)
    if not relative:
        raise ValueError("Nama folder tidak boleh kosong.")
    if os.path.lexists(target):
        raise ValueError("Path sudah ada.")
    os.mkdir(target, 0o755)
    return {"ok": True, "path": relative, "message": "Folder berhasil dibuat."}

def delete_site_entry(domain, rel_path):
    base, target, relative = resolve_site_path(domain, rel_path)
    if not relative:
        raise ValueError("Document root tidak dapat dihapus.")
    if not os.path.lexists(target) or os.path.islink(target):
        raise ValueError("File atau folder tidak ditemukan.")
    if os.path.isdir(target):
        os.rmdir(target)  # Empty directories only; never recursively erase a tree.
    elif os.path.isfile(target):
        os.unlink(target)
    else:
        raise ValueError("Jenis file tidak didukung.")
    return {"ok": True, "path": relative, "message": "Item berhasil dihapus."}


def database_status():
    binary = shutil.which("mariadb") or shutil.which("mysql")
    admin = shutil.which("mariadb-admin") or shutil.which("mysqladmin")
    service_state = "unknown"
    systemctl = shutil.which("systemctl")
    if systemctl:
        for service in ("mariadb", "mysql", "mysqld"):
            result = subprocess.run([systemctl, "is-active", service], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5, check=False)
            if result.returncode == 0:
                service_state = "active"
                break
            if result.stdout.strip() == "inactive":
                service_state = "inactive"
    ping = False
    if admin:
        result = subprocess.run([admin, "--protocol=socket", "ping"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5, check=False)
        ping = result.returncode == 0
    return {"ok": True, "installed": bool(binary or admin), "service_state": service_state,
            "responding": ping, "engine": "MariaDB/MySQL" if binary or admin else None}

DB_PREFIX = "oh_"

def mysql_query(sql, timeout=20):
    binary = shutil.which("mariadb") or shutil.which("mysql")
    if not binary:
        raise RuntimeError("Klien MariaDB/MySQL belum terpasang.")
    result = subprocess.run([binary, "--protocol=socket", "--batch", "--skip-column-names"],
        input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or "Operasi database gagal.")[-500:])
    return result.stdout

def list_databases():
    if not (shutil.which("mariadb") or shutil.which("mysql")):
        return {"ok": True, "available": False, "databases": [],
                "message": "Klien MariaDB/MySQL belum terpasang."}
    try:
        output = mysql_query("SHOW DATABASES;")
        names = sorted({line.strip() for line in output.splitlines()
                        if re.fullmatch(r"oh_[a-f0-9]{10}", line.strip())})
        return {"ok": True, "available": True, "databases": [{"name": name} for name in names]}
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "available": True, "databases": [], "error": str(exc)[:400]}

def create_database():
    if not (shutil.which("mariadb") or shutil.which("mysql")):
        return {"ok": False, "error": "MariaDB/MySQL belum terpasang. Instal dan aktifkan database server terlebih dahulu."}
    suffix = secrets.token_hex(5)
    database = DB_PREFIX + suffix
    username = "ohu_" + suffix
    password = secrets.token_urlsafe(24)
    # Generated identifiers use a strict hex suffix; password is URL-safe and quote-free.
    sql = (f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
           f"CREATE USER '{username}'@'localhost' IDENTIFIED BY '{password}';\n"
           f"GRANT ALL PRIVILEGES ON `{database}`.* TO '{username}'@'localhost';\n"
           "FLUSH PRIVILEGES;\n")
    try:
        mysql_query(sql, timeout=30)
        return {"ok": True, "database": database, "username": username, "password": password,
                "message": "Database berhasil dibuat. Simpan password sekarang; password tidak akan ditampilkan lagi."}
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        cleanup = (f"DROP USER IF EXISTS '{username}'@'localhost';\n"
                   f"DROP DATABASE IF EXISTS `{database}`;\nFLUSH PRIVILEGES;\n")
        try:
            mysql_query(cleanup, timeout=15)
        except Exception:
            pass
        return {"ok": False, "error": "Gagal membuat database. Pastikan akses socket administratif MariaDB/MySQL tersedia. " + str(exc)[:250]}

def delete_database(database, confirmation):
    # Only databases created by OpenHost are eligible; exact-name confirmation is mandatory.
    if not isinstance(database, str) or not re.fullmatch(r"oh_[a-f0-9]{10}", database):
        return {"ok": False, "error": "Nama database tidak termasuk database yang dikelola OpenHost."}
    if confirmation != database:
        return {"ok": False, "error": "Konfirmasi harus sama persis dengan nama database."}
    suffix = database[3:]
    username = "ohu_" + suffix
    sql = (f"DROP DATABASE IF EXISTS `{database}`;\n"
           f"DROP USER IF EXISTS '{username}'@'localhost';\n"
           "FLUSH PRIVILEGES;\n")
    try:
        mysql_query(sql, timeout=30)
        return {"ok": True, "database": database, "message": "Database dan akun khususnya berhasil dihapus."}
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": "Gagal menghapus database. " + str(exc)[:250]}

BACKUP_ROOT = "/opt/openhost-panel/backups/databases"

def list_database_backups():
    os.makedirs(BACKUP_ROOT, mode=0o700, exist_ok=True)
    items = []
    for name in sorted(os.listdir(BACKUP_ROOT), reverse=True):
        if not re.fullmatch(r"oh_[a-f0-9]{10}_[0-9]{8}T[0-9]{6}Z\.sql(\.gz)?", name):
            continue
        path = os.path.join(BACKUP_ROOT, name)
        if os.path.isfile(path) and not os.path.islink(path):
            stat = os.stat(path, follow_symlinks=False)
            items.append({"name": name, "size": stat.st_size, "modified": int(stat.st_mtime)})
    return {"ok": True, "backups": items}

def backup_database(database):
    if not isinstance(database, str) or not re.fullmatch(r"oh_[a-f0-9]{10}", database):
        return {"ok": False, "error": "Nama database tidak termasuk database yang dikelola OpenHost."}
    dump = shutil.which("mariadb-dump") or shutil.which("mysqldump")
    if not dump:
        return {"ok": False, "error": "Utilitas mariadb-dump/mysqldump belum terpasang."}
    os.makedirs(BACKUP_ROOT, mode=0o700, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = f"{database}_{stamp}.sql.gz"
    target = os.path.join(BACKUP_ROOT, name)
    fd, temp_path = tempfile.mkstemp(prefix=".openhost-backup-", dir=BACKUP_ROOT)
    os.close(fd)
    try:
        import gzip
        with gzip.open(temp_path, "wb", compresslevel=6) as output:
            dump_result = subprocess.run([dump, "--protocol=socket", "--single-transaction", "--quick", "--routines", "--triggers", database],
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.PIPE, timeout=300, check=False)
            output.flush()
        if dump_result.returncode:
            raise RuntimeError((dump_result.stderr.decode("utf-8", "replace") or "Database dump gagal.")[-400:])
        with open(temp_path, "rb") as output:
            os.fsync(output.fileno())
        if os.path.getsize(temp_path) < 20:
            raise RuntimeError("Hasil backup kosong atau tidak valid.")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, target)
        return {"ok": True, "database": database, "backup": name, "size": os.path.getsize(target),
                "message": "Backup berhasil dibuat. File disimpan dengan izin hanya untuk root."}
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": "Backup gagal: " + str(exc)[:350]}
    finally:
        if os.path.exists(temp_path):
            try: os.unlink(temp_path)
            except OSError: pass

class Handler(http.server.BaseHTTPRequestHandler):
    server_version="OpenHostAgent/"+VERSION
    def log_message(self,*args): pass
    def send_json(self,status,data):
        body=json.dumps(data).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body))); self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff"); self.end_headers(); self.wfile.write(body)
    def authorized(self):
        token=read_token()
        return bool(token) and secrets.compare_digest(self.headers.get("Authorization",""),"Bearer "+token)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/api/v1/health": self.send_json(200,{"ok":True,"service":"openhost-agent","version":VERSION}); return
        if not self.authorized(): self.send_json(401,{"ok":False,"error":"unauthorized"}); return
        if path=="/api/v1/metrics": self.send_json(200,get_metrics())
        elif path=="/api/v1/services": self.send_json(200,list_services())
        elif path=="/api/v1/sites": self.send_json(200,{"ok":True,"sites":list_sites()})
        elif path=="/api/v1/databases/status": self.send_json(200,database_status())
        elif path=="/api/v1/databases": self.send_json(200,list_databases())
        elif path=="/api/v1/databases/backups": self.send_json(200,list_database_backups())
        elif path=="/api/v1/ssl/status":
            q=parse_qs(urlparse(self.path).query)
            self.send_json(200,ssl_status(q.get("domain",[""])[0]))
        elif path=="/api/v1/files/list":
            q=parse_qs(urlparse(self.path).query)
            try: self.send_json(200,list_site_files(q.get("domain",[""])[0],q.get("path",[""])[0]))
            except (ValueError,OSError) as exc: self.send_json(400,{"ok":False,"error":str(exc)[:240]})
        elif path=="/api/v1/files/read":
            q=parse_qs(urlparse(self.path).query)
            try: self.send_json(200,read_site_file(q.get("domain",[""])[0],q.get("path",[""])[0]))
            except (ValueError,OSError) as exc: self.send_json(400,{"ok":False,"error":str(exc)[:240]})
        else: self.send_json(404,{"ok":False,"error":"not_found"})
    def do_POST(self):
        if not self.authorized(): self.send_json(401,{"ok":False,"error":"unauthorized"}); return
        path=urlparse(self.path).path
        allowed=("/api/v1/sites/create","/api/v1/sites/disable","/api/v1/files/write","/api/v1/files/create","/api/v1/files/mkdir","/api/v1/files/delete","/api/v1/databases/create","/api/v1/databases/delete","/api/v1/databases/backup","/api/v1/ssl/enable")
        if path not in allowed: self.send_json(404,{"ok":False,"error":"not_found"}); return
        try:
            length=int(self.headers.get("Content-Length","0"))
            if length<1 or length>FILE_MAX_BYTES+8192: raise ValueError("Ukuran request tidak valid.")
            data=json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data,dict): raise ValueError("Request harus berupa objek.")
            domain=data.get("domain","")
            if path=="/api/v1/ssl/enable": result=enable_ssl(data.get("domain",""),data.get("email",""))
            elif path=="/api/v1/databases/create": result=create_database()
            elif path=="/api/v1/databases/delete": result=delete_database(data.get("database",""),data.get("confirmation",""))
            elif path=="/api/v1/databases/backup": result=backup_database(data.get("database",""))
            elif path=="/api/v1/sites/create": result=create_site(domain)
            elif path=="/api/v1/sites/disable": result=disable_site(domain)
            elif path=="/api/v1/files/write": result=write_site_file(domain,data.get("path",""),data.get("content",""))
            elif path=="/api/v1/files/create": result=write_site_file(domain,data.get("path",""),data.get("content",""),create_only=True)
            elif path=="/api/v1/files/mkdir": result=create_site_directory(domain,data.get("path",""))
            else: result=delete_site_entry(domain,data.get("path",""))
            self.send_json(200 if result.get("ok") else 400,result)
        except Exception as exc: self.send_json(400,{"ok":False,"error":"Request tidak valid: "+str(exc)[:240]})

if __name__=="__main__":
    os.makedirs(CONF_DIR,exist_ok=True)
    http.server.ThreadingHTTPServer(("127.0.0.1",8787),Handler).serve_forever()
