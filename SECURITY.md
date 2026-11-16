# Security

This plugin can send authenticated accounting API requests. A confirmed write can have external effects, including invoice email. Its confirmation argument is not human authentication.

## Report a suspected vulnerability

Do not publish credentials, customer records, or a working exploit with private data in a public issue.

Use GitHub's private vulnerability reporting feature if the repository offers a "Report a vulnerability" option on its Security page. This document does not claim that private reporting is enabled. If it is unavailable, open a public issue that asks for a private reporting channel without including sensitive details. Wait for that channel before sending details.

Provide a minimal synthetic reproduction. State the affected version, expected boundary, observed result, and whether the issue needs valid credentials. Do not send a real API token or session cache.

If a credential was exposed, revoke or rotate it through the appropriate provider. Removing it from the current source does not remove it from history, forks, logs, or caches.

## Boundaries

The plugin uses a fixed HTTPS API origin, refuses redirects, and restricts supported methods and paths. Live credential access requires an active Hermes profile secret scope. Writes default to local dry-runs and require explicit confirmation. Some invoice fields and financial booking operations are blocked.

These controls do not provide a sandbox, rollback, idempotency, full request validation, or protection from another process with the same operating-system privileges. Keep the Hermes home and cache directory trusted.

Offline tests use synthetic data. Live-account interoperability and broad runtime compatibility have not been verified. No security certification, support lifetime, or response-time guarantee is claimed.
