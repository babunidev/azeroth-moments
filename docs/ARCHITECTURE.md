# Azeroth Moments — architecture

**Author:** Babuni.dev

- Python project under `azeroth_moments/`, with CLI entry points, watcher/publishing logic, settings, database state, caption generation, image classification/canvas/carousel preparation and platform clients.
- Original screenshots and videos are watched under the user's configured directory. Per the [adaptive-image brief](BRIEF_ADDENDUM.md), originals are not silently cropped or deleted; Discord receives an unchanged file.
- Instagram requires supported derivatives, externally available staged media URLs and the official API. Landscape images can be split into portrait slides.
- The [video addendum](VIDEO_ADDENDUM.md) documents MP4/MOV/MKV inputs and FFmpeg processing. The default landscape presentation is Preview → Left → Middle → Right in 9:16 H.264/AAC video panels.
- Local persistence and status/inspection commands help distinguish queued, published, failed and ambiguous remote outcomes. Service files support systemd operation on Nobara/Fedora.

Platform secrets belong outside Git. This is a static audit, not proof of successful live publishing.

## Implementation trace — 2026-10-28

The original [developer brief](BRIEF.md) specifies file-stability checks, Pillow validation, SHA-256 deduplication, SQLite tracking and a publishing queue. The [image addendum](BRIEF_ADDENDUM.md) makes geometry classification and landscape portrait-panel preparation explicit. The [video addendum](VIDEO_ADDENDUM.md) extends file detection to MP4/MOV/MKV and distinguishes preserved Discord video uploads from H.264/AAC Instagram derivatives prepared with FFmpeg.

`pyproject.toml` declares package version **1.0.0**, Python 3.11+, the `azeroth-moments` CLI, and dependencies including watchdog, Pillow, httpx, python-dotenv and boto3; the README separately requires FFmpeg and ffprobe for video. Instagram requires externally retrievable staged media, so the implementation uses S3-compatible object storage rather than a local file URL.

**Operational boundary:** a source-level queue, reconciliation logic and systemd unit do not prove that an installed service is running or platforms have accepted a post. Discord failure from an oversized original is isolated from Instagram. Retrying an ambiguous remote outcome without reconciliation risks duplication; these behaviors remain subjects for controlled end-to-end tests.
