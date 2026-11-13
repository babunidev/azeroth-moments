# Azeroth Moments — development journal

**Author:** Babuni.dev

## Retrospective reconstruction — 2026-10-26

The initial [developer brief](BRIEF.md) begins with a deliberate reduction of friction: screenshots from World of Warcraft should be shared simply by moving them into a local folder. That requirement shaped a file watcher, validation and deduplication, an upload queue, and two publishing routes. Discord was treated as an archive of the original capture; Instagram as a place requiring a different visual presentation.

A subsequent [dimension addendum](BRIEF_ADDENDUM.md) arose from the reality of freeform screenshots. Rather than assume the shape of the input, the pipeline would classify it and choose appropriate derivative panels. The [video addendum](VIDEO_ADDENDUM.md) extended the design to gameplay clips, preserving Discord originals while composing Instagram's preview-first landscape sequence.

The README and Python package show how these concerns were organized into runnable-intent software with persistent status tracking, media preparation and local systemd installation. The actual deployment, publication history and error recovery were not tested in this repository audit. Babuni.dev contributed the creative goal and workflow constraints; AI assistance contributed engineering and this retrospective documentation. Specific tests and corrections require their own evidence.

## 2026-10-28 — Audit #37: from a capture folder to a publishing grammar

Babuni.dev's earliest brief describes a deliberately small action: take a World of Warcraft screenshot, put it in a folder, and let the computer share the moment. Behind that simple gesture lie difficult boundaries—waiting until a file has finished copying, recognizing duplicates, keeping upload attempts within platform limits, and not confusing a timed-out request with definite failure. The original capture is preserved for Discord; Instagram requires an alternative presentation.

A later brief records the artist's actual working habit: screenshots are often selected as irregular regions rather than fixed fullscreen frames. That led to geometry-aware carousel slicing, overlap and padding rather than a universal crop. The third brief adds gameplay video, retaining untouched originals for Discord and placing an overall preview before synchronized Left/Middle/Right portrait panels on Instagram. The new video form is a creative presentation rule, not simply a format conversion.

**Babuni.dev direction:** frictionless publication with source fidelity and careful visual presentation. **AI contribution:** source-backed engineering choices and this written historical synthesis, not ownership of captured gameplay or the original idea. **Iteration:** screenshot-only workflow, variable-size image support, then video. **Implementation in this audit:** Markdown guides and links only. **Verification:** README, original briefs and package metadata reviewed; no watcher process, actual Discord upload, Instagram post, image/video render or R2 account was tested.

## 2026-11-13 — Giving the repository clear permissions

Public source alone did not explain what others could reuse, so I approved MIT for the original media-publishing software. AI assistance added the license and brought the project notes into agreement, without changing how the application runs. Other people's content retains its own terms.
