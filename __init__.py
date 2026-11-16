"""Hermes user plugin for the e-Boekhouden.nl REST API."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import parse, request

from agent.secret_scope import current_secret_scope, get_secret
from hermes_constants import get_hermes_home
from tools.registry import tool_error, tool_result


BASE_URL = "https://api.e-boekhouden.nl"
SOURCE_DEFAULT = "Hermes"
SESSION_CACHE = "cache/eboekhouden-session.json"

READ_PATHS = {
    "/v1/administration",
    "/v1/administration/linked",
    "/v1/costcenter",
    "/v1/emailtemplate",
    "/v1/invoice",
    "/v1/invoicetemplate",
    "/v1/ledger",
    "/v1/ledger/balances",
    "/v1/member",
    "/v1/mutation",
    "/v1/mutation/invoice/outstanding",
    "/v1/product",
    "/v1/product/groups",
    "/v1/relation",
    "/v1/unit",
}

ID_READ_PATTERNS = (
    re.compile(r"^/v1/costcenter/\d+$"),
    re.compile(r"^/v1/invoice/\d+$"),
    re.compile(r"^/v1/ledger/\d+$"),
    re.compile(r"^/v1/ledger/\d+/balance$"),
    re.compile(r"^/v1/member/\d+$"),
    re.compile(r"^/v1/mutation/\d+$"),
    re.compile(r"^/v1/product/\d+$"),
    re.compile(r"^/v1/relation/\d+$"),
)

# Safety policy: /v1/mutation is deliberately NOT allowlisted.
# It is the API's only financial booking endpoint and covers all seven mutation
# types (incoming/outgoing invoices, incoming/outgoing invoice payments, money
# in/out, and general journal entries). Bookings cannot be PATCHed or DELETEd via
# the API, so a wrong booking is permanent. Re-add this path only on an explicit
# reviewed policy change.
POST_PATHS = {
    "/v1/costcenter",
    "/v1/invoice",
    "/v1/ledger",
    "/v1/member",
    "/v1/product",
    "/v1/relation",
}

PATH_ALIASES = {
    "/v1/artikel": "/v1/product",
    "/v1/artikelen": "/v1/product",
    "/v1/factuur": "/v1/invoice",
    "/v1/facturen": "/v1/invoice",
    "/v1/invoices": "/v1/invoice",
    "/v1/producten": "/v1/product",
    "/v1/products": "/v1/product",
    "/v1/relatie": "/v1/relation",
    "/v1/relaties": "/v1/relation",
    "/v1/relations": "/v1/relation",
}

UNSUPPORTED_PATH_HINTS = (
    (
        re.compile(r"^/v1/(abonnement|abonnementen|subscription|subscriptions)(/|$)"),
        "e-Boekhouden REST API has no subscriptions/abonnementen endpoint. "
        "Plan recurring invoices in the e-Boekhouden UI via "
        "Factureren > Abonnementen > Toevoegen; do not create cron-job workarounds.",
    ),
)

# Safety policy: /v1/ledger/{id} PATCH is deliberately NOT
# allowlisted. Editing an existing ledger account changes the chart of accounts
# that all existing bookings hang off. Creating a new ledger (POST /v1/ledger)
# stays allowed — nothing is booked against it yet.
PATCH_PATTERNS = (
    re.compile(r"^/v1/costcenter/\d+$"),
    re.compile(r"^/v1/member/\d+$"),
    re.compile(r"^/v1/product/\d+$"),
    re.compile(r"^/v1/relation/\d+$"),
)

DELETE_PATTERNS = (
    re.compile(r"^/v1/costcenter/\d+$"),
    re.compile(r"^/v1/product/\d+$"),
)


class EboekhoudenError(Exception):
    pass


def _hermes_home() -> Path:
    return Path(get_hermes_home())


def _cache_path() -> Path:
    return _hermes_home() / SESSION_CACHE


def _scoped_value(name: str, default: str = "") -> str:
    # get_secret can use process credentials outside routed/multiplex mode.
    # Require explicit membership first, even in single-profile processes.
    scope = current_secret_scope()
    if scope is None:
        raise EboekhoudenError("No Hermes profile secret scope is active. Credential access is refused.")
    if not isinstance(scope.get(name), str):
        return default
    value = get_secret(name)
    return value.strip() if isinstance(value, str) else default


def _config() -> dict[str, str]:
    token = _scoped_value("EBOEKHOUDEN_API_TOKEN") or _scoped_value("EBOEKHOUDEN_ACCESS_TOKEN")
    source = _scoped_value("EBOEKHOUDEN_SOURCE", SOURCE_DEFAULT)
    base_url = _scoped_value("EBOEKHOUDEN_BASE_URL", BASE_URL).rstrip("/")
    return {"token": token, "source": source, "base_url": base_url}


def _validate_config(cfg: dict[str, str]) -> None:
    if not cfg["token"]:
        raise EboekhoudenError(
            "No scoped API token is configured. Configure EBOEKHOUDEN_API_TOKEN through Hermes for the active profile."
        )
    if not re.fullmatch(r"[\w_ ]{1,10}", cfg["source"]):
        raise EboekhoudenError(
            "EBOEKHOUDEN_SOURCE must match ^[\\w_ ]{1,10}$ "
            "(for example: Hermes)."
        )
    if cfg["base_url"] != BASE_URL:
        raise EboekhoudenError("EBOEKHOUDEN_BASE_URL must equal https://api.e-boekhouden.nl.")


class _NoRedirect(request.HTTPRedirectHandler):
    """Prevent credentials from reaching a redirect destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _json_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
    timeout: int = 30,
) -> tuple[int, Any]:
    data = None
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with request.build_opener(_NoRedirect()).open(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return resp.status, None
            try:
                return resp.status, json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise EboekhoudenError("The API returned invalid JSON.") from exc
    except urllib_error.HTTPError as exc:
        exc.close()
        return exc.code, {"error": "API request failed. Response details were withheld."}
    except urllib_error.URLError as exc:
        raise EboekhoudenError("Network request failed. Details were withheld.") from exc


def _session_scope() -> str:
    cfg = _config()
    binding = {**cfg, "profile_home": str(_hermes_home().resolve())}
    return hashlib.sha256(json.dumps(binding, sort_keys=True).encode("utf-8")).hexdigest()


def _load_cached_session() -> str:
    path = _cache_path()
    if not path.exists() or path.is_symlink():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("scope") != _session_scope():
            return ""
        token = str(data.get("token") or "")
        expires_at = float(data.get("expires_at") or 0)
        if token and expires_at - time.time() > 60:
            return token
    except Exception:
        return ""
    return ""


def _save_cached_session(token: str, expires_in: int) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "token": token,
        "scope": _session_scope(),
        "expires_at": time.time() + max(0, int(expires_in)),
        "created_at": time.time(),
    }
    # The temporary file is private before the first credential byte is written.
    fd, temporary = tempfile.mkstemp(prefix=".eboekhouden-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _clear_cached_session() -> None:
    try:
        _cache_path().unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _session_token(force_new: bool = False) -> str:
    cfg = _config()
    _validate_config(cfg)
    if not force_new:
        cached = _load_cached_session()
        if cached:
            return cached
    status, payload = _json_request(
        "POST",
        f"{cfg['base_url']}/v1/session",
        body={"accessToken": cfg["token"], "source": cfg["source"]},
    )
    if status != 200 or not isinstance(payload, dict) or not payload.get("token"):
        raise EboekhoudenError(f"Could not start session: HTTP {status}. Response details were withheld.")
    token = str(payload["token"])
    expires_in = int(payload.get("expiresIn") or 0)
    _save_cached_session(token, expires_in)
    return token


def _normalize_path(path: str) -> str:
    cleaned = "/" + str(path or "").strip().lstrip("/")
    cleaned = parse.urlsplit(cleaned).path
    if ".." in cleaned or not cleaned.startswith("/v1/"):
        raise EboekhoudenError("Path must be an allowlisted /v1/... path.")
    cleaned = cleaned.rstrip("/") or cleaned
    for alias, canonical in sorted(PATH_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if cleaned == alias or cleaned.startswith(f"{alias}/"):
            return f"{canonical}{cleaned[len(alias):]}"
    return cleaned


def _unsupported_path_hint(path: str) -> str:
    for pattern, message in UNSUPPORTED_PATH_HINTS:
        if pattern.match(path):
            return message
    return ""


def _matches_any(path: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.match(path) for pattern in patterns)


def _method_allowed(method: str, path: str) -> bool:
    if method == "GET":
        return path in READ_PATHS or _matches_any(path, ID_READ_PATTERNS)
    if method == "POST":
        return path in POST_PATHS
    if method == "PATCH":
        return _matches_any(path, PATCH_PATTERNS)
    if method == "DELETE":
        return _matches_any(path, DELETE_PATTERNS)
    return False


def _call_api(method: str, path: str, query: Any = None, body: Any = None) -> dict[str, Any]:
    cfg = _config()
    _validate_config(cfg)
    token = _session_token()
    query = query if isinstance(query, dict) else {}
    url = f"{cfg['base_url']}{path}"
    if query:
        url = f"{url}?{parse.urlencode(query, doseq=True)}"
    status, payload = _json_request(method, url, headers={"Authorization": token}, body=body)
    if status in {401, 403}:
        _clear_cached_session()
        # Only reads are safe to replay automatically.
        if method == "GET":
            token = _session_token(force_new=True)
            status, payload = _json_request(method, url, headers={"Authorization": token}, body=body)
    if not 200 <= status < 300:
        payload = {"error": "API request failed. Response details were withheld."}
    return {"status": status, "payload": payload}


def _redacted_config_status() -> dict[str, Any]:
    cfg = _config()
    token = cfg["token"]
    return {
        "credential_source": "Hermes profile secret scope",
        "token_configured": bool(token),
        "source_valid": bool(re.fullmatch(r"[\w_ ]{1,10}", cfg["source"])),
        "base_url_valid": cfg["base_url"] == BASE_URL,
        "session_cache_exists": _cache_path().exists(),
    }


STATUS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "eboekhouden_status",
        "description": (
            "Check local e-Boekhouden configuration and optionally perform a safe "
            "session/API smoke test. Never returns the API token."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["config", "session", "administrations"],
                    "default": "config",
                    "description": (
                        "config only checks local setup; session creates/reuses an API "
                        "session; administrations performs a safe GET /v1/administration/linked."
                    ),
                }
            },
        },
    },
}


API_SCHEMA = {
    "type": "function",
    "function": {
        "name": "eboekhouden_api",
        "description": (
            "Call allowlisted e-Boekhouden REST endpoints. GET is executed directly. "
            "POST/PATCH/DELETE default to dry-run unless confirm=true. Allowed paths: "
            "/v1/administration, /v1/administration/linked, /v1/costcenter[/id], "
            "/v1/emailtemplate, /v1/invoice[/id], /v1/invoicetemplate, "
            "/v1/ledger[/id][/balance], /v1/member[/id], /v1/mutation[/id], "
            "/v1/mutation/invoice/outstanding, /v1/product[/id], /v1/product/groups, "
            "/v1/relation[/id], /v1/unit. Dutch aliases like /v1/facturen, "
            "/v1/relaties and /v1/artikelen are accepted. Recurring subscriptions "
            "must be planned in the e-Boekhouden UI; the REST API has no endpoint for them."
            " Financial booking creation, ledger updates, invoice mutation and invoice direct debit are blocked."
            " Confirmed invoice creation can send email when the email field is provided."
        ),
        "parameters": {
            "type": "object",
            "required": ["method", "path"],
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PATCH", "DELETE"],
                    "description": "HTTP method.",
                },
                "path": {
                    "type": "string",
                    "description": "API path, for example /v1/relation or /v1/invoice/123.",
                },
                "query": {
                    "type": "object",
                    "description": "Optional query parameters, including filters like date[gte].",
                    "additionalProperties": True,
                },
                "body": {
                    "type": "object",
                    "description": "JSON body for POST/PATCH calls.",
                    "additionalProperties": True,
                },
                "confirm": {
                    "type": "boolean",
                    "default": False,
                    "description": "Required true to execute POST/PATCH/DELETE. False returns dry-run.",
                },
                "acknowledge": {
                    "type": "string",
                    "description": "For DELETE only, must equal DELETE <path>.",
                },
            },
        },
    },
}


def _handle_status(args: dict, **kw) -> str:
    try:
        if not isinstance(args, dict):
            raise EboekhoudenError("Arguments must be an object.")
        action = str(args.get("action") or "config").strip().lower()
        status = _redacted_config_status()
        if action == "config":
            return tool_result({"ok": True, "config": status})
        if action == "session":
            _session_token()
            return tool_result({"ok": True, "config": _redacted_config_status(), "session": "ok"})
        if action == "administrations":
            result = _call_api("GET", "/v1/administration/linked", {"limit": 20}, None)
            return tool_result({"ok": 200 <= result["status"] < 300, **result})
        return tool_error("Unknown action. Use config, session, or administrations.")
    except EboekhoudenError as exc:
        return tool_error(str(exc))
    except Exception:
        return tool_error("Local configuration or API processing failed. Details were withheld.")


def _handle_api(args: dict, **kw) -> str:
    try:
        if not isinstance(args, dict):
            raise EboekhoudenError("Arguments must be an object.")
        for field in ("query", "body"):
            if field in args and not isinstance(args[field], dict):
                raise EboekhoudenError(f"{field} must be an object.")
        method = str(args.get("method") or "GET").strip().upper()
        path = _normalize_path(str(args.get("path") or ""))
        unsupported_hint = _unsupported_path_hint(path)
        if unsupported_hint:
            return tool_error(unsupported_hint)
        if not _method_allowed(method, path):
            return tool_error(f"{method} {path} is not allowlisted for this plugin.")

        query = args.get("query") if isinstance(args.get("query"), dict) else {}
        body = args.get("body") if isinstance(args.get("body"), dict) else None

        # Invoice creation can also create a booking through the mutation field.
        # Block the field even when its value is null or empty.
        # API changes require a new check of https://api.e-boekhouden.nl/openapi/v1.json.
        invoice_fields = {key.casefold() for key in body} if isinstance(body, dict) else set()
        if path == "/v1/invoice" and method == "POST" and "mutation" in invoice_fields:
            return tool_error(
                "Invoice mutation is blocked because it can create a financial booking. "
                "Remove the mutation field. This tool cannot create bookings or payments."
            )

        # Invoice direct debit can request a bank collection.
        # Invoice email remains allowed and can send an external message.
        if path == "/v1/invoice" and method == "POST" and "directdebit" in invoice_fields:
            return tool_error(
                "Invoice direct debit is blocked because it can request a bank collection. "
                "Remove the directDebit field. This tool cannot configure invoice direct debit."
            )

        confirm = args.get("confirm") is True
        if method in {"POST", "PATCH", "DELETE"} and not confirm:
            return tool_result({
                "dry_run": True,
                "message": "Set confirm=true to execute this write request.",
                "request": {"method": method, "path": path, "query": query, "body": body},
            })
        if method == "DELETE":
            expected = f"DELETE {path}"
            if str(args.get("acknowledge") or "") != expected:
                return tool_error(f"DELETE requires acknowledge='{expected}'.")

        result = _call_api(method, path, query, body)
        ok = 200 <= int(result["status"]) < 300
        return tool_result({"ok": ok, "method": method, "path": path, **result})
    except EboekhoudenError as exc:
        return tool_error(str(exc))
    except Exception:
        return tool_error("Local configuration or API processing failed. Details were withheld.")


def register(ctx) -> None:
    ctx.register_tool(
        name="eboekhouden_status",
        toolset="eboekhouden",
        schema=STATUS_SCHEMA,
        handler=_handle_status,
        description="Check e-Boekhouden local config and API session.",
        emoji="EB",
    )
    ctx.register_tool(
        name="eboekhouden_api",
        toolset="eboekhouden",
        schema=API_SCHEMA,
        handler=_handle_api,
        description="Allowlisted e-Boekhouden REST API client.",
        emoji="EB",
    )
