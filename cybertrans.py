#!/usr/bin/env python3
"""CYBERTRANS — neon terminal video transcoder.

Configurable codec (H.264/H.265), resolution cap (FHD/4K), and audio.
Drop a folder or files, originals get atomically overwritten with
web-optimized (faststart) versions.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


def c(n: int) -> str:
    return f"\033[38;5;{n}m"


RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
HIDE_CURSOR, SHOW_CURSOR = "\033[?25l", "\033[?25h"
CLEAR_LINE = "\033[2K\r"
CLEAR_TO_END = "\033[J"

HOTPINK = c(199)
PINK = c(201)
MAGENTA = c(165)
PURPLE = c(141)
DARK = c(54)
SKY = c(45)
CYAN = c(51)
AQUA = c(87)
LIME = c(118)
GREEN = c(46)
YELLOW = c(226)
GOLD = c(214)
ORANGE = c(208)
RED = c(196)
WHITE = c(231)
GREY = c(240)

GRADIENT = [HOTPINK, PINK, MAGENTA, PURPLE, SKY, CYAN]

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}

BANNER = r"""
   ██████╗██╗   ██╗██████╗ ███████╗██████╗ ████████╗██████╗  █████╗ ███╗   ██╗███████╗
  ██╔════╝╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗╚══██╔══╝██╔══██╗██╔══██╗████╗  ██║██╔════╝
  ██║      ╚████╔╝ ██████╔╝█████╗  ██████╔╝   ██║   ██████╔╝███████║██╔██╗ ██║███████╗
  ██║       ╚██╔╝  ██╔══██╗██╔══╝  ██╔══██╗   ██║   ██╔══██╗██╔══██║██║╚██╗██║╚════██║
  ╚██████╗   ██║   ██████╔╝███████╗██║  ██║   ██║   ██║  ██║██║  ██║██║ ╚████║███████║
   ╚═════╝   ╚═╝   ╚═════╝ ╚══════╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝╚══════╝"""

SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


@dataclass(frozen=True)
class Settings:
    codec: str          # "h264" | "h265"
    max_res: str        # "fhd"  | "4k"
    keep_audio: bool

    @property
    def res_dims(self) -> tuple[int, int]:
        return (1920, 1080) if self.max_res == "fhd" else (3840, 2160)

    @property
    def res_label(self) -> str:
        return "FHD" if self.max_res == "fhd" else "4K"

    @property
    def codec_label(self) -> str:
        return "h264" if self.codec == "h264" else "h265"

    @property
    def crf(self) -> str:
        # h265 crf 28 ≈ h264 crf 26 in perceived quality
        return "26" if self.codec == "h264" else "28"

    def summary(self) -> str:
        a = "audio:keep" if self.keep_audio else "audio:strip"
        return f"{self.codec_label} · {self.res_label} cap · crf{self.crf} · {a} · faststart"


@dataclass
class RunState:
    """Mutable state shared with the renderer for overall queue stats."""
    total_files: int = 0
    done_files: int = 0
    bytes_before_done: int = 0
    bytes_after_done: int = 0
    duration_done: float = 0.0       # total video-seconds of files already finished
    duration_remaining: float = 0.0  # total video-seconds of files not yet started
    real_elapsed_done: float = 0.0   # wall-clock seconds spent on finished files
    current_duration: float = 0.0    # duration of the file in flight
    current_progress: float = 0.0    # 0..1 of current file
    current_started_at: float = field(default_factory=time.time)


def print_banner() -> None:
    print()
    for i, line in enumerate(BANNER.strip("\n").splitlines()):
        color = GRADIENT[i % len(GRADIENT)]
        print(f"{color}{BOLD}{line}{RESET}")
    sub = "  ░▒▓  N E O N  ·  V I D E O  ·  O P T I M I Z E R  ▓▒░"
    print(f"  {CYAN}{DIM}{sub}{RESET}")
    print(f"  {PURPLE}{'─' * 86}{RESET}\n")


def human(n: float) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:6.2f} {unit}"
        n /= 1024
    return f"{n:6.2f} TB"


def fmt_eta(sec: float) -> str:
    if sec is None or sec < 0 or sec != sec or sec == float("inf"):
        return "--:--"
    sec = int(sec)
    if sec >= 3600:
        return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"
    return f"{sec // 60:02d}:{sec % 60:02d}"


def parse_paths(raw: str) -> list[Path]:
    raw = raw.strip()
    if not raw:
        return []
    try:
        parts = shlex.split(raw, posix=True)
    except ValueError:
        parts = [raw]
    out: list[Path] = []
    seen: set[str] = set()
    for p in parts:
        if not p:
            continue
        if (p.startswith("'") and p.endswith("'")) or (
            p.startswith('"') and p.endswith('"')
        ):
            p = p[1:-1]
        pp = Path(p).expanduser()
        try:
            pp = pp.resolve()
        except OSError:
            pass
        key = str(pp)
        if key not in seen:
            seen.add(key)
            out.append(pp)
    return out


def collect_videos(paths: list[Path]) -> tuple[list[Path], str]:
    """Return (sorted videos, display label)."""
    videos: list[Path] = []
    for p in paths:
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            videos.append(p)
        elif p.is_dir():
            videos.extend(
                q for q in p.iterdir()
                if q.is_file() and q.suffix.lower() in VIDEO_EXTS
            )
    videos = sorted(set(videos), key=lambda x: str(x).lower())

    if len(paths) == 1 and paths[0].is_dir():
        label = str(paths[0])
    elif len(paths) == 1:
        label = str(paths[0].parent)
    else:
        label = f"{len(paths)} items"
    return videos, label


def probe_duration(path: Path) -> float:
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stderr=subprocess.DEVNULL,
        )
        return float(out.strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return 0.0


def render_bar(percent: float, width: int = 28) -> str:
    filled = int(round(percent * width))
    out = ""
    third = width / 3
    for i in range(width):
        if i < filled:
            if i < third:
                shade = HOTPINK
            elif i < 2 * third:
                shade = PINK
            else:
                shade = PURPLE
            out += f"{shade}█"
        else:
            out += f"{DARK}░"
    return out + RESET


def render_thin_bar(percent: float, width: int = 22) -> str:
    filled = int(round(percent * width))
    out = ""
    for i in range(width):
        ch = "▰" if i < filled else "▱"
        col = CYAN if i < filled else DARK
        out += f"{col}{ch}"
    return out + RESET


def build_ffmpeg_cmd(src: Path, dst: Path, s: Settings) -> list[str]:
    w, h = s.res_dims
    vf = (
        f"scale='min({w},iw)':'min({h},ih)':"
        f"force_original_aspect_ratio=decrease,"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )
    cmd: list[str] = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-y", "-i", str(src),
    ]
    if s.keep_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-an"]

    cmd += [
        "-vf", vf,
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-crf", s.crf,
        "-movflags", "+faststart",
    ]
    if s.codec == "h264":
        cmd += [
            "-c:v", "libx264",
            "-profile:v", "high",
            "-level", "4.0",
        ]
    else:
        cmd += [
            "-c:v", "libx265",
            "-tag:v", "hvc1",
            "-x265-params", "log-level=error",
        ]
    cmd += [
        "-progress", "pipe:1", "-nostats",
        str(dst),
    ]
    return cmd


def choose(title: str, options: list[tuple[str, str]], default: int = 1) -> int:
    print(f"  {HOTPINK}◆ {WHITE}{BOLD}{title}{RESET}")
    for i, (label, hint) in enumerate(options, 1):
        marker_col = HOTPINK if i == default else PURPLE
        print(
            f"    {marker_col}[{i}]{RESET} "
            f"{WHITE}{label:<8}{RESET} {GREY}{hint}{RESET}"
        )
    while True:
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.flush()
        try:
            raw = input(
                f"  {CYAN}▸{RESET} {DIM}select [1-{len(options)}, "
                f"default {default}]:{RESET} "
            ).strip()
        finally:
            sys.stdout.write(HIDE_CURSOR)
            sys.stdout.flush()
        if not raw:
            print()
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            print()
            return int(raw)
        print(f"  {YELLOW}⚠ invalid, try again{RESET}")


def prompt_settings() -> Settings:
    print(f"  {PURPLE}╭─[ {WHITE}CONFIG{PURPLE} ]{'─' * 70}{RESET}")
    print(f"  {PURPLE}╰{'─' * 86}{RESET}\n")

    codec_idx = choose("CODEC", [
        ("H.264", "wide compatibility · fast decode"),
        ("H.265", "~30-50% smaller · modern devices"),
    ])
    res_idx = choose("MAX RESOLUTION", [
        ("FHD", "cap 1920×1080  ·  downscale only"),
        ("4K",  "cap 3840×2160  ·  downscale only"),
    ])
    audio_idx = choose("AUDIO", [
        ("strip", "no audio track  (smaller, silent)"),
        ("keep",  "re-encode AAC 128k  (browser-safe)"),
    ])
    return Settings(
        codec="h264" if codec_idx == 1 else "h265",
        max_res="fhd" if res_idx == 1 else "4k",
        keep_audio=(audio_idx == 2),
    )


def get_inputs() -> list[Path]:
    if len(sys.argv) > 1:
        return parse_paths(" ".join(sys.argv[1:]))
    print(
        f"  {HOTPINK}▸{RESET} {WHITE}drop folder(s) or file(s) here & press "
        f"{BOLD}ENTER{RESET}{WHITE}:{RESET}"
    )
    sys.stdout.write(f"  {CYAN}::{RESET} ")
    sys.stdout.write(SHOW_CURSOR)
    sys.stdout.flush()
    try:
        raw = input()
    finally:
        sys.stdout.write(HIDE_CURSOR)
        sys.stdout.flush()
    return parse_paths(raw)


def transcode_one(
    src: Path,
    tmp: Path,
    duration: float,
    settings: Settings,
    idx: int,
    total: int,
    run: RunState,
) -> tuple[bool, str, int]:
    cmd = build_ffmpeg_cmd(src, tmp, settings)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    err_buf: list[str] = []

    def stderr_reader() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            err_buf.append(line)

    t = threading.Thread(target=stderr_reader, daemon=True)
    t.start()

    state = {
        "fps": 0.0,
        "speed": 0.0,
        "bitrate": "",
        "size": 0,
        "frame": 0,
    }
    spin_i = 0
    last_render = 0.0
    rendered_lines = 0
    full_label = src.name
    label_short = (
        full_label if len(full_label) <= 38 else full_label[:35] + "..."
    )

    def overall_eta() -> float:
        # average speed = video-seconds processed / real seconds elapsed
        proc_video = run.duration_done + run.current_duration * run.current_progress
        real = run.real_elapsed_done + (time.time() - run.current_started_at)
        if real <= 0 or proc_video <= 0:
            return -1.0
        avg_speed = proc_video / real
        remaining_video = (
            run.duration_remaining
            + run.current_duration * (1.0 - run.current_progress)
        )
        if avg_speed <= 0:
            return -1.0
        return remaining_video / avg_speed

    def render(done: bool = False) -> None:
        nonlocal spin_i, rendered_lines
        sp = SPINNER[spin_i % len(SPINNER)]
        spin_i += 1
        bar = render_bar(run.current_progress, 30)
        pct = f"{run.current_progress * 100:5.1f}%"
        tag = f"[{idx:>2}/{total}]"

        # per-file eta
        if state["speed"] > 0.01 and duration > 0:
            remaining = duration * (1.0 - run.current_progress)
            eta_file = remaining / state["speed"]
        else:
            eta_file = -1.0

        line1 = (
            f"  {CYAN}{sp}{RESET} "
            f"{GREY}{tag}{RESET} "
            f"{WHITE}{label_short:<38}{RESET}  "
            f"{bar}  {LIME}{pct}{RESET}"
        )
        line2 = (
            f"     "
            f"{GREY}speed{RESET} {AQUA}{state['speed']:>4.1f}x{RESET}  "
            f"{GREY}fps{RESET} {AQUA}{state['fps']:>5.1f}{RESET}  "
            f"{GREY}br{RESET} {MAGENTA}{(state['bitrate'] or '--'):<11}{RESET}"
            f"{GREY}eta{RESET} {HOTPINK}{fmt_eta(eta_file):<7}{RESET}"
            f"{GREY}size{RESET} {YELLOW}{human(state['size'])}{RESET}"
        )

        # overall queue line
        done_for_bar = run.done_files + run.current_progress
        q_pct = done_for_bar / max(run.total_files, 1)
        qbar = render_thin_bar(q_pct, 22)
        saved = run.bytes_before_done - run.bytes_after_done
        # currently saving estimate based on current tmp size & before
        eta_total = overall_eta()
        line3 = (
            f"     {DIM}{GREY}queue{RESET}  {qbar}  "
            f"{WHITE}{run.done_files:>2}/{run.total_files}{RESET} done   "
            f"{GREY}saved{RESET} {LIME}{human(max(saved, 0))}{RESET}   "
            f"{GREY}overall eta{RESET} {HOTPINK}{fmt_eta(eta_total)}{RESET}"
        )

        if rendered_lines:
            sys.stdout.write(f"\033[{rendered_lines}F{CLEAR_TO_END}")
        sys.stdout.write(line1 + "\n" + line2 + "\n" + line3 + "\n")
        sys.stdout.flush()
        rendered_lines = 3

    render()

    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if k == "out_time" and duration > 0 and ":" in v:
            try:
                h, m, s_ = v.split(":")
                sec = int(h) * 3600 + int(m) * 60 + float(s_)
                run.current_progress = max(0.0, min(1.0, sec / duration))
            except ValueError:
                pass
        elif k == "fps":
            try:
                state["fps"] = float(v)
            except ValueError:
                pass
        elif k == "speed":
            try:
                state["speed"] = float(v.rstrip("x").strip() or "0")
            except ValueError:
                pass
        elif k == "bitrate":
            state["bitrate"] = v
        elif k == "total_size":
            try:
                state["size"] = int(v)
            except ValueError:
                pass
        elif k == "frame":
            try:
                state["frame"] = int(v)
            except ValueError:
                pass
        elif k == "progress" and v == "end":
            run.current_progress = 1.0

        now = time.time()
        if now - last_render > 0.1:
            render()
            last_render = now

    proc.wait()
    t.join(timeout=0.5)
    if proc.returncode != 0:
        return False, "".join(err_buf).strip(), state["size"]
    run.current_progress = 1.0
    render(done=True)
    # erase the 3 progress lines so caller can print a summary
    sys.stdout.write(f"\033[3F{CLEAR_TO_END}")
    sys.stdout.flush()
    return True, "", state["size"]


def write_summary_line(
    idx: int,
    total: int,
    src: Path,
    before: int,
    after: int,
    success: bool,
    err: str,
    elapsed: float,
) -> None:
    full = src.name
    label_short = full if len(full) <= 38 else full[:35] + "..."
    if success:
        ratio = (after / before * 100) if before else 0
        delta = before - after
        sys.stdout.write(
            f"  {GREEN}✓{RESET} "
            f"{GREY}[{idx:>2}/{total}]{RESET} "
            f"{WHITE}{label_short:<38}{RESET}  "
            f"{YELLOW}{human(before)}{RESET} "
            f"{GREY}→{RESET} "
            f"{LIME}{human(after)}{RESET}  "
            f"{DIM}{ratio:>3.0f}% · saved {human(max(delta, 0))} · "
            f"{elapsed:.1f}s{RESET}\n"
        )
    else:
        sys.stdout.write(
            f"  {RED}✘{RESET} "
            f"{GREY}[{idx:>2}/{total}]{RESET} "
            f"{WHITE}{label_short:<38}{RESET}  {RED}failed{RESET}\n"
        )
        if err:
            for ln in err.splitlines()[:3]:
                sys.stdout.write(f"      {DIM}{RED}{ln}{RESET}\n")
    sys.stdout.flush()


def ensure_ffmpeg() -> bool:
    """Detect ffmpeg/ffprobe; if missing, offer to install via Homebrew.

    Returns True if both tools are available after this call.
    """
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if not missing:
        return True

    print(
        f"\n  {YELLOW}⚠ missing on PATH:{RESET} "
        f"{WHITE}{', '.join(missing)}{RESET}"
    )

    brew = shutil.which("brew")
    if not brew:
        if sys.platform == "darwin":
            print(
                f"  {GREY}install Homebrew first "
                f"({CYAN}https://brew.sh{GREY}), then:{RESET} "
                f"{WHITE}brew install ffmpeg{RESET}\n"
            )
        elif sys.platform.startswith("linux"):
            print(
                f"  {GREY}install with your package manager, e.g.:{RESET}\n"
                f"    {WHITE}sudo apt install ffmpeg{RESET}   "
                f"{DIM}# debian/ubuntu{RESET}\n"
                f"    {WHITE}sudo dnf install ffmpeg{RESET}   "
                f"{DIM}# fedora{RESET}\n"
            )
        else:
            print(
                f"  {GREY}install ffmpeg manually from{RESET} "
                f"{CYAN}https://ffmpeg.org/download.html{RESET}\n"
            )
        return False

    print(f"  {GREY}Homebrew detected at{RESET} {DIM}{brew}{RESET}")
    sys.stdout.write(SHOW_CURSOR)
    sys.stdout.flush()
    try:
        ans = input(
            f"  {HOTPINK}▸{RESET} {WHITE}install ffmpeg via brew now? "
            f"{DIM}[y/N]{RESET} "
        ).strip().lower()
    finally:
        sys.stdout.write(HIDE_CURSOR)
        sys.stdout.flush()
    if ans not in ("y", "yes", "s", "si", "sí"):
        print(
            f"\n  {YELLOW}skipped.{RESET}  "
            f"{GREY}run `brew install ffmpeg` and try again.{RESET}\n"
        )
        return False

    print(
        f"\n  {CYAN}▸ brew install ffmpeg{RESET}  "
        f"{DIM}// this may take a few minutes{RESET}\n"
    )
    sys.stdout.write(SHOW_CURSOR)
    sys.stdout.flush()
    try:
        result = subprocess.run([brew, "install", "ffmpeg"], check=False)
    except OSError as e:
        sys.stdout.write(HIDE_CURSOR)
        sys.stdout.flush()
        print(f"\n  {RED}✘ install failed:{RESET} {e}\n")
        return False
    finally:
        sys.stdout.write(HIDE_CURSOR)
        sys.stdout.flush()

    if result.returncode != 0:
        print(
            f"\n  {RED}✘ brew install ffmpeg failed "
            f"(exit {result.returncode}).{RESET}\n"
        )
        return False

    still_missing = [
        t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None
    ]
    if still_missing:
        print(
            f"\n  {RED}✘ still missing after install:{RESET} "
            f"{WHITE}{', '.join(still_missing)}{RESET}\n"
            f"  {GREY}restart your shell or check PATH.{RESET}\n"
        )
        return False

    print(f"\n  {GREEN}✓ ffmpeg ready.{RESET}\n")
    return True


def main() -> int:
    sys.stdout.write(HIDE_CURSOR)
    sys.stdout.flush()
    try:
        if not ensure_ffmpeg():
            return 1

        print_banner()
        settings = prompt_settings()

        # show effective settings
        print(f"  {PURPLE}╭─[ {WHITE}LOCKED{PURPLE} ]{'─' * 70}{RESET}")
        print(f"  {PURPLE}│{RESET} {DIM}{settings.summary()}{RESET}")
        print(f"  {PURPLE}╰{'─' * 86}{RESET}\n")

        inputs = get_inputs()
        if not inputs:
            print(f"\n  {RED}✘ no input provided.{RESET}\n")
            return 1

        missing = [p for p in inputs if not p.exists()]
        if missing:
            print(f"\n  {RED}✘ not found:{RESET}")
            for p in missing:
                print(f"      {DIM}{p}{RESET}")
            print()
            return 1

        videos, target_label = collect_videos(inputs)
        if not videos:
            print(f"\n  {YELLOW}⚠ no videos found in input.{RESET}\n")
            return 0

        print()
        print(f"  {CYAN}╭─[ {WHITE}TARGET{CYAN} ]{'─' * 70}{RESET}")
        print(f"  {CYAN}│{RESET} {DIM}{target_label}{RESET}")
        print(
            f"  {CYAN}├─[ {WHITE}QUEUE · {len(videos)} files{CYAN} ]"
            f"{'─' * (60 - len(str(len(videos))))}{RESET}"
        )
        total_before = 0
        durations: list[float] = []
        for i, v in enumerate(videos, 1):
            sz = v.stat().st_size
            total_before += sz
            dur = probe_duration(v)
            durations.append(dur)
            dlabel = fmt_eta(dur) if dur > 0 else "  --  "
            name = v.name if len(v.name) <= 42 else v.name[:39] + "..."
            print(
                f"  {CYAN}│{RESET} {PURPLE}{i:>2}.{RESET} "
                f"{WHITE}{name:<42}{RESET}  "
                f"{YELLOW}{human(sz)}{RESET}  "
                f"{GREY}{dlabel}{RESET}"
            )
        print(f"  {CYAN}╰{'─' * 86}{RESET}")
        print(
            f"  {GREY}total:{RESET} {YELLOW}{human(total_before)}{RESET}   "
            f"{GREY}runtime:{RESET} {YELLOW}{fmt_eta(sum(durations))}{RESET}\n"
        )

        sys.stdout.write(SHOW_CURSOR)
        ans = input(
            f"  {HOTPINK}▸{RESET} {WHITE}proceed? originals will be "
            f"overwritten {DIM}[y/N]{RESET} "
        ).strip().lower()
        sys.stdout.write(HIDE_CURSOR)
        sys.stdout.flush()
        if ans not in ("y", "yes", "s", "si", "sí"):
            print(f"\n  {YELLOW}aborted.{RESET}\n")
            return 0

        print()
        print(f"  {PURPLE}{'═' * 86}{RESET}")
        print(
            f"  {HOTPINK}{BOLD}▼ TRANSCODING{RESET}  "
            f"{DIM}// {settings.summary()}{RESET}"
        )
        print(f"  {PURPLE}{'═' * 86}{RESET}\n")

        run = RunState(
            total_files=len(videos),
            duration_remaining=sum(durations),
        )

        results: list[tuple[str, int, int, bool, str]] = []
        t_start = time.time()
        for idx, (v, dur) in enumerate(zip(videos, durations), 1):
            tmp = v.with_name(f".__cyber__.{os.getpid()}.{v.name}")
            before = v.stat().st_size
            run.current_duration = dur
            run.current_progress = 0.0
            run.current_started_at = time.time()
            run.duration_remaining = max(
                0.0, run.duration_remaining - dur
            )

            ok, err, after_size = transcode_one(
                v, tmp, dur, settings, idx, len(videos), run
            )
            file_elapsed = time.time() - run.current_started_at

            if ok and tmp.exists():
                after = tmp.stat().st_size
                try:
                    os.replace(tmp, v)
                except OSError as e:
                    sys.stdout.write(
                        f"\n  {RED}✘ replace failed:{RESET} {e}\n"
                    )
                    if tmp.exists():
                        try:
                            tmp.unlink()
                        except OSError:
                            pass
                    results.append((v.name, before, before, False, str(e)))
                    run.done_files += 1
                    run.real_elapsed_done += file_elapsed
                    continue
                write_summary_line(
                    idx, len(videos), v, before, after, True, "", file_elapsed
                )
                results.append((v.name, before, after, True, ""))
                run.bytes_before_done += before
                run.bytes_after_done += after
            else:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                write_summary_line(
                    idx, len(videos), v, before, before, False, err, file_elapsed
                )
                results.append((v.name, before, before, False, err))
                run.bytes_before_done += before
                run.bytes_after_done += before

            run.done_files += 1
            run.duration_done += dur
            run.real_elapsed_done += file_elapsed

        elapsed = time.time() - t_start
        total_after = sum(r[2] for r in results)
        ok_count = sum(1 for r in results if r[3])
        fail_count = len(results) - ok_count
        pct_total = (total_after / total_before * 100) if total_before else 0
        saved_total = max(total_before - total_after, 0)

        print()
        print(f"  {PURPLE}{'═' * 86}{RESET}")
        print(f"  {HOTPINK}{BOLD}▲ DONE{RESET}  {DIM}// {settings.summary()}{RESET}")
        print(f"  {PURPLE}{'═' * 86}{RESET}")
        print(
            f"  {CYAN}files{RESET}    {WHITE}{len(results)}{RESET}   "
            f"{GREEN}ok {ok_count}{RESET}   "
            f"{RED}fail {fail_count}{RESET}"
        )
        print(f"  {CYAN}before{RESET}   {YELLOW}{human(total_before)}{RESET}")
        print(
            f"  {CYAN}after{RESET}    {LIME}{human(total_after)}{RESET}  "
            f"{DIM}({pct_total:.0f}% of original){RESET}"
        )
        print(f"  {CYAN}saved{RESET}    {LIME}{human(saved_total)}{RESET}")
        print(f"  {CYAN}elapsed{RESET}  {WHITE}{fmt_eta(elapsed)}{RESET}\n")
        return 0 if fail_count == 0 else 2
    finally:
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.flush()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stdout.write(SHOW_CURSOR + f"\n  {YELLOW}interrupted.{RESET}\n")
        sys.exit(130)
