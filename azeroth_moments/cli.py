"""Command-line interface for Azeroth Moments."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from azeroth_moments.auth_instagram import InstagramAuth
from azeroth_moments.config import load_config
from azeroth_moments.db import Database
from azeroth_moments.logging_utils import setup_logging
from azeroth_moments.meta_api_gate import InstagramApiGate
from azeroth_moments.publishers.discord import DiscordPublisher
from azeroth_moments.publishers.instagram import InstagramPublisher
from azeroth_moments.recovery import InstagramRecovery
from azeroth_moments.worker import Worker, pid_is_running

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="azeroth-moments",
        description="Automatically share WoW screenshots on Instagram and Discord",
    )
    parser.add_argument("--env", type=Path, default=None, help="Path to .env file")
    parser.add_argument("--dry-run", action="store_true", help="Process without publishing")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("start", help="Start monitoring and publishing")
    sub.add_parser("status", help="Show current state")
    sub.add_parser("test", help="Test configured publishers")
    sub.add_parser("pause", help="Pause publishing")
    sub.add_parser("resume", help="Resume publishing")

    retry_p = sub.add_parser(
        "retry",
        help=(
            "With no id: re-queue eligible failed uploads. "
            "With <screenshot_id> --platform instagram: safe Instagram recovery."
        ),
    )
    retry_p.add_argument(
        "screenshot_id",
        nargs="?",
        type=int,
        default=None,
        help="Screenshot/video ID (screenshots.id), not publication id",
    )
    retry_p.add_argument(
        "--platform",
        choices=("instagram", "discord"),
        default=None,
        help="Required when screenshot_id is set",
    )
    retry_p.add_argument(
        "--confirm-publish",
        action="store_true",
        help="Allow media_publish when parent is FINISHED (also implied by recovery of FINISHED)",
    )
    retry_p.add_argument(
        "--confirm-rebuild",
        action="store_true",
        help="Explicitly allow rebuilding expired containers from the original file",
    )
    retry_p.add_argument(
        "--queue-only",
        action="store_true",
        help="Queue recovery for the worker without running Graph calls in this process",
    )

    hist = sub.add_parser("history", help="Show publishing history")
    hist.add_argument("--limit", type=int, default=50)

    for name in ("unknown", "unknowns"):
        unk = sub.add_parser(
            name,
            help="List unknown publications (local DB only; no Instagram API calls)",
        )
        unk.add_argument("--platform", choices=("instagram", "discord"), default=None)

    insp = sub.add_parser(
        "inspect",
        help="Inspect one screenshot/video and its publication rows",
    )
    insp.add_argument(
        "screenshot_id",
        nargs="?",
        type=int,
        default=None,
        help="Screenshot/video ID (screenshots.id)",
    )
    insp.add_argument(
        "--id",
        type=int,
        default=None,
        dest="screenshot_id_flag",
        help="Alias for positional screenshot id",
    )
    insp.add_argument(
        "--containers",
        action="store_true",
        help="Also GET Instagram container status (uses API; honors global cooldown)",
    )

    rec = sub.add_parser(
        "reconcile",
        help=(
            "Safely check Instagram container status for an unknown publication. "
            "Prefer: retry <id> --platform instagram"
        ),
    )
    rec.add_argument("screenshot_id", nargs="?", type=int, default=None)
    rec.add_argument("--id", type=int, default=None, dest="screenshot_id_flag")
    rec.add_argument(
        "--publish-if-finished",
        action="store_true",
        help="If parent container is FINISHED, call media_publish once (explicit opt-in)",
    )
    return parser


def _resolve_screenshot_id(args: argparse.Namespace) -> int | None:
    return getattr(args, "screenshot_id", None) or getattr(
        args, "screenshot_id_flag", None
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(explicit_env=args.env, dry_run=True if args.dry_run else None)
    setup_logging(config.log_level)
    db = Database()

    if args.command == "start":
        return cmd_start(config, db)
    if args.command == "status":
        return cmd_status(config, db)
    if args.command == "test":
        return cmd_test(config, db)
    if args.command == "pause":
        db.set_paused(True)
        print("Publishing paused.")
        return 0
    if args.command == "resume":
        db.set_paused(False)
        print("Publishing resumed.")
        return 0
    if args.command == "retry":
        return cmd_retry(config, db, args)
    if args.command == "history":
        rows = db.history(limit=args.limit)
        print(json.dumps(rows, indent=2))
        return 0
    if args.command in {"unknown", "unknowns"}:
        return cmd_unknowns(db, platform=args.platform)
    if args.command == "inspect":
        sid = _resolve_screenshot_id(args)
        if sid is None:
            parser.error("inspect requires a screenshot id, e.g. inspect 10")
        return cmd_inspect(config, db, sid, containers=args.containers)
    if args.command == "reconcile":
        sid = _resolve_screenshot_id(args)
        if sid is None:
            parser.error("reconcile requires a screenshot id")
        # Route through recovery when possible
        recovery = InstagramRecovery(config, db)
        report = recovery.retry(
            sid,
            platform="instagram",
            confirm_publish=args.publish_if_finished,
            confirm_rebuild=False,
            run_now=True,
        )
        print(json.dumps(report.as_dict(), indent=2))
        return 0 if report.ok or report.queued else 1
    parser.error(f"Unknown command {args.command}")
    return 2


def cmd_start(config, db: Database) -> int:
    running = pid_is_running()
    if running:
        print(f"Already running with PID {running}", file=sys.stderr)
        return 1
    config.watch_directory.mkdir(parents=True, exist_ok=True)
    worker = Worker(config, db)
    worker.run()
    return 0


def cmd_status(config, db: Database) -> int:
    pid = pid_is_running()
    counts = db.counts_by_status()
    discord_ready, discord_reason = config.discord_ready()
    ig_configured, ig_configured_reason = config.instagram_configured()
    gate = InstagramApiGate(db)
    recovery = InstagramRecovery(config, db, api_gate=gate)
    snap = gate.snapshot()
    api_status = str(snap.get("api_access_status") or "unverified")
    api_verified = api_status == "ok" and not bool(snap.get("access_blocked"))
    # ready = locally configured AND Meta access not known-blocked
    ig_ready = ig_configured and not bool(snap.get("access_blocked"))
    ig_ready_reason = ig_configured_reason
    if ig_configured and snap.get("access_blocked"):
        ig_ready_reason = (
            "configured locally but Meta API access is blocked — "
            "run `azeroth-moments test` after fixing Meta Developer Dashboard"
        )
    elif ig_configured and api_status == "unverified":
        ig_ready_reason = (
            "configured locally; API access not verified yet "
            "(run `azeroth-moments test`)"
        )
    status = {
        "running": bool(pid),
        "pid": pid,
        "paused": db.is_paused(),
        "baseline_done": db.baseline_done(),
        "watch_directory": str(config.watch_directory),
        "dry_run": config.dry_run,
        "discord": {
            "enabled": config.discord_enabled,
            "ready": discord_ready,
            "reason": discord_reason,
        },
        "instagram": {
            "enabled": config.instagram_enabled,
            "configured": ig_configured,
            "configured_reason": ig_configured_reason,
            "ready": ig_ready,
            "ready_reason": ig_ready_reason,
            # Backward-compatible alias for older tooling
            "reason": ig_ready_reason,
            "api_access": {
                "verified": api_verified,
                "status": api_status,
                "access_blocked": bool(snap.get("access_blocked")),
                "detail": snap.get("api_access_reason") or "",
                "checked_at": snap.get("api_access_checked_at") or 0,
                "note": (
                    "status/test probes Meta once; status itself does not call the API"
                ),
            },
            "api_cooldown": snap,
            "token": InstagramAuth(config, db).snapshot(),
            "recovery_queue": [i.screenshot_id for i in recovery.list_intents()],
            "endpoints": {
                "host": "https://graph.instagram.com",
                "api_version": config.instagram_api_version,
                "login": "Instagram Login (not Facebook Page token)",
            },
        },
        "publication_counts": counts,
        "publishes_last_hour": db.publishes_in_last_hour(),
        "publish_max_per_hour": config.publish_max_per_hour,
        "unknown_count": len(db.list_unknown_detail()),
    }
    print(json.dumps(status, indent=2))
    return 0


def cmd_test(config, db: Database) -> int:
    ok = True
    discord = DiscordPublisher(config)
    result = discord.test_connection()
    print(f"Discord: {'OK' if result.ok else 'FAIL'} {result.error or result.remote_id}")
    ok = ok and (result.ok or not config.discord_enabled)

    configured, configured_reason = config.instagram_configured()
    print(
        f"Instagram configured: {'OK' if configured else 'FAIL'} {configured_reason}"
    )
    auth = InstagramAuth(config, db)
    if configured and not config.dry_run:
        refresh = auth.maybe_refresh(force=False)
        if refresh.get("refreshed"):
            print(f"Instagram token: refreshed; expires_at={refresh.get('expires_at')}")
        elif refresh.get("attempted"):
            print(
                f"Instagram token: refresh failed ({refresh.get('error') or refresh.get('reason')})"
            )
        else:
            tok = auth.snapshot()
            days = tok.get("days_until_expiry")
            print(
                "Instagram token: "
                f"present={tok.get('present')} needs_refresh={tok.get('needs_refresh')} "
                f"expires_at={tok.get('expires_at') or 'unknown'} "
                f"days_until_expiry={days}"
            )
    ig = InstagramPublisher(config, db, auth=auth)
    # One live probe only — updates access-block gate from Meta's response.
    result = ig.verify_api_access(force=True)
    gate = InstagramApiGate(db)
    snap = gate.snapshot()
    if result.ok:
        print(
            f"Instagram API access: OK remote_id={result.remote_id} "
            f"status={snap.get('api_access_status')}"
        )
    else:
        print(f"Instagram API access: FAIL {result.error}")
        if snap.get("access_blocked"):
            print(
                "Meta platform block engaged — worker will not call Instagram Graph "
                "until a later `azeroth-moments test` succeeds. Discord is unaffected."
            )
            print(
                "Dashboard: developers.facebook.com → App → Instagram → API setup "
                "with Instagram login; also check developer account verification / "
                "Account Quality if shown."
            )
    if config.instagram_enabled:
        ok = ok and configured and result.ok
    return 0 if ok else 1


def cmd_retry(config, db: Database, args: argparse.Namespace) -> int:
    if args.screenshot_id is None:
        n = db.mark_retry_eligible_failed()
        print(json.dumps({"queued_failed_retries": n}, indent=2))
        return 0
    if not args.platform:
        print(
            json.dumps(
                {
                    "error": "retry <screenshot_id> requires --platform",
                    "example": f"azeroth-moments retry {args.screenshot_id} --platform instagram",
                    "note": "screenshot_id is screenshots.id (not publications.id)",
                }
            ),
            file=sys.stderr,
        )
        return 2
    if args.platform == "discord":
        print(
            json.dumps(
                {
                    "error": "Discord recovery is not performed by this command",
                    "screenshot_id": args.screenshot_id,
                    "hint": "Discord stays independent of Instagram recovery",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    recovery = InstagramRecovery(config, db)
    report = recovery.retry(
        args.screenshot_id,
        platform="instagram",
        confirm_publish=args.confirm_publish,
        confirm_rebuild=args.confirm_rebuild,
        run_now=not args.queue_only,
    )
    print(json.dumps(report.as_dict(), indent=2))
    return 0 if report.ok or report.queued else 1


def cmd_unknowns(db: Database, *, platform: str | None) -> int:
    rows = db.list_unknown_detail(platform)
    now = time.time()
    normalized = []
    for row in rows:
        retry_in = max(0.0, float(row.get("next_retry_at") or 0) - now)
        try:
            container_ids = json.loads(row.get("container_ids") or "[]")
        except json.JSONDecodeError:
            container_ids = row.get("container_ids")
        try:
            staged_keys = json.loads(row.get("staged_keys") or "[]")
        except json.JSONDecodeError:
            staged_keys = row.get("staged_keys")
        normalized.append(
            {
                "screenshot_id": row.get("screenshot_id"),
                "publication_id": row.get("publication_id"),
                "filename": row.get("filename") or Path(str(row.get("path") or "")).name,
                "path": row.get("path"),
                "media_type": row.get("media_type"),
                "platform": row.get("platform"),
                "status": row.get("status"),
                "last_error": row.get("last_error"),
                "container_ids": container_ids,
                "staged_keys": staged_keys,
                "next_retry_at": row.get("next_retry_at"),
                "retry_in_seconds": round(retry_in, 1),
                "updated_at": row.get("updated_at"),
            }
        )
    print(
        json.dumps(
            {
                "count": len(normalized),
                "id_fields": {
                    "screenshot_id": "screenshots.id (use this with retry/inspect)",
                    "publication_id": "publications.id (internal row id)",
                },
                "unknowns": normalized,
            },
            indent=2,
        )
    )
    return 0


def cmd_inspect(
    config,
    db: Database,
    screenshot_id: int,
    *,
    containers: bool,
) -> int:
    shot = db.get_screenshot(screenshot_id)
    if shot is None:
        print(
            json.dumps({"error": f"screenshot_id {screenshot_id} not found"}),
            file=sys.stderr,
        )
        return 1
    pubs = {}
    for platform in ("discord", "instagram"):
        pub = db.get_publication(screenshot_id, platform)
        if pub is None:
            continue
        pubs[platform] = {
            "publication_id": pub.id,
            "screenshot_id": screenshot_id,
            "status": pub.status,
            "attempts": pub.attempts,
            "last_error": pub.last_error,
            "remote_id": pub.remote_id,
            "container_ids": json.loads(pub.container_ids or "[]"),
            "staged_keys": json.loads(pub.staged_keys or "[]"),
            "next_retry_at": pub.next_retry_at,
            "retry_in_seconds": round(max(0.0, pub.next_retry_at - time.time()), 1),
            "updated_at": pub.updated_at,
        }
    gate = InstagramApiGate(db)
    recovery = InstagramRecovery(config, db, api_gate=gate)
    intent = recovery.get_intent(screenshot_id)
    payload: dict = {
        "screenshot_id": shot.id,
        "filename": Path(shot.path).name,
        "screenshot": {
            "id": shot.id,
            "path": shot.path,
            "media_type": shot.media_type,
            "shape": shot.classified_shape,
            "sha256": shot.sha256,
            "author": shot.author,
        },
        "publications": pubs,
        "recovery_intent": json.loads(intent.to_json()) if intent else None,
        "instagram_api_cooldown": gate.snapshot(),
        "id_fields": {
            "screenshot_id": "screenshots.id (CLI retry/inspect argument)",
            "publication_id": "publications.id (per-platform row)",
        },
    }
    if containers:
        ig_pub = pubs.get("instagram") or {}
        cids = list(ig_pub.get("container_ids") or [])
        ig = InstagramPublisher(config, db, api_gate=gate)
        payload["container_status"] = ig.inspect_containers(cids)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
