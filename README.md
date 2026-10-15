# OpenHost Panel

Independent Linux hosting control panel project. PHP web interface with a separate, allow-listed Python agent.

## Current target: v0.2.0
- Dashboard and login foundation
- Website Manager for Nginx virtual hosts and PHP-FPM
- Domain validation, config test before reload, and rollback when validation fails
- Disable website config without deleting website files
- Installer targets Debian/Ubuntu and RHEL-family systems with systemd

## Installation
Run the remote installer only after reviewing the script:

```bash
curl -fsSL https://raw.githubusercontent.com/FerdanaRizky/pdhost-tes/main/install.sh -o install.sh
less install.sh
sudo bash install.sh
```

The repository is being initialized file-by-file. Until the full source tree is committed, this README is informational and the installer is not yet available for remote installation.

Default credentials (first-login temporary credential): `admin` / `admin`. Change it immediately; the panel requires a new password on first login.

This is an early-stage project. Do not expose it to the public internet before configuring HTTPS, firewall restrictions, and testing on a disposable server. Full support for all Linux distributions is not guaranteed.