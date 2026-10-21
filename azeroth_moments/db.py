"""SQLite persistence for screenshots, publications, and control state."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from azeroth_moments.paths import db_path

PLATFORMS = ("discord", "instagram")

STATUSES = (
    "pending",
    "processing",
    "published",
    "retry_pending",
    "failed",
    "unknown",
    "baseline_skipped",
    "awaiting_instagram_link",
)


@dataclass
class Screenshot:
    id: int
    path: str
    sha256: str
    discovered_at: float
    author: str
    original_width: int
    original_height: int
    original_aspect_ratio: float
    classified_shape: str
    ingest_reason: str
    media_type: str = "image"


@dataclass
class Publication:
    id: int
    screenshot_id: int
    platform: str
    status: str
    attempts: int
    last_error: str
    remote_id: str
    container_ids: str
    staged_keys: str
    next_retry_at: float
    updated_at: float
    permalink: str = ""


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS screenshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL UNIQUE,
                    discovered_at REAL NOT NULL,
                    author TEXT NOT NULL,
                    original_width INTEGER NOT NULL,
                    original_height INTEGER NOT NULL,
                    original_aspect_ratio REAL NOT NULL,
                    classified_shape TEXT NOT NULL DEFAULT '',
                    ingest_reason TEXT NOT NULL,
                    media_type TEXT NOT NULL DEFAULT 'image'
                );

                CREATE TABLE IF NOT EXISTS publications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    screenshot_id INTEGER NOT NULL REFERENCES screenshots(id),
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    remote_id TEXT NOT NULL DEFAULT '',
                    container_ids TEXT NOT NULL DEFAULT '[]',
                    staged_keys TEXT NOT NULL DEFAULT '[]',
                    next_retry_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    UNIQUE(screenshot_id, platform)
                );

                CREATE TABLE IF NOT EXISTS transforms (
                    screenshot_id INTEGER PRIMARY KEY REFERENCES screenshots(id),
                    instagram_mode TEXT NOT NULL DEFAULT '',
                    panel_count INTEGER NOT NULL DEFAULT 0,
                    panel_output_dims TEXT NOT NULL DEFAULT '[]',
                    padding_applied INTEGER NOT NULL DEFAULT 0,
                    fallback_used INTEGER NOT NULL DEFAULT 0,
                    validation_ok INTEGER NOT NULL DEFAULT 0,
                    decision_summary TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tokens (
                    provider TEXT PRIMARY KEY,
                    expires_at TEXT NOT NULL DEFAULT '',
                    refreshed_at REAL NOT NULL DEFAULT 0,
                    extra_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS control (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS publish_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    published_at REAL NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO control(key, value) VALUES ('paused', '0')"
            )
            conn.execute(
                "INSERT OR IGNORE INTO control(key, value) VALUES ('baseline_done', '0')"
            )
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(screenshots)").fetchall()
            }
            if "media_type" not in cols:
                conn.execute(
                    "ALTER TABLE screenshots ADD COLUMN media_type TEXT NOT NULL DEFAULT 'image'"
                )
            pub_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(publications)").fetchall()
            }
            if "permalink" not in pub_cols:
                conn.execute(
                    "ALTER TABLE publications ADD COLUMN permalink TEXT NOT NULL DEFAULT ''"
                )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS discord_link_announcements (
                    screenshot_id INTEGER PRIMARY KEY REFERENCES screenshots(id),
                    instagram_media_id TEXT NOT NULL DEFAULT '',
                    permalink TEXT NOT NULL,
                    discord_message_id TEXT NOT NULL DEFAULT '',
                    announced_at REAL NOT NULL DEFAULT 0,
                    next_retry_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0
                );
                """
            )

    def get_control(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM control WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_control(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO control(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def is_paused(self) -> bool:
        return self.get_control("paused", "0") == "1"

    def set_paused(self, paused: bool) -> None:
        self.set_control("paused", "1" if paused else "0")

    def baseline_done(self) -> bool:
        return self.get_control("baseline_done", "0") == "1"

    def mark_baseline_done(self) -> None:
        self.set_control("baseline_done", "1")

    def get_by_sha(self, sha256: str) -> Screenshot | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM screenshots WHERE sha256 = ?", (sha256,)
            ).fetchone()
            return _screenshot_from_row(row) if row else None

    def insert_screenshot(
        self,
        *,
        path: str,
        sha256: str,
        author: str,
        width: int,
        height: int,
        aspect: float,
        shape: str,
        ingest_reason: str,
        media_type: str = "image",
        platforms: tuple[str, ...] = PLATFORMS,
        initial_status: str = "pending",
    ) -> int:
        now = time.time()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO screenshots(
                    path, sha256, discovered_at, author,
                    original_width, original_height, original_aspect_ratio,
                    classified_shape, ingest_reason, media_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    path,
                    sha256,
                    now,
                    author,
                    width,
                    height,
                    aspect,
                    shape,
                    ingest_reason,
                    media_type,
                ),
            )
            screenshot_id = int(cur.lastrowid)
            for platform in platforms:
                status = (
                    "baseline_skipped"
                    if ingest_reason == "baseline_skipped"
                    else initial_status
                )
                conn.execute(
                    """
                    INSERT INTO publications(
                        screenshot_id, platform, status, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (screenshot_id, platform, status, now),
                )
            return screenshot_id

    def update_shape(self, screenshot_id: int, shape: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE screenshots SET classified_shape = ? WHERE id = ?",
                (shape, screenshot_id),
            )

    def save_transform(
        self,
        screenshot_id: int,
        *,
        instagram_mode: str,
        panel_count: int,
        panel_output_dims: list[Any],
        padding_applied: bool,
        fallback_used: bool,
        validation_ok: bool,
        decision_summary: str,
    ) -> None:
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO transforms(
                    screenshot_id, instagram_mode, panel_count, panel_output_dims,
                    padding_applied, fallback_used, validation_ok, decision_summary, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(screenshot_id) DO UPDATE SET
                    instagram_mode = excluded.instagram_mode,
                    panel_count = excluded.panel_count,
                    panel_output_dims = excluded.panel_output_dims,
                    padding_applied = excluded.padding_applied,
                    fallback_used = excluded.fallback_used,
                    validation_ok = excluded.validation_ok,
                    decision_summary = excluded.decision_summary,
                    updated_at = excluded.updated_at
                """,
                (
                    screenshot_id,
                    instagram_mode,
                    panel_count,
                    json.dumps(panel_output_dims),
                    int(padding_applied),
                    int(fallback_used),
                    int(validation_ok),
                    decision_summary,
                    now,
                ),
            )

    def get_screenshot(self, screenshot_id: int) -> Screenshot | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM screenshots WHERE id = ?", (screenshot_id,)
            ).fetchone()
            return _screenshot_from_row(row) if row else None

    def get_publication(self, screenshot_id: int, platform: str) -> Publication | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM publications WHERE screenshot_id = ? AND platform = ?",
                (screenshot_id, platform),
            ).fetchone()
            return _publication_from_row(row) if row else None

    def list_unknown_detail(self, platform: str | None = None) -> list[dict[str, Any]]:
        """Inspect-friendly unknown publications (no API calls)."""
        with self.connect() as conn:
            if platform:
                rows = conn.execute(
                    """
                    SELECT s.id AS screenshot_id, p.id AS publication_id,
                           s.path, s.media_type, s.classified_shape,
                           p.platform, p.status, p.attempts, p.last_error, p.remote_id,
                           p.container_ids, p.staged_keys, p.next_retry_at, p.updated_at
                    FROM publications p
                    JOIN screenshots s ON s.id = p.screenshot_id
                    WHERE p.status = 'unknown' AND p.platform = ?
                    ORDER BY p.updated_at DESC
                    """,
                    (platform,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT s.id AS screenshot_id, p.id AS publication_id,
                           s.path, s.media_type, s.classified_shape,
                           p.platform, p.status, p.attempts, p.last_error, p.remote_id,
                           p.container_ids, p.staged_keys, p.next_retry_at, p.updated_at
                    FROM publications p
                    JOIN screenshots s ON s.id = p.screenshot_id
                    WHERE p.status = 'unknown'
                    ORDER BY p.updated_at DESC
                    """
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["filename"] = Path(str(d.get("path") or "")).name
                out.append(d)
            return out

    def update_publication(
        self,
        screenshot_id: int,
        platform: str,
        *,
        status: str | None = None,
        last_error: str | None = None,
        remote_id: str | None = None,
        container_ids: list[str] | None = None,
        staged_keys: list[str] | None = None,
        permalink: str | None = None,
        increment_attempts: bool = False,
        next_retry_at: float | None = None,
    ) -> None:
        pub = self.get_publication(screenshot_id, platform)
        if pub is None:
            raise KeyError(f"No publication for {screenshot_id}/{platform}")
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE publications SET
                    status = ?,
                    attempts = ?,
                    last_error = ?,
                    remote_id = ?,
                    container_ids = ?,
                    staged_keys = ?,
                    permalink = ?,
                    next_retry_at = ?,
                    updated_at = ?
                WHERE screenshot_id = ? AND platform = ?
                """,
                (
                    status if status is not None else pub.status,
                    pub.attempts + (1 if increment_attempts else 0),
                    last_error if last_error is not None else pub.last_error,
                    remote_id if remote_id is not None else pub.remote_id,
                    json.dumps(container_ids)
                    if container_ids is not None
                    else pub.container_ids,
                    json.dumps(staged_keys)
                    if staged_keys is not None
                    else pub.staged_keys,
                    permalink if permalink is not None else pub.permalink,
                    next_retry_at if next_retry_at is not None else pub.next_retry_at,
                    now,
                    screenshot_id,
                    platform,
                ),
            )

    def claim_next_work(
        self, platform: str, now: float | None = None
    ) -> tuple[Screenshot, Publication] | None:
        now = now or time.time()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT s.*, p.id AS pub_id, p.platform, p.status, p.attempts,
                       p.last_error, p.remote_id, p.container_ids, p.staged_keys,
                       p.next_retry_at, p.updated_at, p.permalink
                FROM publications p
                JOIN screenshots s ON s.id = p.screenshot_id
                WHERE p.platform = ?
                  AND p.status IN ('pending', 'retry_pending')
                  AND p.next_retry_at <= ?
                ORDER BY s.discovered_at ASC
                LIMIT 1
                """,
                (platform, now),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE publications SET status = 'processing', updated_at = ?
                WHERE id = ? AND status IN ('pending', 'retry_pending')
                """,
                (now, row["pub_id"]),
            )
            if conn.execute(
                "SELECT changes()"
            ).fetchone()[0] == 0:
                return None
            shot = _screenshot_from_row(row)
            pub = Publication(
                id=row["pub_id"],
                screenshot_id=row["id"],
                platform=row["platform"],
                status="processing",
                attempts=row["attempts"],
                last_error=row["last_error"],
                remote_id=row["remote_id"],
                container_ids=row["container_ids"],
                staged_keys=row["staged_keys"],
                next_retry_at=row["next_retry_at"],
                updated_at=now,
                permalink=row["permalink"] if "permalink" in row.keys() else "",
            )
            return shot, pub

    def list_unknown(self, platform: str) -> list[tuple[Screenshot, Publication]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.*, p.id AS pub_id, p.platform, p.status, p.attempts,
                       p.last_error, p.remote_id, p.container_ids, p.staged_keys,
                       p.next_retry_at, p.updated_at, p.permalink
                FROM publications p
                JOIN screenshots s ON s.id = p.screenshot_id
                WHERE p.platform = ? AND p.status = 'unknown'
                ORDER BY s.discovered_at ASC
                """,
                (platform,),
            ).fetchall()
            result = []
            for row in rows:
                shot = _screenshot_from_row(row)
                pub = Publication(
                    id=row["pub_id"],
                    screenshot_id=row["id"],
                    platform=row["platform"],
                    status=row["status"],
                    attempts=row["attempts"],
                    last_error=row["last_error"],
                    remote_id=row["remote_id"],
                    container_ids=row["container_ids"],
                    staged_keys=row["staged_keys"],
                    next_retry_at=row["next_retry_at"],
                    updated_at=row["updated_at"],
                    permalink=row["permalink"] if "permalink" in row.keys() else "",
                )
                result.append((shot, pub))
            return result

    def mark_retry_eligible_failed(self) -> int:
        """Move failed/retryable rows back to retry_pending for manual retry."""
        now = time.time()
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE publications
                SET status = 'retry_pending', next_retry_at = 0, updated_at = ?
                WHERE status IN ('failed', 'retry_pending')
                  AND last_error NOT LIKE 'auth:%'
                  AND last_error NOT LIKE 'oversized:%'
                """,
                (now,),
            )
            return cur.rowcount

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.path, s.sha256, s.author, s.classified_shape,
                       p.platform, p.status, p.attempts, p.last_error,
                       p.remote_id, p.updated_at
                FROM publications p
                JOIN screenshots s ON s.id = p.screenshot_id
                ORDER BY p.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def counts_by_status(self) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS c FROM publications GROUP BY status"
            ).fetchall()
            return {r["status"]: r["c"] for r in rows}

    def record_publish_event(self, platform: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO publish_events(platform, published_at) VALUES (?, ?)",
                (platform, time.time()),
            )

    def publishes_in_last_hour(self, platform: str | None = None) -> int:
        cutoff = time.time() - 3600
        with self.connect() as conn:
            if platform:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM publish_events
                    WHERE platform = ? AND published_at >= ?
                    """,
                    (platform, cutoff),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM publish_events
                    WHERE published_at >= ?
                    """,
                    (cutoff,),
                ).fetchone()
            return int(row["c"])

    def set_token_meta(self, provider: str, expires_at: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tokens(provider, expires_at, refreshed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    expires_at = excluded.expires_at,
                    refreshed_at = excluded.refreshed_at
                """,
                (provider, expires_at, time.time()),
            )

    def get_token_meta(self, provider: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tokens WHERE provider = ?", (provider,)
            ).fetchone()
            return dict(row) if row else None

    def get_link_announcement(self, screenshot_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM discord_link_announcements WHERE screenshot_id = ?",
                (screenshot_id,),
            ).fetchone()
            return dict(row) if row else None

    def upsert_link_announcement(
        self,
        screenshot_id: int,
        *,
        instagram_media_id: str = "",
        permalink: str = "",
        discord_message_id: str = "",
        announced_at: float | None = None,
        next_retry_at: float | None = None,
        last_error: str | None = None,
        increment_attempts: bool = False,
    ) -> None:
        existing = self.get_link_announcement(screenshot_id)
        now = time.time()
        with self.connect() as conn:
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO discord_link_announcements(
                        screenshot_id, instagram_media_id, permalink,
                        discord_message_id, announced_at, next_retry_at,
                        last_error, attempts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        screenshot_id,
                        instagram_media_id,
                        permalink,
                        discord_message_id,
                        announced_at or 0.0,
                        next_retry_at or 0.0,
                        last_error or "",
                        1 if increment_attempts else 0,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE discord_link_announcements SET
                        instagram_media_id = ?,
                        permalink = ?,
                        discord_message_id = ?,
                        announced_at = ?,
                        next_retry_at = ?,
                        last_error = ?,
                        attempts = ?
                    WHERE screenshot_id = ?
                    """,
                    (
                        instagram_media_id
                        if instagram_media_id
                        else existing["instagram_media_id"],
                        permalink if permalink else existing["permalink"],
                        discord_message_id
                        if discord_message_id
                        else existing["discord_message_id"],
                        announced_at
                        if announced_at is not None
                        else existing["announced_at"],
                        next_retry_at
                        if next_retry_at is not None
                        else existing["next_retry_at"],
                        last_error if last_error is not None else existing["last_error"],
                        int(existing["attempts"]) + (1 if increment_attempts else 0),
                        screenshot_id,
                    ),
                )

    def list_pending_video_link_announcements(
        self, *, now: float | None = None
    ) -> list[tuple[Screenshot, Publication]]:
        """Videos with Instagram published but no successful Discord link announce."""
        now = now if now is not None else time.time()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.*, p.id AS pub_id, p.platform, p.status, p.attempts,
                       p.last_error, p.remote_id, p.container_ids, p.staged_keys,
                       p.next_retry_at, p.updated_at, p.permalink
                FROM screenshots s
                JOIN publications p
                  ON p.screenshot_id = s.id AND p.platform = 'instagram'
                LEFT JOIN discord_link_announcements a
                  ON a.screenshot_id = s.id
                WHERE s.media_type = 'video'
                  AND p.status = 'published'
                  AND p.remote_id != ''
                  AND (
                        a.screenshot_id IS NULL
                     OR a.discord_message_id = ''
                     OR a.discord_message_id IS NULL
                  )
                  AND COALESCE(a.next_retry_at, 0) <= ?
                ORDER BY s.id ASC
                """,
                (now,),
            ).fetchall()
            out: list[tuple[Screenshot, Publication]] = []
            for row in rows:
                shot = _screenshot_from_row(row)
                pub = Publication(
                    id=row["pub_id"],
                    screenshot_id=row["id"],
                    platform=row["platform"],
                    status=row["status"],
                    attempts=row["attempts"],
                    last_error=row["last_error"],
                    remote_id=row["remote_id"],
                    container_ids=row["container_ids"],
                    staged_keys=row["staged_keys"],
                    next_retry_at=row["next_retry_at"],
                    updated_at=row["updated_at"],
                    permalink=row["permalink"] if "permalink" in row.keys() else "",
                )
                out.append((shot, pub))
            return out


def _screenshot_from_row(row: sqlite3.Row) -> Screenshot:
    keys = set(row.keys())
    media_type = row["media_type"] if "media_type" in keys else "image"
    return Screenshot(
        id=row["id"],
        path=row["path"],
        sha256=row["sha256"],
        discovered_at=row["discovered_at"],
        author=row["author"],
        original_width=row["original_width"],
        original_height=row["original_height"],
        original_aspect_ratio=row["original_aspect_ratio"],
        classified_shape=row["classified_shape"],
        ingest_reason=row["ingest_reason"],
        media_type=media_type or "image",
    )


def _publication_from_row(row: sqlite3.Row) -> Publication:
    keys = set(row.keys())
    return Publication(
        id=row["id"],
        screenshot_id=row["screenshot_id"],
        platform=row["platform"],
        status=row["status"],
        attempts=row["attempts"],
        last_error=row["last_error"],
        remote_id=row["remote_id"],
        container_ids=row["container_ids"],
        staged_keys=row["staged_keys"],
        next_retry_at=row["next_retry_at"],
        updated_at=row["updated_at"],
        permalink=row["permalink"] if "permalink" in keys else "",
    )
