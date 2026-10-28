# Azeroth Moments — roadmap and status

**Author:** Babuni.dev

## Source-supported functionality

The README documents local file watching, a persistent/rate-limited publishing queue, Discord originals, Instagram prepared derivatives, S3-compatible staging for Instagram, FFmpeg conversion, systemd operation and CLI inspection. The brief files record how the scope expanded from screenshots to arbitrary aspect ratios and then video.

## Outstanding verification (not new approved features)

The repository was inspected, but no local service or actual platform credentials were accessed in this audit. Confirm end-to-end publication and source preservation, deduplication, recovery after restarts, reconciliation of unknown remote outcomes, expected carousel order, rate-limit handling, and safe management of private tokens. No additional platform or publishing scheme was approved in this documentation pass.

## Audit #37 — evidence-based checks (2026-10-28)

- [x] Preserve the original screenshot, adaptive-geometry, and video briefs as project direction.
- [x] Add dedicated design and content/provenance documentation for portfolio use.
- [x] Distinguish source media from Instagram derivatives and known from ambiguous publication outcomes.
- [ ] Run the existing test suite in a clean environment and record its exact result.
- [ ] Verify a fresh screenshot and video publication end to end, confirming Discord byte fidelity and Instagram media layout.
- [ ] Confirm start-up baseline, hash deduplication, queue persistence, rate limits and recovery from restart.
- [ ] Exercise failed Discord-only oversize cases and unknown Instagram publication outcomes without double-posting.
- [ ] Check FFmpeg/ffprobe dependencies, R2 staged URL retention, platform permissions and systemd behavior on the actual host.
- [ ] Create approved sample images and publication screenshots with secrets and private account data removed.

These are validation tasks rather than newly accepted feature requirements.
