# Video Support Addendum

Azeroth Moments accepts `.mp4`, `.mov`, and `.mkv` in the watch folder.

- **Discord:** original file only; fail if over `DISCORD_MAX_ATTACHMENT_BYTES`; never transcode.
- **Instagram:** FFmpeg derivatives staged to R2; landscape default is preview-first carousel (Preview → Left → Middle → Right) at 9:16 H.264/AAC MP4.

See `.env.example` for `VIDEO_*` and `INSTAGRAM_VIDEO_*` settings. Requires `ffmpeg` and `ffprobe`.
