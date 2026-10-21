"""Instagram publisher via Instagram API with Instagram Login."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

import httpx

from azeroth_moments.auth_instagram import GRAPH_HOST, InstagramAuth
from azeroth_moments.config import Config
from azeroth_moments.db import Database
from azeroth_moments.images.transform import PreparedInstagramAssets, prepare_instagram_assets
from azeroth_moments.meta_api_gate import InstagramApiGate
from azeroth_moments.meta_errors import parse_meta_error
from azeroth_moments.publishers.base import PublishResult
from azeroth_moments.staging.s3 import S3StagingAdapter
from azeroth_moments.videos.transform import PreparedInstagramVideoAssets

log = logging.getLogger(__name__)

PreparedAssets = PreparedInstagramAssets | PreparedInstagramVideoAssets

# Meta: query container status about once per minute, for no more than ~5 minutes.
DEFAULT_POLL_INTERVAL_IMAGE = 20.0
DEFAULT_POLL_INTERVAL_VIDEO = 60.0
DEFAULT_MAX_POLL_SECONDS = 300.0
RATE_LIMIT_MIN_BACKOFF = 60.0


class InstagramPublisher:
    name = "instagram"

    def __init__(
        self,
        config: Config,
        db: Database,
        *,
        client: httpx.Client | None = None,
        staging: S3StagingAdapter | None = None,
        auth: InstagramAuth | None = None,
        api_gate: InstagramApiGate | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self._client = client
        self.auth = auth or InstagramAuth(config, db)
        self.staging = staging
        self.api_gate = api_gate or InstagramApiGate(db)

    def _http(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(timeout=120.0)

    def _staging(self) -> S3StagingAdapter:
        if self.staging is None:
            self.staging = S3StagingAdapter(self.config)
        return self.staging

    def _api_base(self) -> str:
        return f"{GRAPH_HOST}/{self.config.instagram_api_version}"

    def test_connection(self) -> PublishResult:
        """Live probe of Instagram Login API access (single GET /me)."""
        return self.verify_api_access()

    def verify_api_access(self, *, force: bool = True) -> PublishResult:
        """Verify Meta Graph access via Instagram Login GET /me.

        Distinguishes local configuration from live API access. On
        ``API access blocked`` (code 200), trips the shared access-block gate
        so the worker stops further Graph calls. Successful probes clear it.
        """
        if self.config.dry_run:
            self.api_gate.record_access_ok(detail="dry-run")
            return PublishResult(ok=True, remote_id="dry-run-test")
        configured, reason = self.config.instagram_configured()
        if not configured:
            self.api_gate.record_access_check(status="error", detail=reason)
            return PublishResult(ok=False, error=f"configured: {reason}", auth_error=True)
        if not force and self.api_gate.access_blocked():
            return PublishResult(
                ok=False,
                error=f"auth: {self.api_gate.reason() or 'API access blocked'}",
                auth_error=True,
            )
        owns = self._client is None
        client = self._http()
        try:
            token = self.auth.ensure_fresh_token(client).access_token
            # Instagram Login: /me with user_id + username (graph.instagram.com)
            response = client.get(
                f"{self._api_base()}/me",
                params={
                    "fields": "user_id,username,account_type",
                    "access_token": token,
                },
            )
            if response.status_code >= 400:
                result = self._result_from_error_response(response, action="verify")
                if not result.auth_error and not (result.error or "").startswith("rate_limit:"):
                    self.api_gate.record_access_check(
                        status="error", detail=result.error or f"HTTP {response.status_code}"
                    )
                return result
            data = response.json()
            user_id = str(data.get("user_id") or data.get("id") or "")
            username = str(data.get("username") or "")
            configured_id = self.config.instagram_account_id
            if configured_id and user_id and configured_id not in {user_id, str(data.get("id") or "")}:
                detail = (
                    f"token user_id={user_id} does not match "
                    f"INSTAGRAM_ACCOUNT_ID (suffix …{configured_id[-4:]})"
                )
                self.api_gate.record_access_check(status="auth_error", detail=detail)
                return PublishResult(ok=False, error=f"auth: {detail}", auth_error=True)
            self.api_gate.record_access_ok(
                detail=f"ok username={username} account_type={data.get('account_type') or ''}"
            )
            return PublishResult(ok=True, remote_id=user_id or str(data.get("id") or ""))
        except Exception as exc:  # noqa: BLE001
            self.api_gate.record_access_check(status="error", detail=str(exc)[:400])
            return PublishResult(ok=False, error=str(exc))
        finally:
            if owns:
                client.close()

    def publish_prepared(
        self,
        assets: PreparedAssets,
        caption: str,
        *,
        prior_container_ids: list[str] | None = None,
    ) -> PublishResult:
        if not assets.validation_ok or not assets.paths:
            return PublishResult(
                ok=False,
                error="preparation failed: " + "; ".join(assets.errors or ["no assets"]),
            )
        media_kind = getattr(assets, "media_kind", "image")
        is_video = media_kind == "video" or any(
            p.suffix.lower() in {".mp4", ".mov", ".m4v"} for p in assets.paths
        )
        if self.config.dry_run:
            log.info(
                "DRY RUN Instagram would publish kind=%s mode=%s panels=%s",
                "video" if is_video else "image",
                assets.mode,
                len(assets.paths),
            )
            return PublishResult(ok=True, remote_id="dry-run")

        blocked = self._cooldown_result(container_ids=list(prior_container_ids or []))
        if blocked is not None:
            return blocked

        owns = self._client is None
        client = self._http()
        staged_keys: list[str] = []
        container_ids: list[str] = list(prior_container_ids or [])
        try:
            token = self.auth.ensure_fresh_token(client).access_token
            staging = self._staging()
            ig_user = self.config.instagram_account_id
            multi = assets.mode in {"carousel", "preview_carousel"} or len(assets.paths) > 1

            if multi:
                # Do not publish incomplete carousel sets
                if is_video and assets.mode == "preview_carousel":
                    expected = 4 if self.config.instagram_video_include_preview_first else 3
                    if len(assets.paths) != expected:
                        return PublishResult(
                            ok=False,
                            error=(
                                f"Incomplete video carousel: {len(assets.paths)}/{expected}"
                            ),
                        )
                child_ids: list[str] = []
                for path in assets.paths:
                    key = f"azeroth-moments/{uuid.uuid4().hex}/{path.name}"
                    staged = staging.upload(path, key)
                    staged_keys.append(staged.key)
                    child = self._create_media_container(
                        client,
                        ig_user,
                        token,
                        media_url=staged.url,
                        is_video=is_video,
                        is_carousel_item=True,
                    )
                    if child.ambiguous:
                        return PublishResult(
                            ok=False,
                            error=child.error,
                            ambiguous=True,
                            container_ids=container_ids + child_ids,
                            staged_keys=staged_keys,
                        )
                    if not child.ok:
                        child.staged_keys = staged_keys
                        child.container_ids = container_ids + child_ids
                        return child
                    # Videos must reach FINISHED before carousel assembly
                    if is_video:
                        ready_child = self._wait_container_ready(
                            client,
                            child.remote_id,
                            token,
                            is_video=True,
                        )
                        if not ready_child.ok:
                            ready_child.staged_keys = staged_keys
                            ready_child.container_ids = container_ids + child_ids + [
                                child.remote_id
                            ]
                            return ready_child
                    child_ids.append(child.remote_id)
                    container_ids.append(child.remote_id)

                carousel = self._create_carousel_container(
                    client, ig_user, token, child_ids, caption
                )
                if carousel.ambiguous or not carousel.ok:
                    carousel.staged_keys = staged_keys
                    carousel.container_ids = container_ids
                    return carousel
                container_ids.append(carousel.remote_id)
                creation_id = carousel.remote_id
            else:
                path = assets.paths[0]
                key = f"azeroth-moments/{uuid.uuid4().hex}/{path.name}"
                staged = staging.upload(path, key)
                staged_keys.append(staged.key)
                created = self._create_media_container(
                    client,
                    ig_user,
                    token,
                    media_url=staged.url,
                    is_video=is_video,
                    caption=caption,
                    is_carousel_item=False,
                )
                if created.ambiguous or not created.ok:
                    created.staged_keys = staged_keys
                    created.container_ids = container_ids
                    return created
                container_ids.append(created.remote_id)
                creation_id = created.remote_id

            ready = self._wait_container_ready(
                client,
                creation_id,
                token,
                is_video=is_video,
            )
            if not ready.ok:
                ready.staged_keys = staged_keys
                ready.container_ids = container_ids
                return ready

            published = self._publish_container(client, ig_user, token, creation_id)
            published.staged_keys = staged_keys
            published.container_ids = container_ids
            if published.ok:
                for key in staged_keys:
                    staging.delete(key)
            return published
        except httpx.TimeoutException:
            return PublishResult(
                ok=False,
                error="Ambiguous Instagram timeout",
                ambiguous=True,
                container_ids=container_ids,
                staged_keys=staged_keys,
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            auth = msg.startswith("auth:")
            return PublishResult(
                ok=False,
                error=msg,
                auth_error=auth,
                container_ids=container_ids,
                staged_keys=staged_keys,
            )
        finally:
            if owns:
                client.close()

    def publish(self, path: Path, caption: str) -> PublishResult:
        assets = prepare_instagram_assets(path, self.config, allow_fallback=True)
        return self.publish_prepared(assets, caption)

    def reconcile(
        self,
        container_ids: list[str],
        remote_id: str,
        *,
        publish_if_finished: bool = False,
    ) -> PublishResult:
        """Check whether a prior ambiguous publish already succeeded.

        Never recreates containers. Auto-publish only when publish_if_finished=True
        (CLI opt-in). Default worker path never auto-publishes FINISHED containers.
        """
        if remote_id:
            return PublishResult(ok=True, remote_id=remote_id, container_ids=container_ids)
        if not container_ids:
            return PublishResult(ok=False, error="Nothing to reconcile")
        blocked = self._cooldown_result(container_ids=container_ids)
        if blocked is not None:
            return blocked
        owns = self._client is None
        client = self._http()
        try:
            token = self.auth.ensure_fresh_token(client).access_token
            creation_id = container_ids[-1]
            response = self._get_container_status(client, creation_id, token)
            if response.status_code >= 400:
                return self._result_from_error_response(
                    response, action="reconcile", container_ids=container_ids
                )
            data = response.json()
            status = str(data.get("status_code", "")).upper()
            detail = str(data.get("status", ""))
            if status == "PUBLISHED":
                return PublishResult(
                    ok=True,
                    remote_id=str(data.get("id") or creation_id),
                    container_ids=container_ids,
                )
            if status == "FINISHED":
                if publish_if_finished:
                    published = self._publish_container(
                        client, self.config.instagram_account_id, token, creation_id
                    )
                    published.container_ids = container_ids
                    return published
                return PublishResult(
                    ok=False,
                    error="Container status FINISHED; manual reconcile required (no auto-publish)",
                    ambiguous=True,
                    retry_after=3600.0,
                    container_ids=container_ids,
                )
            if status in {"ERROR", "EXPIRED"}:
                return PublishResult(
                    ok=False,
                    error=f"Container {status}" + (f": {detail}" if detail else ""),
                    container_ids=container_ids,
                )
            return PublishResult(
                ok=False,
                error=f"Container status {status or 'unknown'}",
                ambiguous=True,
                retry_after=max(DEFAULT_POLL_INTERVAL_VIDEO, 60.0),
                container_ids=container_ids,
            )
        finally:
            if owns:
                client.close()

    def fetch_permalink(self, media_id: str) -> PublishResult:
        """GET official permalink for a published IG media id (graph.instagram.com)."""
        if not media_id:
            return PublishResult(ok=False, error="missing media id")
        blocked = self._cooldown_result()
        if blocked is not None:
            return blocked
        owns = self._client is None
        client = self._http()
        try:
            token = self.auth.ensure_fresh_token(client).access_token
            response = client.get(
                f"{self._api_base()}/{media_id}",
                params={"fields": "id,permalink,media_type", "access_token": token},
            )
            if response.status_code >= 400:
                return self._result_from_error_response(
                    response, action="permalink", ambiguous_on_error=True
                )
            data = response.json()
            permalink = str(data.get("permalink") or "").strip()
            if not permalink:
                return PublishResult(
                    ok=False,
                    error="permalink temporarily unavailable",
                    ambiguous=True,
                    retry_after=60.0,
                    remote_id=str(data.get("id") or media_id),
                )
            return PublishResult(
                ok=True,
                remote_id=str(data.get("id") or media_id),
                permalink=permalink,
            )
        except httpx.HTTPError as exc:
            return PublishResult(
                ok=False,
                error=f"network: {exc}",
                ambiguous=True,
                retry_after=60.0,
            )
        finally:
            if owns:
                client.close()

    def inspect_containers(self, container_ids: list[str]) -> list[dict[str, object]]:
        """One status GET per unique container id (honors global cooldown)."""
        if self.api_gate.access_blocked():
            reason = self.api_gate.reason() or "API access blocked"
            return [
                {
                    "container_id": cid,
                    "error": f"auth: API access blocked ({reason})",
                    "access_blocked": True,
                    "permanent_auth": True,
                }
                for cid in container_ids
            ]
        if self.api_gate.blocked():
            snap = self.api_gate.snapshot()
            return [
                {
                    "container_id": cid,
                    "error": f"rate_limit: global cooldown {snap['remaining_seconds']}s",
                }
                for cid in container_ids
            ]
        owns = self._client is None
        client = self._http()
        out: list[dict[str, object]] = []
        try:
            token = self.auth.ensure_fresh_token(client).access_token
            seen: set[str] = set()
            for cid in container_ids:
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                if self.api_gate.access_blocked():
                    out.append(
                        {
                            "container_id": cid,
                            "error": f"auth: API access blocked ({self.api_gate.reason()})",
                            "access_blocked": True,
                            "permanent_auth": True,
                        }
                    )
                    continue
                if self.api_gate.blocked():
                    out.append(
                        {
                            "container_id": cid,
                            "error": "rate_limit: global cooldown engaged mid-inspect",
                        }
                    )
                    continue
                response = self._get_container_status(client, cid, token)
                if response.status_code >= 400:
                    meta = parse_meta_error(response)
                    if meta.access_blocked:
                        self.api_gate.trip_access_blocked(detail=meta.summary())
                    elif meta.rate_limited:
                        self.api_gate.trip_rate_limit(
                            retry_after=meta.retry_after, detail=meta.summary()
                        )
                    out.append(
                        {
                            "container_id": cid,
                            "http_status": response.status_code,
                            "error": meta.summary(),
                            "rate_limited": meta.rate_limited,
                            "access_blocked": meta.access_blocked,
                            "permanent_auth": meta.permanent_auth,
                        }
                    )
                    continue
                data = response.json()
                out.append(
                    {
                        "container_id": cid,
                        "http_status": response.status_code,
                        "status_code": data.get("status_code"),
                        "status": data.get("status"),
                        "id": data.get("id"),
                    }
                )
            return out
        finally:
            if owns:
                client.close()

    def _create_media_container(
        self,
        client: httpx.Client,
        ig_user: str,
        token: str,
        *,
        media_url: str,
        is_video: bool = False,
        caption: str | None = None,
        is_carousel_item: bool = False,
    ) -> PublishResult:
        params: dict[str, str] = {"access_token": token}
        if is_video:
            params["media_type"] = "VIDEO"
            params["video_url"] = media_url
        else:
            params["image_url"] = media_url
        if is_carousel_item:
            params["is_carousel_item"] = "true"
        if caption is not None:
            params["caption"] = caption
        response = client.post(f"{self._api_base()}/{ig_user}/media", params=params)
        return self._parse_id_response(response, "create media")

    def _create_carousel_container(
        self,
        client: httpx.Client,
        ig_user: str,
        token: str,
        children: list[str],
        caption: str,
    ) -> PublishResult:
        params = {
            "media_type": "CAROUSEL",
            "children": ",".join(children),
            "caption": caption,
            "access_token": token,
        }
        response = client.post(f"{self._api_base()}/{ig_user}/media", params=params)
        return self._parse_id_response(response, "create carousel")

    def _get_container_status(
        self,
        client: httpx.Client,
        creation_id: str,
        token: str,
    ) -> httpx.Response:
        """GET container status for Instagram Login (graph.instagram.com)."""
        # Instagram Login uses graph.instagram.com with the user access token
        # as a query param (not Facebook Page token / graph.facebook.com).
        return client.get(
            f"{self._api_base()}/{creation_id}",
            params={"fields": "id,status_code,status", "access_token": token},
        )

    def _cooldown_result(
        self, *, container_ids: list[str] | None = None
    ) -> PublishResult | None:
        if self.api_gate.access_blocked():
            reason = self.api_gate.reason() or "API access blocked"
            return PublishResult(
                ok=False,
                error=f"auth: API access blocked ({reason})",
                auth_error=True,
                retry_after=self.api_gate.remaining(),
                container_ids=container_ids,
            )
        remaining = self.api_gate.remaining()
        if remaining <= 0:
            return None
        return PublishResult(
            ok=False,
            error=(
                f"rate_limit: global Instagram API cooldown "
                f"{remaining:.0f}s ({self.api_gate.reason() or 'active'})"
            ),
            ambiguous=True,
            retry_after=remaining,
            container_ids=container_ids,
        )

    def _result_from_error_response(
        self,
        response: httpx.Response,
        *,
        action: str,
        container_ids: list[str] | None = None,
        ambiguous_on_error: bool = False,
    ) -> PublishResult:
        meta = parse_meta_error(response)
        log.warning("Instagram %s error: %s", action, meta.summary())
        if meta.access_blocked:
            self.api_gate.trip_access_blocked(detail=meta.summary())
            return PublishResult(
                ok=False,
                error=f"auth: {action} {meta.summary()}",
                auth_error=True,
                retry_after=self.api_gate.remaining(),
                container_ids=container_ids,
            )
        if meta.permanent_auth:
            self.api_gate.record_access_check(
                status="auth_error", detail=meta.summary()
            )
            return PublishResult(
                ok=False,
                error=f"auth: {action} {meta.summary()}",
                auth_error=True,
                container_ids=container_ids,
            )
        if meta.rate_limited:
            cooldown = self.api_gate.trip_rate_limit(
                retry_after=meta.retry_after, detail=meta.summary()
            )
            # Always ambiguous: partial carousels must not be auto-recreated.
            return PublishResult(
                ok=False,
                error=f"rate_limit: {action} {meta.summary()}",
                ambiguous=True,
                retry_after=cooldown,
                container_ids=container_ids,
            )
        if response.status_code >= 500:
            return PublishResult(
                ok=False,
                error=f"{action} {meta.summary()}",
                ambiguous=ambiguous_on_error,
                container_ids=container_ids,
            )
        return PublishResult(
            ok=False,
            error=f"{action} {meta.summary()}",
            container_ids=container_ids,
        )

    def _wait_container_ready(
        self,
        client: httpx.Client,
        creation_id: str,
        token: str,
        *,
        is_video: bool = False,
        interval: float | None = None,
        max_duration: float = DEFAULT_MAX_POLL_SECONDS,
    ) -> PublishResult:
        """Poll until FINISHED, permanent failure, auth error, rate limit, or timeout.

        On Meta rate limits, trip the shared gate and stop immediately (no local
        retry storm). Poll intervals follow Meta guidance (~1/min for video).
        """
        interval = interval or (
            DEFAULT_POLL_INTERVAL_VIDEO if is_video else DEFAULT_POLL_INTERVAL_IMAGE
        )
        deadline = time.time() + max_duration

        while time.time() < deadline:
            blocked = self._cooldown_result()
            if blocked is not None:
                if (blocked.error or "").startswith("rate_limit:"):
                    blocked.error = (
                        f"rate_limit: status "
                        f"{blocked.error.removeprefix('rate_limit: ')}"
                    )
                return blocked

            response = self._get_container_status(client, creation_id, token)
            if response.status_code >= 400:
                # Rate limit → global cooldown + stop (do not keep polling)
                result = self._result_from_error_response(
                    response, action="status", ambiguous_on_error=True
                )
                log.warning(
                    "Instagram container status error container_id=%s %s",
                    creation_id,
                    result.error,
                )
                return result

            data = response.json()
            status = str(data.get("status_code", "")).upper()
            detail = str(data.get("status", ""))
            if status == "FINISHED":
                return PublishResult(ok=True, remote_id=creation_id)
            if status == "PUBLISHED":
                return PublishResult(ok=True, remote_id=creation_id)
            if status in {"ERROR", "EXPIRED"}:
                return PublishResult(
                    ok=False,
                    error=f"container {status}" + (f": {detail}" if detail else ""),
                )
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))

        return PublishResult(
            ok=False,
            error="container not ready in time",
            ambiguous=True,
            retry_after=RATE_LIMIT_MIN_BACKOFF,
        )

    def _publish_container(
        self,
        client: httpx.Client,
        ig_user: str,
        token: str,
        creation_id: str,
    ) -> PublishResult:
        response = client.post(
            f"{self._api_base()}/{ig_user}/media_publish",
            params={"creation_id": creation_id, "access_token": token},
        )
        return self._parse_id_response(response, "publish", ambiguous_on_error=True)

    def _parse_id_response(
        self,
        response: httpx.Response,
        action: str,
        *,
        ambiguous_on_error: bool = False,
    ) -> PublishResult:
        if response.status_code >= 400:
            return self._result_from_error_response(
                response, action=action, ambiguous_on_error=ambiguous_on_error
            )
        try:
            data = response.json()
        except json.JSONDecodeError:
            return PublishResult(
                ok=False,
                error=f"{action}: unreadable response",
                ambiguous=True,
            )
        media_id = str(data.get("id", ""))
        if not media_id:
            return PublishResult(
                ok=False,
                error=f"{action}: missing id",
                ambiguous=True,
            )
        return PublishResult(ok=True, remote_id=media_id)
