# OpenHost Panel

OpenHost Panel is an independent Linux hosting control panel built with a PHP web interface and a separate allow-listed Python agent. It is not affiliated with aaPanel.

## Current release: v0.2.0

- Responsive dark dashboard with OS, load average, memory, disk and uptime metrics.
- Website Manager for validated domains, Nginx virtual hosts and PHP-FPM.
- Nginx config validation before reload; the agent attempts rollback when an operation fails.
- Disabling a website preserves its document root and files.
- Agent listens only on loopback and exposes fixed API routes rather than arbitrary shell execution.
- Installer backups app code and preserves the admin hash, data directory and agent token during upgrades.
- Remote bootstrap and in-place updater command.

## One-command installation

Review the source before production use. Install on a disposable test server first.

```bash
curl -fsSL https://raw.githubusercontent.com/FerdanaRizky/pdhost-tes/main/bootstrap.sh | sudo bash
```

After installation, open `http://SERVER_IP:8888`. The bootstrap installs packages and creates the panel service. Configure HTTPS and restrict access with a firewall before exposing it publicly.

## Default login

- Username: `admin`
- Temporary password: `admin`
- The first login requires a new password of at least 12 characters.

## Updating without downloading and extracting manually

After new code is committed to the `main` branch, run this on the installed server:

```bash
sudo openhost-update
```

The updater downloads the latest repository archive and runs the installer. It replaces application source code while preserving `/opt/openhost-panel/data/admin.json`, website files under `/var/www/openhost-sites`, and the agent token in `/etc/openhost-panel/agent.token`. It creates a dated backup of the application before copying new code.

## Current limitations

The installer currently targets Debian/Ubuntu and RHEL-family systems with systemd, Nginx, Python 3 and PHP-FPM. This does not guarantee support for every Linux distribution or release. The current Website Manager creates and disables HTTP virtual hosts; automated TLS issuance, databases, File Manager, cron UI, backup management, Docker management, and multi-user roles are not implemented yet.

The privileged agent runs as root to change web-server configuration and reload Nginx. It is designed with fixed routes, loopback binding, bearer-token authentication and systemd hardening, but the project is early-stage and has not been audited. Use on a test VM until the installer and server operations are validated on your specific distribution.
