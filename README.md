# e-Boekhouden.nl plugin for Hermes Agent

Connect Hermes Agent to selected e-Boekhouden.nl REST API operations through a native Python plugin. Inspect local dry-run previews before you send supported write requests.

This independent community project is not an official e-Boekhouden.nl product or an endorsed Hermes Agent integration. Live-account interoperability has not been verified.

## What it provides

- `eboekhouden_status`: inspect local configuration or request an API session check.
- `eboekhouden_api`: read supported endpoints and preview supported writes.
- Profile-scoped credentials, profile-local session caching, a fixed HTTPS API origin, and blocked HTTP redirects.
- Explicit write confirmation and an additional acknowledgement for deletion.
- Offline regression tests with synthetic data and blocked network access.

## Requirements

Use a compatible Hermes Agent installation with the native plugin interface and these Python helpers:

- `agent.secret_scope.current_secret_scope` and `agent.secret_scope.get_secret`
- `hermes_constants.get_hermes_home`
- `tools.registry.tool_result` and `tools.registry.tool_error`

An active Hermes profile secret scope is required for configuration inspection and live requests. A process environment variable alone is not sufficient. Older Hermes versions without these helpers are not supported by this candidate. The plugin adds no third-party Python dependency; Hermes supplies its own runtime dependencies.

For live requests, you also need an e-Boekhouden.nl account with REST API access and an API token. Account permissions still apply.

## Install

The public source is available at [MarketingBoer/hermes-eboekhouden-plugin](https://github.com/MarketingBoer/hermes-eboekhouden-plugin). Review the source and compatibility requirements before installation.

### Local directory

1. Identify the active profile's Hermes data directory.
2. Create its `plugins/eboekhouden/` directory.
3. Copy the released `plugin.yaml` and `__init__.py` into that directory.
4. Use the native plugin tools to inspect the installation:

```sh
hermes plugins list
hermes plugins enable eboekhouden
hermes plugins doctor eboekhouden --ci
```

These commands are documented Hermes operations. They do not prove remote installation, activation, or live-account interoperability. Plugin Doctor imports Python with your user permissions; it is not an operating-system sandbox. Hermes CLI startup can perform its own maintenance. Inspect your runtime before using an isolated HOME as a test boundary.

### GitHub installation

Hermes documents this installation route. End-to-end installation through the Hermes CLI has not been verified for this release:

```sh
hermes plugins install MarketingBoer/hermes-eboekhouden-plugin --no-enable
hermes plugins list
hermes plugins enable eboekhouden
```

Restart the relevant Hermes session when your installation requires it.

## Configure credentials

Configure the token in the active profile's Hermes-managed secrets. Hermes loads profile `.env` values into its secret scope. Do not put a token in a chat, tool argument, repository, screenshot, or bug report.

Use the following names. The values below are placeholders, not working credentials:

```dotenv
EBOEKHOUDEN_API_TOKEN=REPLACE_WITH_YOUR_API_TOKEN
EBOEKHOUDEN_SOURCE=Hermes
```

Protect the profile secret file with owner-only permissions. Keep it outside the plugin repository. This plugin does not read a separate `eboekhouden.env` file or fall back to process-global credentials.

`EBOEKHOUDEN_ACCESS_TOKEN` is a legacy alias within the active secret scope. `EBOEKHOUDEN_API_TOKEN` takes precedence. The source defaults to `Hermes` and must match `[\w_ ]{1,10}`. The API origin is fixed to `https://api.e-boekhouden.nl`; an optional `EBOEKHOUDEN_BASE_URL` must equal that origin after trailing slashes are removed.

If the secret scope is missing, credential access fails closed. If the scoped token is missing, a live request is refused. A dry-run write can still work without credentials.

Sessions use `POST /v1/session`. The session cache is stored at `cache/eboekhouden-session.json` inside the active Hermes profile home. Its fingerprint includes that home, the API token, source, and API origin. Cache files use mode 0600 on POSIX systems. Keep the home and cache directory trusted; this does not protect against another process with the same operating-system privileges.

## Examples

Each example below is a tool argument object, not a shell command.

### Inspect local configuration

Call `eboekhouden_status`:

```json
{"action":"config"}
```

This action does not contact the accounting API. `ok: true` means inspection completed; inspect the individual configuration flags. The output does not disclose the token or profile path.

`action: "session"` authenticates or reuses a session. `action: "administrations"` reads linked administrations. These actions can contact the API.

### Read relations

Call `eboekhouden_api`:

```json
{"method":"GET","path":"/v1/relation","query":{"limit":5}}
```

GET requests execute immediately and can return private accounting data. Keep their output private. The plugin does not automatically paginate.

### Preview a write

```json
{"method":"POST","path":"/v1/relation","body":{"name":"Synthetic example"}}
```

The result is a local dry-run. It does not authenticate, verify remote permissions, or validate all server-side business rules. The preview includes the supplied body and query.

### Send a supported write

Add the JSON boolean `"confirm": true` only after you approve the exact request. Strings such as `"true"` and numbers do not confirm a write. This argument is a technical gate, not proof of human authorization.

A DELETE also requires `acknowledge` to equal `DELETE <normalized-path>`. For example, deleting `/v1/product/123` requires `"acknowledge":"DELETE /v1/product/123"`. Deletion has real external effects; the plugin has no rollback.

## Supported operations

| Method | Paths |
| --- | --- |
| GET collections | `/v1/administration`, `/v1/administration/linked`, `/v1/costcenter`, `/v1/emailtemplate`, `/v1/invoice`, `/v1/invoicetemplate`, `/v1/ledger`, `/v1/ledger/balances`, `/v1/member`, `/v1/mutation`, `/v1/mutation/invoice/outstanding`, `/v1/product`, `/v1/product/groups`, `/v1/relation`, `/v1/unit` |
| GET numeric ID | costcenter, invoice, ledger, member, mutation, product, relation; also `/v1/ledger/{id}/balance` |
| POST collections | costcenter, invoice, ledger, member, product, relation |
| PATCH numeric ID | costcenter, member, product, relation |
| DELETE numeric ID | costcenter, product |

Collection and numeric-ID names in the table use the `/v1/` prefix. Dutch and English aliases include `factuur`, `facturen`, `invoices`, `relatie`, `relaties`, `relations`, `artikel`, `artikelen`, `producten`, and `products`.

Put filters in `query`, not in `path`. The plugin normalizes paths and removes their query and fragment components. Query values can include lists and filters such as `date[gte]`.

## Safety boundaries

- Financial booking creation through `POST /v1/mutation` is blocked.
- Existing-ledger PATCH requests are blocked.
- Invoice creation fields `mutation` and `directDebit` are blocked, including case variants and empty values.
- Invoice `email` remains allowed. A confirmed invoice request can send email immediately.
- Relation and member writes can store bank and mandate details. This plugin does not prohibit all financial fields.
- Successful responses and local previews can contain personal data.
- Writes are not automatically replayed after authentication failures. GET can refresh authentication and retry once.
- A timed-out write can have succeeded remotely. Inspect remote state before you retry it.
- No transaction, rollback, idempotency, duplicate-write detection, automatic pagination, or rate-limit recovery is provided.
- Unknown body fields generally pass through to allowlisted endpoints. API changes can introduce effects not covered by the current guards.

Recurring subscriptions are not exposed through this plugin. Use the e-Boekhouden.nl interface for that workflow.

## Test locally

Use the Python interpreter from a compatible Hermes environment:

```sh
python -B -m unittest discover -s tests -v
```

The suite uses synthetic credentials and blocks socket creation. It checks tool registration, write guards, transport behavior, error redaction, missing secret scopes, conflicting process credentials, and profile cache separation. Offline tests do not prove live-account compatibility or support across all Python and Hermes versions.

## Support and contributions

If the plugin is useful, consider starring the repository. Report reproducible issues and suggest focused improvements through GitHub. Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a report or change. Use the procedure in [SECURITY.md](SECURITY.md) for security reports.

Do not share real accounting exports, tokens, session files, customer records, or personal contact details in public reports.

## References

- [Hermes native plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/)
- [Hermes plugin user guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)
- [Hermes plugin catalog submission](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission)
- [e-Boekhouden.nl REST API specification](https://api.e-boekhouden.nl/openapi/v1.json)

## License

This plugin is released under the [MIT License](LICENSE). Hermes Agent and e-Boekhouden.nl remain separate projects with their own terms. The MIT License does not grant access to an accounting account or API subscription.
