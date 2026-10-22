"""Background worker: drain publish queue with rate limits and retries."""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from pathlib import Path

from azeroth_moments.auth_instagram import InstagramAuth
from azeroth_moments.config import Config
from azeroth_moments.db import Database
from azeroth_moments.images.transform import prepare_instagram_assets
from azeroth_moments.notify import notify_published
from azeroth_moments.paths import pid_path
from azeroth_moments.publishers.discord import DiscordPublisher
from azeroth_moments.publishers.instagram import InstagramPublisher
from azeroth_moments.discord_links import InstagramDiscordLinkAnnouncer
from azeroth_moments.meta_api_gate import InstagramApiGate
from azeroth_moments.rate_limit import RateLimiter
from azeroth_moments.recovery import InstagramRecovery
from azeroth_moments.retry import is_auth_error, next_retry_timestamp
from azeroth_moments.videos.transform import prepare_instagram_video_assets
from azeroth_moments.watcher import baseline_scan, start_observer

log = logging.getLogger(__name__)

# How often the idle worker re-checks whether the long-lived token is due.
TOKEN_MAINTENANCE_INTERVAL = 6 * 3600
KEY_TOKEN_MAINTENANCE_AT = "instagram_token_maintenance_at"


class Worker:
    def __init__(self, config: Config, db: Database | None = None) -> None:
        self.config = config
        self.db = db or Database()
        self.rate_limiter = RateLimiter(self.db, config.publish_max_per_hour)
        self.discord = DiscordPublisher(config)
        self.instagram_api_gate = InstagramApiGate(self.db)
        self.instagram_auth = InstagramAuth(config, self.db)
        self.instagram = InstagramPublisher(
            config,
            self.db,
            api_gate=self.instagram_api_gate,
            auth=self.instagram_auth,
        )
        self.instagram_recovery = InstagramRecovery(
            config, self.db, publisher=self.instagram, api_gate=self.instagram_api_gate
        )
        self.link_announcer = InstagramDiscordLinkAnnouncer(
            config,
            self.db,
            instagram=self.instagram,
            discord=self.discord,
            api_gate=self.instagram_api_gate,
        )
        self._stop = False

    def request_stop(self, *_args: object) -> None:
        self._stop = True

    def write_pid(self) -> None:
        path = pid_path()
        path.write_text(str(os.getpid()), encoding="utf-8")

    def clear_pid(self) -> None:
        path = pid_path()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        self.write_pid()
        try:
            if not self.db.baseline_done():
                baseline_scan(self.db, self.config)
            else:
                from azeroth_moments.ingest import is_candidate_file, register_screenshot

                for path in sorted(self.config.watch_directory.iterdir()):
                    if is_candidate_file(path, self.config):
                        register_screenshot(
                            self.db, self.config, path, ingest_reason="new"
                        )
            observer = start_observer(self.db, self.config)
            log.info(
                "Worker started dry_run=%s discord=%s instagram=%s",
                self.config.dry_run,
                self.config.discord_enabled,
                self.config.instagram_enabled,
            )
            try:
                while not self._stop:
                    if self.db.is_paused():
                        time.sleep(1.0)
                        continue
                    did_work = False
                    did_work |= self._maintain_instagram_token()
                    did_work |= self._process_instagram_recoveries()
                    did_work |= self._reconcile_unknowns()
                    did_work |= self._process_platform("discord")
                    did_work |= self._process_platform("instagram")
                    did_work |= self._process_video_link_announcements()
                    if not did_work and self.instagram_api_gate.blocked():
                        # Idle while Meta cooldown is active — Discord already tried.
                        time.sleep(min(30.0, max(1.5, self.instagram_api_gate.remaining())))
                    else:
                        time.sleep(0.5 if did_work else 1.5)
            finally:
                observer.stop()
                observer.join(timeout=5)
        finally:
            self.clear_pid()
            log.info("Worker stopped")

    def _maintain_instagram_token(self) -> bool:
        """Periodically refresh the long-lived Instagram Login token (~60 days)."""
        if self.config.dry_run or not self.config.instagram_enabled:
            return False
        if self.instagram_api_gate.access_blocked():
            return False
        now = time.time()
        try:
            last = float(self.db.get_control(KEY_TOKEN_MAINTENANCE_AT, "0") or 0)
        except ValueError:
            last = 0.0
        if last > 0 and (now - last) < TOKEN_MAINTENANCE_INTERVAL:
            return False
        self.db.set_control(KEY_TOKEN_MAINTENANCE_AT, str(now))
        result = self.instagram_auth.maybe_refresh(force=False, now=now)
        if result.get("refreshed"):
            log.info(
                "Instagram token auto-renewed; expires_at=%s",
                result.get("expires_at") or "unknown",
            )
            return True
        if result.get("attempted") and not result.get("refreshed"):
            log.warning(
                "Instagram token auto-renew attempt failed: %s",
                result.get("error") or result.get("reason"),
            )
        return False

    def _process_instagram_recoveries(self) -> bool:
        """Resume user-requested Instagram recoveries after cooldown / restart."""
        reports = self.instagram_recovery.process_queued()
        for report in reports:
            log.info(
                "Instagram recovery screenshot_id=%s action=%s status=%s: %s",
                report.screenshot_id,
                report.action,
                report.status_after,
                report.message,
            )
            if report.status_after == "published" and report.ok:
                self._announce_video_link_if_needed(report.screenshot_id)
        return bool(reports)

    def _process_video_link_announcements(self) -> bool:
        results = self.link_announcer.process_pending()
        for result in results:
            if result.skipped:
                continue
            if result.ok:
                log.info(
                    "Discord Instagram-link announce screenshot_id=%s permalink=%s",
                    result.screenshot_id,
                    result.permalink,
                )
            else:
                log.warning(
                    "Discord Instagram-link announce deferred screenshot_id=%s: %s",
                    result.screenshot_id,
                    result.message,
                )
        return any(not r.skipped for r in results)

    def _announce_video_link_if_needed(self, screenshot_id: int) -> None:
        shot = self.db.get_screenshot(screenshot_id)
        ig_pub = self.db.get_publication(screenshot_id, "instagram")
        if shot is None or ig_pub is None:
            return
        if (shot.media_type or "image") != "video":
            return
        if ig_pub.status != "published" or not ig_pub.remote_id:
            return
        result = self.link_announcer.announce_for(shot, ig_pub)
        if result.ok and not result.skipped:
            log.info(
                "Discord Instagram-link announce screenshot_id=%s permalink=%s",
                screenshot_id,
                result.permalink,
            )

    def _reconcile_unknowns(self) -> bool:
        worked = False
        now = time.time()
        # Shared Meta cooldown blocks all Instagram Graph traffic (not Discord).
        ig_cooling = self.instagram_api_gate.blocked(now=now)
        for shot, pub in self.db.list_unknown("instagram"):
            # Respect per-row backoff — critical to avoid Meta rate-limit storms
            if pub.next_retry_at and pub.next_retry_at > now:
                continue
            if ig_cooling:
                # Push row backoff to match gate so restarts still honor cooldown
                remaining = self.instagram_api_gate.remaining(now=now)
                self.db.update_publication(
                    shot.id,
                    "instagram",
                    status="unknown",
                    last_error=pub.last_error
                    or f"rate_limit: global cooldown {remaining:.0f}s",
                    container_ids=json.loads(pub.container_ids or "[]"),
                    next_retry_at=now + remaining,
                )
                continue
            worked = True
            container_ids = json.loads(pub.container_ids or "[]")
            result = self.instagram.reconcile(container_ids, pub.remote_id)
            if result.ok:
                self.db.update_publication(
                    shot.id,
                    "instagram",
                    status="published",
                    remote_id=result.remote_id,
                    last_error="",
                    container_ids=container_ids,
                )
                notify_published(
                    "instagram",
                    filename=shot.path,
                    media_type=getattr(shot, "media_type", "image") or "image",
                    enabled=self.config.desktop_notifications,
                    dry_run=self.config.dry_run,
                )
                # Do not count reconcile-as-published toward rate limit spam
                self._announce_video_link_if_needed(shot.id)
            elif getattr(result, "auth_error", False) or (
                is_auth_error(result.error or "")
                and not (result.error or "").lower().startswith("rate_limit:")
            ):
                self.db.update_publication(
                    shot.id,
                    "instagram",
                    status="failed",
                    last_error=result.error,
                    container_ids=container_ids,
                    next_retry_at=0.0,
                )
                log.error(
                    "Instagram reconcile auth/permission failure for screenshot_id=%s: %s",
                    shot.id,
                    result.error,
                )
            elif result.ambiguous:
                retry_after = getattr(result, "retry_after", None) or 60.0
                # Stay unknown — never recreate containers or republish
                self.db.update_publication(
                    shot.id,
                    "instagram",
                    status="unknown",
                    last_error=result.error,
                    container_ids=container_ids,
                    next_retry_at=next_retry_timestamp(
                        max(pub.attempts, 1), retry_after=retry_after
                    ),
                )
            else:
                self.db.update_publication(
                    shot.id,
                    "instagram",
                    status="failed",
                    last_error=result.error,
                    container_ids=container_ids,
                    next_retry_at=0.0,
                )
        for shot, pub in self.db.list_unknown("discord"):
            if pub.next_retry_at and pub.next_retry_at > now:
                continue
            worked = True
            # Discord cannot be safely reconciled without message id; keep unknown
            self.db.update_publication(
                shot.id,
                "discord",
                status="unknown",
                last_error=pub.last_error or "Ambiguous Discord outcome; not retrying publish",
                next_retry_at=next_retry_timestamp(max(pub.attempts, 1), retry_after=3600),
            )
        return worked

    def _process_platform(self, platform: str) -> bool:
        if platform == "discord":
            ready, reason = self.config.discord_ready()
            if not self.config.discord_enabled:
                return False
            if not ready and not self.config.dry_run:
                return False
        if platform == "instagram":
            if not self.config.instagram_enabled and not self.config.dry_run:
                return False
            ready, reason = self.config.instagram_configured()
            if not ready and not self.config.dry_run:
                return False
            if self.instagram_api_gate.access_blocked():
                # Meta platform block — do not create media or poll Graph.
                return False
            if self.instagram_api_gate.blocked():
                return False

        if not self.rate_limiter.can_publish():
            return False

        claimed = self.db.claim_next_work(platform)
        if claimed is None:
            return False
        shot, pub = claimed
        path = Path(shot.path)
        if not path.is_file():
            self.db.update_publication(
                shot.id,
                platform,
                status="failed",
                last_error="Source file missing",
                increment_attempts=True,
            )
            return True

        caption = self.config.caption_for(platform, path)
        media_type = getattr(shot, "media_type", "image") or "image"
        try:
            if platform == "discord":
                if media_type == "video" and not self.config.discord_video_enabled:
                    self.db.update_publication(
                        shot.id,
                        platform,
                        status="failed",
                        last_error="Discord video publishing disabled",
                    )
                    return True
                result = self.discord.publish(path, caption)
            else:
                # Skip disabled Instagram publications cleanly
                if not self.config.instagram_enabled and not self.config.dry_run:
                    self.db.update_publication(
                        shot.id,
                        platform,
                        status="failed",
                        last_error="Instagram disabled",
                    )
                    return True
                if media_type == "video":
                    if not self.config.instagram_video_enabled:
                        self.db.update_publication(
                            shot.id,
                            platform,
                            status="failed",
                            last_error="Instagram video publishing disabled",
                        )
                        return True
                    assets = prepare_instagram_video_assets(path, self.config)
                    padding_applied = False
                    fallback_used = False
                else:
                    assets = prepare_instagram_assets(
                        path, self.config, allow_fallback=True
                    )
                    padding_applied = assets.padding_applied
                    fallback_used = assets.fallback_used
                self.db.update_shape(shot.id, assets.metrics.shape)
                self.db.save_transform(
                    shot.id,
                    instagram_mode=assets.mode,
                    panel_count=assets.panel_count,
                    panel_output_dims=assets.panel_output_dims,
                    padding_applied=padding_applied,
                    fallback_used=fallback_used,
                    validation_ok=assets.validation_ok,
                    decision_summary=assets.decision_summary,
                )
                if not assets.validation_ok:
                    self.db.update_publication(
                        shot.id,
                        platform,
                        status="failed",
                        last_error="preparation: " + "; ".join(assets.errors),
                        increment_attempts=True,
                    )
                    return True
                prior = json.loads(pub.container_ids or "[]")
                result = self.instagram.publish_prepared(
                    assets, caption, prior_container_ids=prior or None
                )
        except Exception as exc:  # noqa: BLE001
            log.exception("Publish error %s/%s", platform, shot.id)
            result = type("R", (), {
                "ok": False,
                "error": str(exc),
                "ambiguous": False,
                "auth_error": is_auth_error(str(exc)),
                "retry_after": None,
                "remote_id": "",
                "container_ids": None,
                "staged_keys": None,
            })()

        self._apply_result(
            shot.id, platform, pub.attempts, result, media_type=media_type
        )
        return True

    def _apply_result(
        self,
        screenshot_id: int,
        platform: str,
        attempts: int,
        result: object,
        *,
        media_type: str = "image",
    ) -> None:
        ok = bool(getattr(result, "ok", False))
        error = str(getattr(result, "error", "") or "")
        ambiguous = bool(getattr(result, "ambiguous", False))
        auth_error = bool(getattr(result, "auth_error", False)) or is_auth_error(error)
        retry_after = getattr(result, "retry_after", None)
        remote_id = str(getattr(result, "remote_id", "") or "")
        container_ids = getattr(result, "container_ids", None)
        staged_keys = getattr(result, "staged_keys", None)

        if ok:
            self.db.update_publication(
                screenshot_id,
                platform,
                status="published",
                remote_id=remote_id,
                last_error="",
                container_ids=container_ids,
                staged_keys=staged_keys,
                increment_attempts=True,
            )
            if not self.config.dry_run:
                self.rate_limiter.record(platform)
            log.info("Published %s screenshot_id=%s remote_id=%s", platform, screenshot_id, remote_id)
            shot = self.db.get_screenshot(screenshot_id)
            notify_published(
                platform,
                filename=shot.path if shot else str(screenshot_id),
                media_type=media_type,
                enabled=self.config.desktop_notifications,
                dry_run=self.config.dry_run,
            )
            if platform == "instagram" and media_type == "video" and remote_id:
                self._announce_video_link_if_needed(screenshot_id)
            return

        if ambiguous:
            self.db.update_publication(
                screenshot_id,
                platform,
                status="unknown",
                last_error=error or "ambiguous outcome",
                remote_id=remote_id,
                container_ids=container_ids,
                staged_keys=staged_keys,
                increment_attempts=True,
                next_retry_at=next_retry_timestamp(attempts + 1, retry_after=retry_after),
            )
            log.warning(
                "Ambiguous %s publish for screenshot_id=%s; marked unknown (no fallback)",
                platform,
                screenshot_id,
            )
            return

        if (
            platform == "discord"
            and media_type == "video"
            and error.startswith("oversized:")
        ):
            # Do not upload/compress oversized videos — wait for Instagram permalink.
            self.db.update_publication(
                screenshot_id,
                platform,
                status="awaiting_instagram_link",
                last_error=error,
                increment_attempts=True,
                next_retry_at=0.0,
            )
            log.info(
                "Discord awaiting Instagram link for oversized video screenshot_id=%s",
                screenshot_id,
            )
            return

        if auth_error or error.startswith("oversized:") or error.startswith("status permanent:"):
            status = "failed"
            next_at = 0.0
        elif error.startswith("rate_limit:") or (retry_after and not ambiguous):
            # Rate limits during active publish → retry later without losing container ids
            status = "retry_pending" if not ambiguous else "unknown"
            next_at = next_retry_timestamp(
                attempts + 1,
                retry_after=retry_after or 60.0,
            )
        else:
            status = "retry_pending"
            next_at = next_retry_timestamp(attempts + 1, retry_after=retry_after)

        self.db.update_publication(
            screenshot_id,
            platform,
            status=status,
            last_error=error,
            container_ids=container_ids,
            staged_keys=staged_keys,
            increment_attempts=True,
            next_retry_at=next_at,
        )
        log.error("Failed %s screenshot_id=%s: %s", platform, screenshot_id, error)


def pid_is_running() -> int | None:
    path = pid_path()
    if not path.is_file():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid
