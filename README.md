# ▓▒░ CYBERTRANS ░▒▓

```
   ██████╗██╗   ██╗██████╗ ███████╗██████╗ ████████╗██████╗  █████╗ ███╗   ██╗███████╗
  ██╔════╝╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗╚══██╔══╝██╔══██╗██╔══██╗████╗  ██║██╔════╝
  ██║      ╚████╔╝ ██████╔╝█████╗  ██████╔╝   ██║   ██████╔╝███████║██╔██╗ ██║███████╗
  ██║       ╚██╔╝  ██╔══██╗██╔══╝  ██╔══██╗   ██║   ██╔══██╗██╔══██║██║╚██╗██║╚════██║
  ╚██████╗   ██║   ██████╔╝███████╗██║  ██║   ██║   ██║  ██║██║  ██║██║ ╚████║███████║
   ╚═════╝   ╚═╝   ╚═════╝ ╚══════╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝
                  N E O N  ·  V I D E O  ·  O P T I M I Z E R
```

> Neon-terminal batch transcoder. Drag a folder, get web-ready videos in place.
> `ffmpeg` under the hood, cyberpunk on the surface, zero pip dependencies.

`CYBERTRANS` is a single-file Python CLI that takes the videos you throw at it and
overwrites them with smaller, browser-streamable H.264 / H.265 versions. Originals are
replaced atomically — if `ffmpeg` fails, the source is left untouched.

---

## ◆ Features

- **Interactive codec menu** — pick H.264 or H.265 per run
- **Resolution cap** — clamp to FHD (1920×1080) or 4K (3840×2160), never upscales
- **Audio toggle** — strip entirely or keep as AAC 128k (browser-safe)
- **Drag-drop friendly** — drop a folder, drop a file, drop ten files, mixed is fine
- **Live progress UI** — per-file speed / fps / bitrate / ETA + overall queue ETA
- **Atomic in-place writes** — `os.replace()` only on success, temp cleaned on failure
- **Faststart** — `moov` atom moved to front so browsers can stream before full download
- **Zero pip deps** — stdlib Python only; pure ANSI 256-color escapes (no `rich` / `textual`)

---

## ▶ Quick start

```bash
git clone https://github.com/AccentureCodeFoundry/314899_Cybertranscoding.git
cd 314899_Cybertranscoding
./cybertrans
```

That's it. The script will walk you through the config menu, then ask you to drop a
folder or files into the terminal.

**Need ffmpeg?** On first run, if `ffmpeg` / `ffprobe` are missing from `PATH`,
CYBERTRANS detects it and offers to install them for you via Homebrew:

```
  ⚠ missing on PATH: ffmpeg, ffprobe
  Homebrew detected at /opt/homebrew/bin/brew
  ▸ install ffmpeg via brew now? [y/N] _
```

If you don't have Homebrew, you'll get a platform-specific hint (`apt`, `dnf`, or the
manual download URL). No silent installs — you stay in the driver's seat.

### Modes

```bash
./cybertrans                           # full interactive: menu → drag inputs
./cybertrans /path/to/folder           # menu first, then process that folder
./cybertrans clip1.mp4 clip2.mov dir/  # mix of files and dirs
```

---

## ▦ The menu

```
  ◆ CODEC
    [1] H.264    wide compatibility · fast decode
    [2] H.265    ~30-50% smaller · modern devices
  ▸ select [1-2, default 1]: _

  ◆ MAX RESOLUTION
    [1] FHD      cap 1920×1080  ·  downscale only
    [2] 4K       cap 3840×2160  ·  downscale only
  ▸ select [1-2, default 1]: _

  ◆ AUDIO
    [1] strip    no audio track  (smaller, silent)
    [2] keep     re-encode AAC 128k  (browser-safe)
  ▸ select [1-2, default 1]: _
```

---

## ▼ Live progress

While encoding, every file gets a 3-line panel that refreshes ~10× per second:

```
  ⠹ [03/12] product_demo_full.mov          ████████████░░░░░░░░  62.4%
     speed  4.2x  fps 121.3  br 1.8Mbits/s  eta 00:12   size  14.20 MB
     queue  ▰▰▰▰▰▰░░░░░░░░░░░░░░░░   2/12 done   saved 38.50 MB   overall eta 4m20s
```

When a file finishes, the panel collapses into a single summary line and the next
one starts.

```
  ✓ [03/12] product_demo_full.mov   142.30 MB → 14.20 MB   10% · saved 128.10 MB · 38.4s
```

---

## ▰ The ffmpeg pipeline

This is what runs under the hood (parameters change with your menu picks):

```bash
ffmpeg -i IN [-an | -c:a aac -b:a 128k] \
  -vf "scale='min(W,iw)':'min(H,ih)':force_original_aspect_ratio=decrease, \
       scale=trunc(iw/2)*2:trunc(ih/2)*2" \
  -pix_fmt yuv420p -preset medium -crf {26|28} \
  -movflags +faststart \
  [-c:v libx264 -profile:v high -level 4.0 \
   | -c:v libx265 -tag:v hvc1 -x265-params log-level=error] \
  OUT
```

| flag                   | why                                                         |
| ---------------------- | ----------------------------------------------------------- |
| `crf 26 / 28`          | visually transparent, 5-30% of original size                |
| `+faststart`           | `moov` atom up front → browsers play before full download   |
| `yuv420p`              | broadest browser/device compatibility                       |
| `high@4.0`             | safe H.264 profile for everything from Safari to old phones |
| `-tag:v hvc1`          | H.265 tag Apple devices actually recognize                  |
| `force_aspect:decrease`| cap into the target box without distorting portrait sources |
| `trunc(iw/2)*2`        | force even dimensions (yuv420p requires it)                 |

---

## ◇ How "atomic in-place" works

For every source `clip.mov`, CYBERTRANS:

1. Writes the encode to `.__cyber__.<pid>.clip.mov` next to the source
2. On success → `os.replace(temp, source)` (atomic on POSIX)
3. On failure → `temp.unlink()`, original is untouched

The `.__cyber__.*` pattern is gitignored and easy to grep for if a run is killed
mid-encode.

---

## ⌬ Dependencies

| layer  | needs                                                            |
| ------ | ---------------------------------------------------------------- |
| system | `ffmpeg` + `ffprobe` on `PATH` — auto-installable via Homebrew   |
| python | 3.9+, **stdlib only** — no `pip install` step                    |

Tested on macOS with stock `python3`. Should work on any Unix with ANSI-capable terminal.

---

## ▣ File layout

```
.
├── cybertrans          # bash launcher: `exec python3 cybertrans.py "$@"`
├── cybertrans.py       # everything: menu, ffmpeg orchestration, UI render loop
├── CLAUDE.md           # internal notes / agent instructions
└── README.md           # you are here
```

---

## ◈ Notes & caveats

- Directory inputs are scanned **flat, non-recursively** — intentional safety so dropping
  a parent folder by mistake doesn't recurse into `node_modules` and friends. Recurse by
  passing the deeper folder directly, or drop individual files.
- Recognized extensions: `.mp4 .mov .m4v .mkv .webm .avi`
- H.265 in `.webm` containers is not supported by ffmpeg — pick H.264 for `.webm` inputs.
- Audio re-encoding (`keep`) always re-encodes to AAC, even if source is already AAC.
  Trade-off for consistent, browser-safe output.

---

## ⌘ Built for

Born from a one-shot batch on a folder of UI prototype recordings — 21 videos, 393 MB
down to 44 MB, all faststart, all in-place. Same workflow, now as a CLI you can keep
around.

```
 ─── ░▒▓  cyberpunk hacker terminal vibes  ▓▒░ ───
```
