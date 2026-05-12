# CYBERTRANS

CLI app for batch-transcoding videos to web-optimized H.264 in-place.
Drop a folder, originals get overwritten with smaller, fast-start versions.

## Origin

Born 2026-05-08 from a one-shot ffmpeg job on the `OneDrive/.../PROTOTIPOS/videos` folder
(21 prototype videos, ~393 MB → ~44 MB). The user wanted that same workflow as a reusable
terminal app with a cyberpunk UI.

## Transcoding parameters

Configured via interactive menu before each run. The pipeline is:

```
ffmpeg -i IN [audio-flags] \
  -vf "scale='min(W,iw)':'min(H,ih)':force_original_aspect_ratio=decrease,
       scale=trunc(iw/2)*2:trunc(ih/2)*2" \
  -pix_fmt yuv420p -preset medium -crf {26|28} \
  -movflags +faststart \
  [-c:v libx264 -profile:v high -level 4.0
   | -c:v libx265 -tag:v hvc1 -x265-params log-level=error] \
  OUT
```

User-selectable:
- **Codec**: H.264 (`libx264`, crf 26) or H.265 (`libx265`, crf 28, `hvc1` tag for Apple compat)
- **Max resolution**: FHD (1920×1080) or 4K (3840×2160) — **cap only, never upscales**.
  Uses `force_original_aspect_ratio=decrease` so portrait sources fit into a rotated box;
  trailing scale forces even dims for `yuv420p`.
- **Audio**: strip (`-an`) or keep (re-encoded to `aac 128k` for browser-safe consistency)

Constant across all settings: `+faststart`, `yuv420p`, `preset medium`, `high@4.0` profile (h264 only).

Atomic in-place: writes to `.__cyber__.<pid>.<name>` next to source, `os.replace()` on success.
On ffmpeg failure the temp is deleted and the original is left untouched.

## Files

- `cybertrans.py` — main script (Python 3, stdlib only, no pip deps)
- `cybertrans` — thin bash launcher (`./cybertrans [folder]`)

## Run

```
./cybertrans                    # full interactive flow: menu → drag inputs → confirm
./cybertrans /path/to/folder    # skip drag-drop, still shows menu
./cybertrans file1.mp4 file2.mov dir/   # any mix of files & dirs
```

Interactive prompt handles paths pasted via macOS Terminal drag-drop (backslash-escaped
spaces, surrounding quotes, multiple items) — see `parse_paths()` which uses `shlex.split`
and dedups.

Discovers videos with extensions: `.mp4 .mov .m4v .mkv .webm .avi`. For directory inputs:
flat scan only, no recursion (intentional — safer when the user drops a parent folder by
accident). Individual file inputs are always included.

## UI notes

- Pure ANSI 256-color escapes, no external libs (rich/textual deliberately avoided so
  the app runs on any macOS with stock `python3`)
- Spinner: braille frames; main bar: pink→magenta→purple gradient; queue bar: cyan ▰▱
- Banner is "ANSI Shadow" style figlet for "CYBERTRANS"
- Per-file live UI is **3 lines** redrawn at ~10 Hz via `\033[3F` cursor-up + `\033[J`:
  1. spinner · `[i/n]` · filename · bar · percent
  2. `speed Xx · fps · bitrate · eta · current output size`
  3. `queue ▰▱… · done/total · saved total · overall eta`
- Per-file ETA = `remaining_video_sec / speed` (parsed from ffmpeg's `speed=` line)
- Overall ETA = `remaining_video_sec_total / avg_speed` where avg_speed is
  `processed_video_sec / real_elapsed` accumulated across the run
- Progress fields parsed from `ffmpeg -progress pipe:1`: `out_time`, `fps`, `speed`,
  `bitrate`, `total_size`, `frame`, `progress=end`

## Dependencies

System: `ffmpeg`, `ffprobe` on PATH (Homebrew: `brew install ffmpeg`). Python: stdlib only.
