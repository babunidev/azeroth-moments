"""Announce Instagram video permalinks to Discord after confirmed publish."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from azeroth_moments.config import Config
from azeroth_moments.db import Database, Publication, Screenshot
from azeroth_moments.meta_api_gate import InstagramApiGate
from azeroth_moments.notify import notify_published
from azeroth_moments.publishers.discord import DiscordPublisher
from azeroth_moments.publishers.instagram import InstagramPublisher
from azeroth_moments.retry import next_retry_timestamp

log = logging.getLogger(__name__)


@dataclass
class AnnounceResult:
    screenshot_id: int
    ok: bool
    skipped: bool = False
    message: str = ""
    permalink: str = ""
    discord_message_id: str = ""


def format_video_link_message(*, author: str, permalink: str) -> str:
    display = author.strip() or "BabuniGaming"
    # Preserve brand capitalization when author is the handle
    if display.lower() == "babunigaming":
        display = "BabuniGaming"
    return (
        f"**New video from {display}!**\n\n"
        f"Watch on Instagram:\n"
        f"{permalink}"
    )


class InstagramDiscordLinkAnnouncer:
    """Fetch official Instagram permalinks and post them to Discord once."""

    def __init__(
        self,
        config: Config,
        db: Database,
        *,
        instagram: InstagramPublisher | None = None,
        discord: DiscordPublisher | None = None,
        api_gate: InstagramApiGate | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.api_gate = api_gate or InstagramApiGate(db)
        self.instagram = instagram or InstagramPublisher(
            config, db, api_gate=self.api_gate
        )
        self.discord = discord or DiscordPublisher(config)

    def process_pending(self, *, now: float | None = None) -> list[AnnounceResult]:
        now = now if now is not None else time.time()
        results: list[AnnounceResult] = []
        for shot, ig_pub in self.db.list_pending_video_link_announcements(now=now):
            results.append(self.announce_for(shot, ig_pub, now=now))
        return results

    def announce_for(
        self,
        shot: Screenshot,
        ig_pub: Publication,
        *,
        now: float | None = None,
    ) -> AnnounceResult:
        now = now if now is not None else time.time()
        if (shot.media_type or "image") != "video":
            return AnnounceResult(
                screenshot_id=shot.id,
                ok=True,
                skipped=True,
                message="not a video",
            )
        if ig_pub.status != "published" or not ig_pub.remote_id:
            return AnnounceResult(
                screenshot_id=shot.id,
                ok=False,
                skipped=True,
                message="Instagram not confirmed published",
            )

        existing = self.db.get_link_announcement(shot.id)
        if existing and existing.get("discord_message_id"):
            return AnnounceResult(
                screenshot_id=shot.id,
                ok=True,
                skipped=True,
                message="already announced",
                permalink=str(existing.get("permalink") or ""),
                discord_message_id=str(existing["discord_message_id"]),
            )

        if not self.config.discord_enabled:
            return AnnounceResult(
                screenshot_id=shot.id,
                ok=False,
                skipped=True,
                message="Discord disabled",
            )

        permalink = (ig_pub.permalink or "").strip()
        if existing and existing.get("permalink"):
            permalink = str(existing["permalink"]).strip() or permalink

        if not permalink:
            if self.api_gate.blocked(now=now):
                remaining = self.api_gate.remaining(now=now)
                self.db.upsert_link_announcement(
                    shot.id,
                    instagram_media_id=ig_pub.remote_id,
                    next_retry_at=now + remaining,
                    last_error=f"rate_limit: cooldown {remaining:.0f}s",
                    increment_attempts=True,
                )
                return AnnounceResult(
                    screenshot_id=shot.id,
                    ok=False,
                    message=f"cooldown {remaining:.0f}s",
                )

            fetched = self.instagram.fetch_permalink(ig_pub.remote_id)
            if not fetched.ok:
                retry_after = fetched.retry_after or 60.0
                if fetched.auth_error:
                    self.db.upsert_link_announcement(
                        shot.id,
                        instagram_media_id=ig_pub.remote_id,
                        next_retry_at=0.0,
                        last_error=fetched.error,
                        increment_attempts=True,
                    )
                    return AnnounceResult(
                        screenshot_id=shot.id,
                        ok=False,
                        message=fetched.error,
                    )
                self.db.upsert_link_announcement(
                    shot.id,
                    instagram_media_id=ig_pub.remote_id,
                    next_retry_at=next_retry_timestamp(
                        max((existing or {}).get("attempts", 0), 1),
                        retry_after=retry_after,
                        now=now,
                    ),
                    last_error=fetched.error,
                    increment_attempts=True,
                )
                return AnnounceResult(
                    screenshot_id=shot.id,
                    ok=False,
                    message=fetched.error or "permalink unavailable",
                )
            permalink = (fetched.permalink or fetched.remote_id or "").strip()
            self.db.update_publication(
                shot.id,
                "instagram",
                permalink=permalink,
            )

        if self.config.dry_run:
            self.db.upsert_link_announcement(
                shot.id,
                instagram_media_id=ig_pub.remote_id,
                permalink=permalink,
                discord_message_id="dry-run",
                announced_at=now,
                next_retry_at=0.0,
                last_error="",
            )
            return AnnounceResult(
                screenshot_id=shot.id,
                ok=True,
                message="dry-run",
                permalink=permalink,
                discord_message_id="dry-run",
            )

        content = format_video_link_message(
            author=self.config.author, permalink=permalink
        )
        sent = self.discord.publish_text(content)
        if not sent.ok:
            retry_after = sent.retry_after or 30.0
            self.db.upsert_link_announcement(
                shot.id,
                instagram_media_id=ig_pub.remote_id,
                permalink=permalink,
                next_retry_at=next_retry_timestamp(1, retry_after=retry_after, now=now),
                last_error=sent.error,
                increment_attempts=True,
            )
            return AnnounceResult(
                screenshot_id=shot.id,
                ok=False,
                message=sent.error,
                permalink=permalink,
            )

        self.db.upsert_link_announcement(
            shot.id,
            instagram_media_id=ig_pub.remote_id,
            permalink=permalink,
            discord_message_id=sent.remote_id,
            announced_at=now,
            next_retry_at=0.0,
            last_error="",
        )
        # If Discord was waiting for the Instagram link (oversized video), mark published
        disc = self.db.get_publication(shot.id, "discord")
        if disc and disc.status in {
            "awaiting_instagram_link",
            "failed",
            "pending",
            "retry_pending",
        }:
            # Only auto-complete failed-oversized / awaiting rows — not successful file uploads
            if disc.status == "awaiting_instagram_link" or (
                disc.status == "failed" and (disc.last_error or "").startswith("oversized:")
            ):
                self.db.update_publication(
                    shot.id,
                    "discord",
                    status="published",
                    remote_id=sent.remote_id,
                    last_error="",
                    permalink=permalink,
                )
        log.info(
            "Announced Instagram video link screenshot_id=%s permalink=%s discord_id=%s",
            shot.id,
            permalink,
            sent.remote_id,
        )
        notify_published(
            "discord",
            filename=shot.path,
            media_type=shot.media_type or "video",
            enabled=self.config.desktop_notifications,
            dry_run=self.config.dry_run,
        )
        return AnnounceResult(
            screenshot_id=shot.id,
            ok=True,
            message="announced",
            permalink=permalink,
            discord_message_id=sent.remote_id,
        )
