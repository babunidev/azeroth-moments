# Azeroth Moments — development journal

**Author:** Babuni.dev

## Retrospective reconstruction — 2026-10-26

The initial [developer brief](BRIEF.md) begins with a deliberate reduction of friction: screenshots from World of Warcraft should be shared simply by moving them into a local folder. That requirement shaped a file watcher, validation and deduplication, an upload queue, and two publishing routes. Discord was treated as an archive of the original capture; Instagram as a place requiring a different visual presentation.

A subsequent [dimension addendum](BRIEF_ADDENDUM.md) arose from the reality of freeform screenshots. Rather than assume the shape of the input, the pipeline would classify it and choose appropriate derivative panels. The [video addendum](VIDEO_ADDENDUM.md) extended the design to gameplay clips, preserving Discord originals while composing Instagram's preview-first landscape sequence.

The README and Python package show how these concerns were organized into runnable-intent software with persistent status tracking, media preparation and local systemd installation. The actual deployment, publication history and error recovery were not tested in this repository audit. Babuni.dev contributed the creative goal and workflow constraints; AI assistance contributed engineering and this retrospective documentation. Specific tests and corrections require their own evidence.
