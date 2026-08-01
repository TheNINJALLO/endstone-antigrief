# Security Policy

## Supported version

Security fixes are applied to the latest release.

## Reporting

Do not publish secrets, private player data, live server addresses, database files, or exploitable details in a public issue. Open a private GitHub security advisory for the repository and include the affected version, reproduction steps, impact, logs with secrets removed, and any proposed mitigation.

## WebUI deployment

Change the default shared secret, restrict the port to trusted networks, prefer TLS through a reverse proxy, and never publish URLs containing the secret query parameter.
