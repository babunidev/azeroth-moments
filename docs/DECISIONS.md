# Azeroth Moments — decisions and alternatives

**Author:** Babuni.dev

## Source-documented directions

1. **Minimal manual workflow:** copying or moving a screenshot into the folder should be enough to begin handling it. See [original brief](BRIEF.md).
2. **Discord fidelity:** retain the exact input file and isolate Discord failures from Instagram processing. See [adaptive brief](BRIEF_ADDENDUM.md).
3. **Aspect-ratio care:** never assume standard fullscreen capture dimensions; choose a strategy from actual geometry and avoid silent cropping.
4. **Landscape video grammar:** begin with a preview and follow with Left → Middle → Right panels. See [video addendum](VIDEO_ADDENDUM.md).
5. **Publication integrity:** distinguish remote ambiguous outcomes from definite failures; do not blindly double-post.

These reflect source briefs and README. The precise alternatives debated in unavailable discussions are not invented. No new choice was approved during the present audit.

## Design evolution and documented tradeoffs — audit #37

- **Initial brief:** Babuni.dev wanted publishing to be as simple as putting a screenshot in a watched directory. Original screenshots remain untouched, and platform results are recorded.
- **Adaptive-image addendum:** Captures may be irregular, portrait, square, landscape or extra-wide. Discord uses the exact original; Instagram may use padded, overlapping portrait carousel panels instead of destructive cropping.
- **Video addendum:** Source MP4/MOV/MKV files join the watcher; landscape videos become a **Preview → Left → Middle → Right** sequence for Instagram, while Discord retains the untouched file.
- **Publishing reliability:** Keep per-platform failures independent and reconcile unknown remote results before a retry. Persistent status and rate limiting make automatic operation safer.
- **Scope boundary:** Additional destinations were mentioned as an architectural possibility in the initial brief, not implemented services or approved imminent milestones.

These are existing brief/source decisions. The documentation audit approved no new creative or technical behavior.
