#!/usr/bin/env python3
"""OpenHost Panel privileged agent: fixed allow-listed operations only."""
import glob, http.server, json, os, platform, re, secrets, shutil, subprocess, tempfile, time
from urllib.parse import urlparse

VERSION = "0.2.0"
TOKEN_FILE = "/etc/openhost-panel/agent.token"
SITE_ROOT = "/var/www/openhost-sites"
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
        elif path=="/api/v1/sites": self.send_json(200,{"ok":True,"sites":list_sites()})
        else: self.send_json(404,{"ok":False,"error":"not_found"})
    def do_POST(self):
        if not self.authorized(): self.send_json(401,{"ok":False,"error":"unauthorized"}); return
        path=urlparse(self.path).path
        if path not in ("/api/v1/sites/create","/api/v1/sites/disable"): self.send_json(404,{"ok":False,"error":"not_found"}); return
        try:
            length=int(self.headers.get("Content-Length","0"))
            if length<1 or length>4096: raise ValueError("Ukuran request tidak valid.")
            data=json.loads(self.rfile.read(length).decode("utf-8"))
            domain=data.get("domain","") if isinstance(data,dict) else ""
            result=create_site(domain) if path.endswith("/create") else disable_site(domain)
            self.send_json(200 if result.get("ok") else 400,result)
        except Exception as exc: self.send_json(400,{"ok":False,"error":"Request tidak valid: "+str(exc)[:160]})

if __name__=="__main__":
    os.makedirs(CONF_DIR,exist_ok=True)
    http.server.ThreadingHTTPServer(("127.0.0.1",8787),Handler).serve_forever()
