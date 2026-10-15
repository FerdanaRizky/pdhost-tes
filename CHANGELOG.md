# Changelog

## v0.2.0
- Dashboard with live OS, CPU load estimate, memory, disk and uptime metrics.
- Website Manager creates validated Nginx virtual hosts and PHP-FPM site roots.
- Nginx configuration is tested before reload; failed changes are rolled back.
- Website disable action preserves website files.
- Agent API is loopback-only, bearer-token authenticated, and has no arbitrary command endpoint.
- Installer preserves admin hash and data directory on upgrades.
- Added bootstrap installer and openhost-update command.
- Installer no longer disables the default Nginx website.

## Planned
Database manager, file manager, SSL automation, multiple PHP versions, cron, backup/restore, firewall UI, Docker manager, role management, signed updates, and automated tests on supported distributions.
