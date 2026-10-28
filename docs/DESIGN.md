# Azeroth Moments — Media and Interaction Design

**Author:** Babuni.dev

The interface is intentionally almost invisible: a player captures a World of Warcraft screenshot or video, moves it into `~/Pictures/Azeroth Moments/`, and the local watcher handles the rest. The original developer brief defines this minimum-friction experience. There is no requirement to manually upload each capture or edit media in a separate GUI.

## One source, two treatments

Discord receives the exact original file, subject to the platform attachment limit; no resize, crop, or transcode. Instagram receives an adapted derivative so unusually shaped screenshots can retain their visual story. The [adaptive brief](BRIEF_ADDENDUM.md) rejects assumptions about fullscreen geometry and prefers a sequence of portrait slices for landscape images. It distinguishes source-preserving overlap/padding from unannounced cropping.

The later [video addendum](VIDEO_ADDENDUM.md) adds landscape video presented as **Preview → Left → Middle → Right** portrait videos, formatted for 9:16 H.264/AAC delivery. The preview first conveys the overall scene; later synchronized crops invite closer attention. Discord retains the video original.

## Design constraints

The tool is event-driven and intended to stay unobtrusive. Startup behavior must not create an accidental flood of old posts. Public-facing captions, source framing and hashtags remain adjustable author choices, not assumptions embedded into portfolio documentation. Failed or ambiguous publication should be recorded clearly instead of quietly claiming success.

This guide documents existing briefs and source-reported behavior. It does not claim that a new capture or platform publication was tested in this audit.
