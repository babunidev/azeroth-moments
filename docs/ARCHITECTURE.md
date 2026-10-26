# Azeroth Moments — architecture

**Author:** Babuni.dev

- Python project under `azeroth_moments/`, with CLI entry points, watcher/publishing logic, settings, database state, caption generation, image classification/canvas/carousel preparation and platform clients.
- Original screenshots and videos are watched under the user's configured directory. Per the [adaptive-image brief](BRIEF_ADDENDUM.md), originals are not silently cropped or deleted; Discord receives an unchanged file.
- Instagram requires supported derivatives, externally available staged media URLs and the official API. Landscape images can be split into portrait slides.
- The [video addendum](VIDEO_ADDENDUM.md) documents MP4/MOV/MKV inputs and FFmpeg processing. The default landscape presentation is Preview → Left → Middle → Right in 9:16 H.264/AAC video panels.
- Local persistence and status/inspection commands help distinguish queued, published, failed and ambiguous remote outcomes. Service files support systemd operation on Nobara/Fedora.

Platform secrets belong outside Git. This is a static audit, not proof of successful live publishing.
