# Azeroth Moments — Media, Captions and Publication Records

**Author:** Babuni.dev

## Source and derivative separation

The watch directory contains original game captures. Discord uploads those original bytes, unchanged. Instagram preparation creates intermediate JPEG images or FFmpeg-rendered MP4 videos suited to that platform. Staged outputs are temporary media needed by Instagram's official API; they are not replacements for original source files.

## Media types and transformations

The [original brief](BRIEF.md) describes PNG, JPEG and WebP screenshots, validation after copy completion, SHA-256 deduplication and a publishing queue. The [adaptive addendum](BRIEF_ADDENDUM.md) requires handling freeform geometry without stretching or silent cropping and adds landscape portrait carousels. The [video addendum](VIDEO_ADDENDUM.md) covers MP4, MOV and MKV source video; Discord uploads those originals, while Instagram receives H.264/AAC MP4 derivatives. Default landscape video order: Preview, Left, Middle, Right.

## Provenance and safety

Source media ownership and the desired publishing aesthetic belong to Babuni.dev. Store original filename/hash, media type, relevant transformation settings, queue/run status and verified external platform IDs when available. Never include webhook credentials, OAuth tokens, staging secrets, private account logs or other unpublished media in public portfolio materials. Ambiguous API responses need reconciliation before retry to avoid accidental duplicate posts.

The source documentation describes R2/S3-compatible staging and a persistent queue. This audit did not query connected accounts, inspect active credential values, run a service or verify remote outcomes.
