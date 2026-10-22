# Azeroth Moments

Automatically share World of Warcraft screenshots **and videos** to Discord and Instagram on Nobara Linux.

Drop a file into `~/Pictures/Azeroth Moments` — Discord receives the **exact original file**, and Instagram receives an adaptively prepared post (landscape images → geometry-driven carousel; landscape videos → preview-first portrait carousel).

## Features

- Folder watcher with startup baseline (does not bulk-publish existing files by default)
- Rate-limited publish queue
- Discord webhook uploads of unmodified originals (images and videos)
- Instagram API with Instagram Login (`graph.instagram.com`)
- S3-compatible staging (Cloudflare R2 / AWS S3) for Instagram media URLs
- Adaptive Instagram framing for images; FFmpeg video prep (H.264/AAC MP4)
- Landscape video default: **Preview → Left → Middle → Right** carousel
- Meta pre-stage validation, reconcile-first ambiguous outcomes
- systemd user service + CLI

Requires **FFmpeg** (`ffmpeg` + `ffprobe`) for video processing.

## Install (Nobara / Fedora)

```bash
cd ~/Projects/AzerothMoments
chmod +x scripts/install.sh
./scripts/install.sh
```

Edit secrets:

```bash
nano ~/.config/azeroth-moments/.env
chmod 600 ~/.config/azeroth-moments/.env
```

Then:

```bash
systemctl --user restart azeroth-moments
~/.local/share/azeroth-moments/venv/bin/azeroth-moments status
~/.local/share/azeroth-moments/venv/bin/azeroth-moments test
```

## CLI

| Command | Purpose |
| --- | --- |
| `azeroth-moments start` | Start monitoring (used by systemd) |
| `azeroth-moments status` | Show state (includes Instagram API cooldown) |
| `azeroth-moments test` | Test Discord / Instagram connections |
| `azeroth-moments pause` / `resume` | Pause or resume publishing |
| `azeroth-moments history` | Recent publication history |
| `azeroth-moments unknown` | List unknown publications (local DB only) |
| `azeroth-moments inspect 10` | Inspect screenshot/video id 10 (add `--containers` for live Meta status) |
| `azeroth-moments retry 10 --platform instagram` | Safe Instagram recovery (reuses containers; honors API cooldown) |
| `azeroth-moments retry 10 --platform instagram --confirm-rebuild` | Rebuild only after containers expired (explicit; may create a new post) |
| `azeroth-moments retry` | Bulk re-queue eligible failed uploads |
| `azeroth-moments --dry-run start` | Process without calling publish APIs |

`screenshot_id` is `screenshots.id` (what `inspect` / `retry` take). `publication_id` is the per-platform row in `publications`.

## Discord setup

1. In the ROAR Discord server, create an incoming webhook for your channel.
2. Set `DISCORD_WEBHOOK_URL` in `.env`.
3. Keep `DISCORD_ENABLED=true`.

Discord uploads original screenshots as attachments. Videos that fit under `DISCORD_MAX_ATTACHMENT_BYTES` are uploaded as originals; oversized videos are not compressed — after Instagram publishes, Discord receives the official Instagram permalink instead.

## Instagram setup (Instagram Login)

1. Convert `@babunigaming` to a Professional account if needed.
2. Create a Meta app that supports **Instagram API with Instagram Login**.
3. Request permissions such as `instagram_business_basic` and `instagram_business_content_publish`.
4. Obtain a long-lived Instagram user access token and Instagram account ID.
5. Configure an S3-compatible bucket (R2 recommended) and set:

   - `INSTAGRAM_ENABLED=true`
   - `INSTAGRAM_ACCESS_TOKEN`
   - `INSTAGRAM_ACCOUNT_ID`
   - `INSTAGRAM_TOKEN_EXPIRES_AT` (ISO-8601)
   - `INSTAGRAM_LOGIN_APP_ID` / `INSTAGRAM_LOGIN_APP_SECRET` (for app identity / docs)
   - `S3_BUCKET`, `S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`

Tokens may also be stored at `~/.config/azeroth-moments/instagram_token.txt` (mode 600). Long-lived Instagram Login tokens last about **60 days**. The worker renews them automatically via `graph.instagram.com/refresh_access_token` (about 7 days before expiry, or periodically when `INSTAGRAM_TOKEN_EXPIRES_AT` is unknown). Check `azeroth-moments status` → `instagram.token` for expiry / refresh state.

Without staging credentials, Instagram stays unavailable and Discord still works.

## Landscape carousels

**Images:** panel count and crop windows are computed from the source geometry (config min/max/default are bounds/hints). The full scene is covered left-to-right with controlled overlap; panels are framed to 4:5 with blurred-background padding when needed. Single-image fallback runs only if carousel **preparation** fails before any publish attempt.

**Videos:** landscape clips default to `INSTAGRAM_VIDEO_LANDSCAPE_MODE=preview_carousel`:
1. Preview (center-focused portrait)
2. Left portrait crop
3. Middle portrait crop
4. Right portrait crop  

All clips share the same duration/sync window, target 9:16, and are staged to R2 before Instagram carousel publish. Incomplete sets are never published. Discord still gets the original video only when under `DISCORD_MAX_ATTACHMENT_BYTES` (no Discord transcode).

Ambiguous Instagram API responses are reconciled and never auto-switched to another method.

## Desktop notifications

On successful Discord or Instagram publishes, Azeroth Moments shows a desktop
notification with the filename (via `notify-send` / libnotify). Disable with
`DESKTOP_NOTIFICATIONS=false` in `.env`.

## Captions

Default caption (Discord and Instagram):

```
Azeroth, through my eyes.

#BabuniGaming #WorldOfWarcraft #WoWForever #Azeroth #AzerothMoments
```

Optional custom middle line from the filename: if the basename starts with `?`,
every `+` becomes a space and that text is inserted between the tagline and
hashtags (with dashes). Example: `?arriving+in+Redridge.png` publishes as:

```
Azeroth, through my eyes.

-arriving in Redridge-

#BabuniGaming #WorldOfWarcraft #WoWForever #Azeroth #AzerothMoments
```

Works for images and videos. Filenames without a leading `?` keep the default caption.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Specs: [docs/BRIEF.md](docs/BRIEF.md), [docs/BRIEF_ADDENDUM.md](docs/BRIEF_ADDENDUM.md).

## Troubleshooting

- **Nothing publishing:** `azeroth-moments status` — check `paused`, `baseline_done`, and publisher `ready` reasons.
- **Existing folder not posted on first run:** expected (`PUBLISH_EXISTING_ON_STARTUP=false`). New drops are published.
- **Local publish pacing:** items wait until under `PUBLISH_MAX_PER_HOUR`.
- **Instagram API rate limit (HTTP 403 code 4):** a shared cooldown is stored in the DB and survives restarts; Discord keeps publishing. Check `status` → `instagram.api_cooldown`, then `unknown` / `inspect 10`.
- **Instagram unknown:** ambiguous Meta outcome; containers are preserved. Recover with `retry <screenshot_id> --platform instagram` (queues through cooldowns; publishes a FINISHED parent once; expired containers need `--confirm-rebuild`).
- **Logs:** `~/.local/state/azeroth-moments/azeroth-moments.log`
