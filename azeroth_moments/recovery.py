"""Safe Instagram recovery for unknown publications.

Operates on existing SQLite rows and container IDs. Never renames source files.
Never bypasses the shared InstagramApiGate. Discord is untouched.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from azeroth_moments.config import Config
from azeroth_moments.db import Database, Publication, Screenshot
from azeroth_moments.images.transform import prepare_instagram_assets
from azeroth_moments.meta_api_gate import InstagramApiGate
from azeroth_moments.publishers.base import PublishResult
from azeroth_moments.publishers.instagram import InstagramPublisher
from azeroth_moments.videos.transform import prepare_instagram_video_assets

log = logging.getLogger(__name__)

INTENT_PREFIX = "instagram_recovery:"


@dataclass
class RecoveryIntent:
    screenshot_id: int
    platform: str = "instagram"
    confirm_publish: bool = False
    confirm_rebuild: bool = False
    queued_at: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> RecoveryIntent:
        data = json.loads(raw)
        return cls(
            screenshot_id=int(data["screenshot_id"]),
            platform=str(data.get("platform") or "instagram"),
            confirm_publish=bool(data.get("confirm_publish")),
            confirm_rebuild=bool(data.get("confirm_rebuild")),
            queued_at=float(data.get("queued_at") or 0),
        )


@dataclass
class RecoveryReport:
    screenshot_id: int
    publication_id: int
    platform: str
    filename: str
    action: str
    message: str
    status_after: str
    parent_status: str = ""
    container_ids: list[str] = field(default_factory=list)
    remote_id: str = ""
    cooldown_remaining: float = 0.0
    queued: bool = False
    ok: bool = False
    needs_confirm_publish: bool = False
    needs_confirm_rebuild: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class InstagramRecovery:
    def __init__(
        self,
        config: Config,
        db: Database,
        *,
        publisher: InstagramPublisher | None = None,
        api_gate: InstagramApiGate | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.api_gate = api_gate or InstagramApiGate(db)
        self.publisher = publisher or InstagramPublisher(
            config, db, api_gate=self.api_gate
        )

    # --- intent queue (survives restarts) ---------------------------------

    def save_intent(self, intent: RecoveryIntent) -> None:
        intent.queued_at = intent.queued_at or time.time()
        self.db.set_control(f"{INTENT_PREFIX}{intent.screenshot_id}", intent.to_json())

    def get_intent(self, screenshot_id: int) -> RecoveryIntent | None:
        raw = self.db.get_control(f"{INTENT_PREFIX}{screenshot_id}", "")
        if not raw:
            return None
        try:
            return RecoveryIntent.from_json(raw)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def clear_intent(self, screenshot_id: int) -> None:
        self.db.set_control(f"{INTENT_PREFIX}{screenshot_id}", "")

    def list_intents(self) -> list[RecoveryIntent]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM control WHERE key LIKE ?",
                (f"{INTENT_PREFIX}%",),
            ).fetchall()
        out: list[RecoveryIntent] = []
        for row in rows:
            raw = row["value"] or ""
            if not raw.strip():
                continue
            try:
                out.append(RecoveryIntent.from_json(raw))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        return out

    # --- public entry points ---------------------------------------------

    def retry(
        self,
        screenshot_id: int,
        *,
        platform: str = "instagram",
        confirm_publish: bool = False,
        confirm_rebuild: bool = False,
        run_now: bool = True,
    ) -> RecoveryReport:
        if platform != "instagram":
            shot = self.db.get_screenshot(screenshot_id)
            pub = self.db.get_publication(screenshot_id, platform)
            return RecoveryReport(
                screenshot_id=screenshot_id,
                publication_id=pub.id if pub else 0,
                platform=platform,
                filename=Path(shot.path).name if shot else "",
                action="refused",
                message=(
                    f"Recovery for platform={platform} is not supported. "
                    "Discord stays independent; Instagram recovery does not retry Discord."
                ),
                status_after=pub.status if pub else "missing",
            )

        shot = self.db.get_screenshot(screenshot_id)
        pub = self.db.get_publication(screenshot_id, "instagram")
        if shot is None or pub is None:
            return RecoveryReport(
                screenshot_id=screenshot_id,
                publication_id=0,
                platform="instagram",
                filename="",
                action="missing",
                message=f"No Instagram publication for screenshot_id={screenshot_id}",
                status_after="missing",
            )

        intent = RecoveryIntent(
            screenshot_id=screenshot_id,
            platform="instagram",
            confirm_publish=confirm_publish,
            confirm_rebuild=confirm_rebuild,
            queued_at=time.time(),
        )
        self.save_intent(intent)

        remaining = self.api_gate.remaining()
        if remaining > 0:
            self.db.update_publication(
                screenshot_id,
                "instagram",
                status="unknown" if pub.status == "unknown" else pub.status,
                last_error=(
                    f"recovery queued: Instagram API cooldown {remaining:.0f}s "
                    f"({self.api_gate.reason() or 'active'})"
                ),
                container_ids=json.loads(pub.container_ids or "[]"),
                next_retry_at=time.time() + remaining,
            )
            return RecoveryReport(
                screenshot_id=screenshot_id,
                publication_id=pub.id,
                platform="instagram",
                filename=Path(shot.path).name,
                action="queued_cooldown",
                message=(
                    f"Instagram API cooldown active ({remaining:.0f}s remaining). "
                    "Recovery queued; worker will resume when clear."
                ),
                status_after="unknown",
                container_ids=json.loads(pub.container_ids or "[]"),
                cooldown_remaining=remaining,
                queued=True,
                ok=True,
            )

        if not run_now:
            self.db.update_publication(
                screenshot_id,
                "instagram",
                next_retry_at=0.0,
                last_error="recovery queued",
                container_ids=json.loads(pub.container_ids or "[]"),
            )
            return RecoveryReport(
                screenshot_id=screenshot_id,
                publication_id=pub.id,
                platform="instagram",
                filename=Path(shot.path).name,
                action="queued",
                message="Recovery queued for the worker.",
                status_after=pub.status,
                container_ids=json.loads(pub.container_ids or "[]"),
                queued=True,
                ok=True,
            )

        return self.execute(intent)

    def process_queued(self, *, now: float | None = None) -> list[RecoveryReport]:
        """Worker hook: run due recovery intents (honors cooldown / access block)."""
        now = now if now is not None else time.time()
        reports: list[RecoveryReport] = []
        access_blocked = self.api_gate.access_blocked()
        remaining = self.api_gate.remaining(now=now)
        for intent in self.list_intents():
            pub = self.db.get_publication(intent.screenshot_id, "instagram")
            if pub is None:
                self.clear_intent(intent.screenshot_id)
                continue
            if pub.status == "published":
                self.clear_intent(intent.screenshot_id)
                continue
            if pub.next_retry_at and pub.next_retry_at > now:
                continue
            if access_blocked:
                # Preserve failed/unknown; do not probe Meta while blocked.
                self.db.update_publication(
                    intent.screenshot_id,
                    "instagram",
                    status=pub.status if pub.status in {"failed", "unknown"} else "failed",
                    last_error=(
                        f"auth: API access blocked ({self.api_gate.reason()})"
                    ),
                    container_ids=json.loads(pub.container_ids or "[]"),
                    next_retry_at=now + remaining,
                )
                continue
            if remaining > 0:
                self.db.update_publication(
                    intent.screenshot_id,
                    "instagram",
                    status="unknown",
                    last_error=(
                        f"recovery queued: Instagram API cooldown {remaining:.0f}s"
                    ),
                    container_ids=json.loads(pub.container_ids or "[]"),
                    next_retry_at=now + remaining,
                )
                continue
            reports.append(self.execute(intent))
        return reports

    def execute(self, intent: RecoveryIntent) -> RecoveryReport:
        shot = self.db.get_screenshot(intent.screenshot_id)
        pub = self.db.get_publication(intent.screenshot_id, "instagram")
        if shot is None or pub is None:
            self.clear_intent(intent.screenshot_id)
            return RecoveryReport(
                screenshot_id=intent.screenshot_id,
                publication_id=0,
                platform="instagram",
                filename="",
                action="missing",
                message="Publication disappeared before recovery",
                status_after="missing",
            )

        container_ids = json.loads(pub.container_ids or "[]")
        filename = Path(shot.path).name
        base = dict(
            screenshot_id=shot.id,
            publication_id=pub.id,
            platform="instagram",
            filename=filename,
            container_ids=list(container_ids),
        )

        if pub.remote_id and pub.status == "published":
            self.clear_intent(shot.id)
            return RecoveryReport(
                **base,
                action="already_published",
                message="Already published",
                status_after="published",
                remote_id=pub.remote_id,
                ok=True,
            )

        if self.api_gate.access_blocked():
            defer = self.api_gate.remaining()
            self.db.update_publication(
                shot.id,
                "instagram",
                status=pub.status if pub.status in {"failed", "unknown"} else "failed",
                last_error=f"auth: API access blocked ({self.api_gate.reason()})",
                container_ids=container_ids,
                next_retry_at=time.time() + defer,
            )
            return RecoveryReport(
                **base,
                action="access_blocked",
                message=(
                    "Meta API access is blocked. Run `azeroth-moments test` after "
                    "restoring access in Meta Developer Dashboard, then retry."
                ),
                status_after=pub.status if pub.status in {"failed", "unknown"} else "failed",
                cooldown_remaining=defer,
                queued=True,
                ok=False,
            )

        remaining = self.api_gate.remaining()
        if remaining > 0:
            self.db.update_publication(
                shot.id,
                "instagram",
                status="unknown",
                last_error=f"recovery queued: cooldown {remaining:.0f}s",
                container_ids=container_ids,
                next_retry_at=time.time() + remaining,
            )
            return RecoveryReport(
                **base,
                action="queued_cooldown",
                message=f"Cooldownoldown {remaining:.0f}s; recovery remains queued",
                status_after="unknown",
                cooldown_remaining=remaining,
                queued=True,
                ok=True,
            )

        # Assess existing containers (no recreate)
        assessment = self._assess(shot, pub, container_ids)
        action = assessment["action"]

        if action == "mark_published":
            remote = str(assessment.get("remote_id") or container_ids[-1])
            self.db.update_publication(
                shot.id,
                "instagram",
                status="published",
                remote_id=remote,
                last_error="",
                container_ids=container_ids,
                next_retry_at=0.0,
            )
            self.clear_intent(shot.id)
            return RecoveryReport(
                **base,
                action="mark_published",
                message="Container already PUBLISHED; SQLite updated",
                status_after="published",
                parent_status="PUBLISHED",
                remote_id=remote,
                ok=True,
                details=assessment,
            )

        if action == "publish_parent":
            # FINISHED = ready, not yet published. Manual retry / queued recovery
            # is explicit user intent to resume — do not recreate containers.
            result = self.publisher.reconcile(
                container_ids,
                pub.remote_id,
                publish_if_finished=True,
            )
            return self._apply_publish_result(
                shot,
                pub,
                container_ids,
                result,
                parent_status="FINISHED",
                base=base,
            )

        if action == "wait_processing":
            retry_after = float(assessment.get("retry_after") or 60)
            self.db.update_publication(
                shot.id,
                "instagram",
                status="unknown",
                last_error=assessment.get("message") or "Container still processing",
                container_ids=container_ids,
                next_retry_at=time.time() + retry_after,
            )
            # Keep intent so worker retries
            return RecoveryReport(
                **base,
                action="wait_processing",
                message=str(assessment.get("message") or "Still processing"),
                status_after="unknown",
                parent_status=str(assessment.get("parent_status") or ""),
                queued=True,
                ok=True,
                details=assessment,
            )

        if action == "access_blocked":
            defer = self.api_gate.remaining() or float(
                assessment.get("retry_after") or 86400
            )
            # Keep prior status (failed/unknown); do not invent a publish outcome.
            self.db.update_publication(
                shot.id,
                "instagram",
                status=pub.status if pub.status in {"failed", "unknown"} else "failed",
                last_error=str(
                    assessment.get("message") or "auth: API access blocked"
                ),
                container_ids=container_ids,
                next_retry_at=time.time() + defer,
            )
            return RecoveryReport(
                **base,
                action="access_blocked",
                message=(
                    "Meta API access is blocked. Fix Instagram Login / developer "
                    "account access in Meta Developer Dashboard, then run "
                    "`azeroth-moments test` and retry. No Graph calls will be made "
                    "until access is verified."
                ),
                status_after=pub.status if pub.status in {"failed", "unknown"} else "failed",
                cooldown_remaining=defer,
                queued=True,
                ok=False,
                details=assessment,
            )

        if action == "rate_limited":
            cooldown = self.api_gate.remaining() or float(
                assessment.get("retry_after") or 300
            )
            self.db.update_publication(
                shot.id,
                "instagram",
                status="unknown",
                last_error=assessment.get("message") or "rate_limit",
                container_ids=container_ids,
                next_retry_at=time.time() + cooldown,
            )
            return RecoveryReport(
                **base,
                action="queued_cooldown",
                message=str(assessment.get("message") or "Rate limited"),
                status_after="unknown",
                cooldown_remaining=cooldown,
                queued=True,
                ok=True,
                details=assessment,
            )

        if action == "need_confirm_rebuild":
            if not intent.confirm_rebuild:
                self.db.update_publication(
                    shot.id,
                    "instagram",
                    status="unknown",
                    last_error=(
                        "Containers expired/error; rebuild requires "
                        "--confirm-rebuild (may create a new Instagram post)"
                    ),
                    container_ids=container_ids,
                    next_retry_at=time.time() + 3600,
                )
                self.clear_intent(shot.id)
                return RecoveryReport(
                    **base,
                    action="need_confirm_rebuild",
                    message=(
                        "Existing containers are expired or failed. "
                        "Re-run with --confirm-rebuild to rebuild from the original "
                        "file (explicit; risk of a new post if the old one went live)."
                    ),
                    status_after="unknown",
                    parent_status=str(assessment.get("parent_status") or "EXPIRED"),
                    needs_confirm_rebuild=True,
                    ok=True,
                    details=assessment,
                )
            return self._rebuild(shot, pub, container_ids, base=base)

        if action == "ambiguous":
            self.db.update_publication(
                shot.id,
                "instagram",
                status="unknown",
                last_error=assessment.get("message") or "ambiguous recovery",
                container_ids=container_ids,
                next_retry_at=time.time() + float(assessment.get("retry_after") or 3600),
            )
            self.clear_intent(shot.id)
            return RecoveryReport(
                **base,
                action="ambiguous",
                message=str(assessment.get("message") or "Ambiguous; not publishing"),
                status_after="unknown",
                parent_status=str(assessment.get("parent_status") or ""),
                ok=True,
                details=assessment,
            )

        if action == "failed":
            self.db.update_publication(
                shot.id,
                "instagram",
                status="failed",
                last_error=assessment.get("message") or "recovery failed",
                container_ids=container_ids,
                next_retry_at=0.0,
            )
            self.clear_intent(shot.id)
            return RecoveryReport(
                **base,
                action="failed",
                message=str(assessment.get("message") or "failed"),
                status_after="failed",
                parent_status=str(assessment.get("parent_status") or ""),
                details=assessment,
            )

        if action == "nothing_to_reconcile":
            if intent.confirm_rebuild:
                return self._rebuild(shot, pub, container_ids, base=base)
            self.clear_intent(shot.id)
            return RecoveryReport(
                **base,
                action="need_confirm_rebuild",
                message="No container IDs stored; --confirm-rebuild required to publish anew",
                status_after="unknown",
                needs_confirm_rebuild=True,
                ok=True,
            )

        self.clear_intent(shot.id)
        return RecoveryReport(
            **base,
            action="noop",
            message=str(assessment.get("message") or "No recovery action"),
            status_after=pub.status,
            details=assessment,
        )

    # --- internals -------------------------------------------------------

    def _assess(
        self,
        shot: Screenshot,
        pub: Publication,
        container_ids: list[str],
    ) -> dict[str, Any]:
        if pub.remote_id:
            return {
                "action": "mark_published",
                "remote_id": pub.remote_id,
                "message": "remote_id already present",
            }
        if not container_ids:
            return {
                "action": "nothing_to_reconcile",
                "message": "No Instagram container_ids on this publication",
            }

        # Prefer parent (last id); also sample unique children for expiry
        statuses = self.publisher.inspect_containers(container_ids)
        if statuses and all(
            bool(s.get("access_blocked"))
            or "api access blocked" in str(s.get("error", "")).lower()
            for s in statuses
        ):
            return {
                "action": "access_blocked",
                "message": statuses[0].get("error") or "API access blocked",
                "retry_after": self.api_gate.remaining() or 86400,
                "container_status": statuses,
            }
        if statuses and all(
            str(s.get("error", "")).startswith("rate_limit:") for s in statuses
        ):
            return {
                "action": "rate_limited",
                "message": statuses[0].get("error"),
                "retry_after": self.api_gate.remaining() or 300,
                "container_status": statuses,
            }

        parent_id = container_ids[-1]
        parent = next((s for s in statuses if s.get("container_id") == parent_id), None)
        if parent is None and statuses:
            parent = statuses[-1]

        if parent and parent.get("error"):
            err = str(parent.get("error"))
            if parent.get("access_blocked") or "api access blocked" in err.lower():
                return {
                    "action": "access_blocked",
                    "message": err,
                    "retry_after": self.api_gate.remaining() or 86400,
                    "container_status": statuses,
                }
            if "rate_limit" in err.lower():
                return {
                    "action": "rate_limited",
                    "message": err,
                    "retry_after": self.api_gate.remaining() or 300,
                    "container_status": statuses,
                }
            # Hard API error on parent — ambiguous unless clearly expired language
            return {
                "action": "ambiguous",
                "message": f"Could not read parent container: {err}",
                "parent_status": "",
                "retry_after": 3600,
                "container_status": statuses,
            }

        parent_status = str((parent or {}).get("status_code") or "").upper()
        if parent_status == "PUBLISHED":
            return {
                "action": "mark_published",
                "remote_id": str((parent or {}).get("id") or parent_id),
                "parent_status": parent_status,
                "container_status": statuses,
            }
        if parent_status == "FINISHED":
            # Ready to publish; FINISHED ≠ already posted. retry is user intent;
            # confirm_publish only required if we later tighten policy via flag.
            return {
                "action": "publish_parent",
                "parent_status": parent_status,
                "require_confirm": False,
                "message": "Parent FINISHED; safe to media_publish once",
                "container_status": statuses,
            }
        if parent_status in {"ERROR", "EXPIRED"}:
            return {
                "action": "need_confirm_rebuild",
                "parent_status": parent_status,
                "message": f"Parent container {parent_status}",
                "container_status": statuses,
            }
        if parent_status in {"IN_PROGRESS", "PUBLISHING", ""}:
            return {
                "action": "wait_processing",
                "parent_status": parent_status or "unknown",
                "message": f"Container status {parent_status or 'unknown'}",
                "retry_after": 120,
                "container_status": statuses,
            }

        # Any child expired while parent unknown → rebuild confirm
        child_states = [
            str(s.get("status_code") or "").upper()
            for s in statuses
            if s.get("container_id") != parent_id
        ]
        if any(st in {"EXPIRED", "ERROR"} for st in child_states):
            return {
                "action": "need_confirm_rebuild",
                "parent_status": parent_status,
                "message": "One or more child containers expired/failed",
                "container_status": statuses,
            }

        return {
            "action": "ambiguous",
            "parent_status": parent_status,
            "message": f"Unrecognized container status {parent_status!r}",
            "retry_after": 3600,
            "container_status": statuses,
        }

    def _apply_publish_result(
        self,
        shot: Screenshot,
        pub: Publication,
        container_ids: list[str],
        result: PublishResult,
        *,
        parent_status: str,
        base: dict[str, Any],
    ) -> RecoveryReport:
        if result.ok:
            self.db.update_publication(
                shot.id,
                "instagram",
                status="published",
                remote_id=result.remote_id,
                last_error="",
                container_ids=result.container_ids or container_ids,
                next_retry_at=0.0,
            )
            self.clear_intent(shot.id)
            return RecoveryReport(
                **base,
                action="published",
                message="media_publish succeeded",
                status_after="published",
                parent_status=parent_status,
                remote_id=result.remote_id,
                ok=True,
            )
        if result.auth_error:
            self.db.update_publication(
                shot.id,
                "instagram",
                status="failed",
                last_error=result.error,
                container_ids=container_ids,
                next_retry_at=0.0,
            )
            self.clear_intent(shot.id)
            return RecoveryReport(
                **base,
                action="failed",
                message=result.error,
                status_after="failed",
                parent_status=parent_status,
            )
        # rate limit / ambiguous — keep unknown + containers, keep or clear intent
        retry_after = result.retry_after or self.api_gate.remaining() or 300
        self.db.update_publication(
            shot.id,
            "instagram",
            status="unknown",
            last_error=result.error,
            container_ids=result.container_ids or container_ids,
            next_retry_at=time.time() + retry_after,
        )
        queued = bool(result.ambiguous or (result.error or "").startswith("rate_limit:"))
        if not queued:
            self.clear_intent(shot.id)
        return RecoveryReport(
            **base,
            action="queued_cooldown" if "rate_limit" in (result.error or "") else "ambiguous",
            message=result.error or "ambiguous publish",
            status_after="unknown",
            parent_status=parent_status,
            cooldown_remaining=self.api_gate.remaining(),
            queued=queued,
            ok=True,
        )

    def _rebuild(
        self,
        shot: Screenshot,
        pub: Publication,
        old_container_ids: list[str],
        *,
        base: dict[str, Any],
    ) -> RecoveryReport:
        """Explicit rebuild from original path; does not modify the source file."""
        path = Path(shot.path)
        if not path.is_file():
            self.clear_intent(shot.id)
            return RecoveryReport(
                **base,
                action="failed",
                message=f"Source file missing: {path}",
                status_after="failed",
            )
        caption = self.config.caption_for("instagram", path)
        media_type = shot.media_type or "image"
        if media_type == "video":
            assets = prepare_instagram_video_assets(path, self.config)
        else:
            assets = prepare_instagram_assets(path, self.config, allow_fallback=True)
        # Fresh containers — old IDs retained in last_error for history
        result = self.publisher.publish_prepared(
            assets, caption, prior_container_ids=None
        )
        history_note = (
            f"rebuild after {old_container_ids}; prior unknown recovery"
        )
        if result.ok:
            new_ids = list(result.container_ids or [])
            self.db.update_publication(
                shot.id,
                "instagram",
                status="published",
                remote_id=result.remote_id,
                last_error="",
                container_ids=new_ids,
                staged_keys=result.staged_keys,
                next_retry_at=0.0,
                increment_attempts=True,
            )
            self.clear_intent(shot.id)
            report_base = {**base, "container_ids": new_ids}
            return RecoveryReport(
                **report_base,
                action="rebuilt_published",
                message=f"Rebuild published ({history_note})",
                status_after="published",
                remote_id=result.remote_id,
                ok=True,
            )
        new_ids = list(result.container_ids or old_container_ids)
        status = "unknown" if result.ambiguous else "failed"
        if (result.error or "").startswith("rate_limit:"):
            status = "unknown"
        self.db.update_publication(
            shot.id,
            "instagram",
            status=status,
            last_error=f"{result.error}; {history_note}",
            container_ids=new_ids,
            staged_keys=result.staged_keys,
            next_retry_at=time.time() + (result.retry_after or 300),
            increment_attempts=True,
        )
        if (result.error or "").startswith("rate_limit:"):
            pass  # keep intent for resume after cooldown
        else:
            self.clear_intent(shot.id)
        report_base = {**base, "container_ids": new_ids}
        return RecoveryReport(
            **report_base,
            action="rebuild_incomplete",
            message=result.error or "rebuild did not complete",
            status_after=status,
            queued=status == "unknown",
            ok=status == "unknown",
        )
