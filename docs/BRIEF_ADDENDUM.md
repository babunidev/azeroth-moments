# Addendum: Adaptive Screenshot Dimensions and Instagram Landscape Carousel

## Purpose

The application must support arbitrary screenshot dimensions and aspect ratios because the user often captures screenshots using freeform region selection rather than fixed full-screen dimensions.

## Adaptive dimension handling

Accept screenshots of any reasonable dimensions. Never assume a fixed screen resolution, fixed aspect ratio, or full-screen captures only. Analyze each image independently: detect dimensions, classify shape, choose Instagram strategy, generate platform-specific outputs.

Suggested shape categories: portrait, square, landscape, extra_wide, extra_tall, irregular.

## Platform-specific output

- **Discord:** Upload the exact original file. No transforms. If over Discord attachment limit, fail Discord only.
- **Instagram:** Create processed derivatives. Never stretch. Never silently crop by default. Use padding or carousel slicing when appropriate.

## Instagram landscape carousel

Default landscape mode is a geometry-driven carousel of portrait-oriented slides (prefer 4:5), covering the full source scene left to right, with controlled overlap and blurred-background padding when needed. Panel count is computed from source dimensions within configured min/max bounds.

## Configuration

See `.env.example` for `INSTAGRAM_LANDSCAPE_MODE`, carousel panel bounds, overlap, canvas mode, padding style, and single-image fallback (preparation-time only).
