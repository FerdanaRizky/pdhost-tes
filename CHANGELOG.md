# Changelog

## v0.7.0
- Added SSL Certificates page to request Let's Encrypt certificates for OpenHost-managed websites.
- Added authenticated agent endpoints for certificate status and issuance.
- Configures HTTPS with HTTP redirect, TLS 1.2/1.3, webroot ACME challenge handling, Nginx validation and rollback, and renewal deploy hook.
- Installer attempts to install Certbot; added SSL configuration unit tests.

## v0.6.0
- Added explicit-name confirmation before removing OpenHost-managed databases and their dedicated local users.
- Added compressed database backup creation, backup history listing, and root-only backup file permissions.
- Added tests for deletion guards and backup utility prerequisites.
- Fixed Database Manager create requests to send a JSON object to the agent.

## v0.5.0
- Added Database Manager creation form and managed database listing.
- Creates a database with a random name and a dedicated local account scoped to that database.
- Shows generated credentials once after creation; the password is not stored by the panel.
- Added unit tests for database creation and listing; CI validates the database grant scope.

## v0.4.0
- Added Database Manager navigation and status page.
- Displays MariaDB/MySQL client detection, systemd service status, and local socket response using the authenticated agent.
- Database changes are intentionally disabled in this release until provisioning and least-privilege integration tests are complete.

## v0.3.0
- Added File Manager for active OpenHost websites: list folders/files, read and save UTF-8 text files, create files and folders, and delete files or empty folders.
- Agent enforces a per-website public document-root boundary, rejects path traversal and symbolic links, and caps editor reads/writes at 1 MiB.
- Added automated security-boundary tests for path traversal, symlinks, file CRUD, and domain validation.
- CI now runs the File Manager tests in addition to PHP, Python and shell syntax checks.

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
Database manager, SSL automation, multiple PHP versions, cron, backup/restore, firewall UI, Docker manager, role management, signed updates, and end-to-end tests on supported distributions.
