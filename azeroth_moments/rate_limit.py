"""Publishing rate limit helpers backed by SQLite publish_events."""

from __future__ import annotations

from azeroth_moments.db import Database


class RateLimiter:
    def __init__(self, db: Database, max_per_hour: int) -> None:
        self.db = db
        self.max_per_hour = max_per_hour

    def can_publish(self, platform: str | None = None) -> bool:
        return self.db.publishes_in_last_hour(platform) < self.max_per_hour

    def remaining(self, platform: str | None = None) -> int:
        return max(0, self.max_per_hour - self.db.publishes_in_last_hour(platform))

    def record(self, platform: str) -> None:
        self.db.record_publish_event(platform)
