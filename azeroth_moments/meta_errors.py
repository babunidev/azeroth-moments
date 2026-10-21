"""Parse Meta/Instagram Graph API error payloads (never log tokens)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx


_TOKEN_RE = re.compile(r"(access_token=)[^&\s\"']+", re.I)


@dataclass(frozen=True)
class MetaApiError:
    http_status: int
    message: str
    error_type: str
    code: int | None
    error_subcode: int | None
    fbtrace_id: str
    permanent_auth: bool
    rate_limited: bool
    access_blocked: bool
    retry_after: float | None

    def summary(self) -> str:
        parts = [f"HTTP {self.http_status}"]
        if self.code is not None:
            parts.append(f"code={self.code}")
        if self.error_subcode is not None:
            parts.append(f"subcode={self.error_subcode}")
        if self.message:
            parts.append(self.message)
        if self.fbtrace_id:
            parts.append(f"fbtrace_id={self.fbtrace_id}")
        text = " ".join(parts)
        return _TOKEN_RE.sub(r"\1***", text)


def redact(text: str) -> str:
    return _TOKEN_RE.sub(r"\1***", text)


def parse_meta_error(response: httpx.Response) -> MetaApiError:
    http_status = response.status_code
    message = ""
    error_type = ""
    code: int | None = None
    subcode: int | None = None
    fbtrace = ""
    try:
        payload: dict[str, Any] = response.json()
        err = payload.get("error") or {}
        if isinstance(err, dict):
            message = str(err.get("message") or "")
            error_type = str(err.get("type") or "")
            code = _as_int(err.get("code"))
            subcode = _as_int(err.get("error_subcode"))
            fbtrace = str(err.get("fbtrace_id") or "")
        elif payload:
            message = redact(str(payload))[:240]
    except Exception:
        message = redact((response.text or "")[:240])

    retry_after = None
    if response.headers.get("Retry-After"):
        try:
            retry_after = float(response.headers["Retry-After"])
        except ValueError:
            retry_after = None

    rate_limited = http_status == 429 or code == 4 or "request limit" in message.lower()
    # Meta platform / developer-account security block (not a missing local env var).
    access_blocked = (not rate_limited) and (
        "api access blocked" in message.lower()
        or (code == 200 and "access blocked" in message.lower())
    )
    # True auth/permission failures (not app rate limits)
    permanent_auth = False
    if access_blocked:
        permanent_auth = True
    elif http_status == 401:
        permanent_auth = True
    elif http_status == 403 and not rate_limited:
        permanent_auth = True
    elif code in {10, 190, 102, 200, 294} and not rate_limited:
        permanent_auth = True
    elif "permission" in message.lower() and not rate_limited:
        permanent_auth = True

    if rate_limited and retry_after is None:
        retry_after = 60.0

    return MetaApiError(
        http_status=http_status,
        message=message,
        error_type=error_type,
        code=code,
        error_subcode=subcode,
        fbtrace_id=fbtrace,
        permanent_auth=permanent_auth,
        rate_limited=rate_limited,
        access_blocked=access_blocked,
        retry_after=retry_after,
    )


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
