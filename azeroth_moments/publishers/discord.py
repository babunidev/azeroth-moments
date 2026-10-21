"""Discord webhook publisher — exact original file only."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from azeroth_moments.config import Config
from azeroth_moments.publishers.base import PublishResult

log = logging.getLogger(__name__)


class DiscordPublisher:
    name = "discord"

    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = client

    def _http(self, *, timeout: float = 60.0) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(timeout=timeout)

    def publish(self, path: Path, caption: str) -> PublishResult:
        if self.config.dry_run:
            log.info("DRY RUN Discord would upload %s", path)
            return PublishResult(ok=True, remote_id="dry-run")

        suffix = path.suffix.lower()
        is_video = suffix in {".mp4", ".mov", ".mkv", ".webm"}
        if is_video and not self.config.discord_video_enabled:
            return PublishResult(ok=False, error="Discord video publishing disabled")

        size = path.stat().st_size
        if size > self.config.discord_max_attachment_bytes:
            msg = (
                f"oversized: {size} bytes exceeds Discord limit "
                f"{self.config.discord_max_attachment_bytes}"
            )
            log.error("Discord upload failed for %s: %s", path, msg)
            return PublishResult(ok=False, error=msg)

        url = self.config.discord_webhook_url
        if not url:
            return PublishResult(ok=False, error="auth: DISCORD_WEBHOOK_URL missing", auth_error=True)

        # Original bytes only — no transcode/resize. Longer timeout for large videos.
        timeout = 180.0 if is_video else 60.0
        owns_client = self._client is None
        client = self._http(timeout=timeout)
        try:
            with path.open("rb") as fh:
                files = {
                    "files[0]": (path.name, fh, "application/octet-stream"),
                }
                data = {"payload_json": _payload_json(caption)}
                response = client.post(f"{url}?wait=true", data=data, files=files)
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", "5"))
                return PublishResult(
                    ok=False,
                    error=f"429 rate limited",
                    retry_after=retry_after,
                )
            if response.status_code in {401, 403, 404}:
                return PublishResult(
                    ok=False,
                    error=f"auth: Discord webhook HTTP {response.status_code}",
                    auth_error=True,
                )
            if response.status_code >= 500:
                return PublishResult(ok=False, error=f"HTTP {response.status_code}: temporary")
            if response.status_code >= 400:
                return PublishResult(
                    ok=False,
                    error=f"HTTP {response.status_code}: {response.text[:300]}",
                )
            try:
                body = response.json()
                message_id = str(body.get("id", ""))
            except Exception:
                # Ambiguous: Discord may have accepted without parseable body
                return PublishResult(
                    ok=False,
                    error="Ambiguous Discord response",
                    ambiguous=True,
                )
            return PublishResult(ok=True, remote_id=message_id)
        except httpx.TimeoutException:
            return PublishResult(
                ok=False,
                error="Ambiguous Discord timeout after possible send",
                ambiguous=True,
            )
        except httpx.HTTPError as exc:
            return PublishResult(ok=False, error=f"network: {exc}")
        finally:
            if owns_client:
                client.close()

    def publish_text(self, content: str) -> PublishResult:
        """Send a text-only webhook message (no file attachment)."""
        if self.config.dry_run:
            log.info("DRY RUN Discord would post text message")
            return PublishResult(ok=True, remote_id="dry-run-text")
        url = self.config.discord_webhook_url
        if not url:
            return PublishResult(
                ok=False, error="auth: DISCORD_WEBHOOK_URL missing", auth_error=True
            )
        owns_client = self._client is None
        client = self._http()
        try:
            response = client.post(
                f"{url}?wait=true",
                json={"content": content[:2000]},
            )
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", "5"))
                return PublishResult(
                    ok=False,
                    error="429 rate limited",
                    retry_after=retry_after,
                )
            if response.status_code in {401, 403, 404}:
                return PublishResult(
                    ok=False,
                    error=f"auth: Discord webhook HTTP {response.status_code}",
                    auth_error=True,
                )
            if response.status_code >= 500:
                return PublishResult(ok=False, error=f"HTTP {response.status_code}: temporary")
            if response.status_code >= 400:
                return PublishResult(
                    ok=False,
                    error=f"HTTP {response.status_code}: {response.text[:300]}",
                )
            try:
                body = response.json()
                message_id = str(body.get("id", ""))
            except Exception:
                return PublishResult(
                    ok=False,
                    error="Ambiguous Discord text response",
                    ambiguous=True,
                )
            return PublishResult(ok=True, remote_id=message_id)
        except httpx.TimeoutException:
            return PublishResult(
                ok=False,
                error="Ambiguous Discord text timeout",
                ambiguous=True,
            )
        except httpx.HTTPError as exc:
            return PublishResult(ok=False, error=f"network: {exc}")
        finally:
            if owns_client:
                client.close()

    def test_connection(self) -> PublishResult:
        if self.config.dry_run:
            return PublishResult(ok=True, remote_id="dry-run-test")
        ready, reason = self.config.discord_ready()
        if not ready:
            return PublishResult(ok=False, error=reason, auth_error=True)
        # Lightweight GET on webhook
        owns_client = self._client is None
        client = self._http()
        try:
            response = client.get(self.config.discord_webhook_url)
            if response.status_code == 200:
                return PublishResult(ok=True, remote_id="webhook-ok")
            return PublishResult(
                ok=False,
                error=f"Webhook test HTTP {response.status_code}",
                auth_error=response.status_code in {401, 403, 404},
            )
        except httpx.HTTPError as exc:
            return PublishResult(ok=False, error=str(exc))
        finally:
            if owns_client:
                client.close()


def _payload_json(caption: str) -> str:
    import json

    return json.dumps({"content": caption[:2000]})
