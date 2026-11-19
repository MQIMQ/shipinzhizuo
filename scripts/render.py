#!/usr/bin/env python3
"""Slideshow renderer for liao-video-studio.

Builds an mp4 from still images and a narration mp3, following the
project's fixed look:

- Output is 1080x1440 (3:4) by default, 30 fps, h264 + aac.
- Each shot slowly pushes in; neighbouring shots crossfade for 0.8 s.
- An optional cover image replaces the first 0.5 s of the first shot:
  it is shown static, the narration plays from 0 s as usual, and the
  total length and audio sync do not change.

Input is a plan.json file, for example::

    {
      "width": 1080, "height": 1440, "fps": 30, "crf": 18,
      "cover": "cover.jpg",
      "audio": "audio/narration.mp3",
      "shots": [
        {"image": "images/shot1.jpg", "duration": 10.0},
        {"image": "images/shot2.jpg", "duration": 10.0}
      ]
    }

Shot durations must add up to the narration length (they come from the
per-segment durations measured in step 4 of the workflow). Image paths
are resolved relative to the plan file unless they are absolute.

Usage::

    python3 render.py --plan plan.json --output final.mp4
    python3 render.py --plan plan.json --check-only
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

COVER_SECONDS = 0.5
CROSSFADE_SECONDS = 0.8
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1440
DEFAULT_FPS = 30
DEFAULT_CRF = 18
ZOOM_STEP = 0.0012
ZOOM_MAX = 1.10


class RenderError(Exception):
    """A user-facing problem with the plan or the render."""


def run(command: list[str], description: str) -> None:
    """Run an ffmpeg/ffprobe command, raising RenderError on failure."""
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-5:]
        raise RenderError(
            f"{description} failed (exit {result.returncode}):\n"
            + "\n".join(tail)
        )


def probe_duration(path: Path, ffprobe: str) -> float:
    """Return the duration in seconds of an audio/video file."""
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RenderError(f"ffprobe could not read {path}")
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RenderError(f"ffprobe returned no duration for {path}")


def probe_video(path: Path, ffprobe: str) -> dict:
    """Return width, height, fps and stream kinds of a video file."""
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RenderError(f"ffprobe could not read {path}")
    info = json.loads(result.stdout)
    stream = info["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    return {
        "width": stream["width"],
        "height": stream["height"],
        "fps": int(num) / int(den),
        "duration": float(info["format"]["duration"]),
        "has_audio": _has_stream(path, ffprobe, "a"),
    }


def _has_stream(path: Path, ffprobe: str, kind: str) -> bool:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", f"{kind}:0",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.returncode == 0 and result.stdout.strip() != ""


def load_plan(plan_path: Path) -> dict:
    """Read and validate a plan file; return resolved segments."""
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RenderError(f"cannot read plan {plan_path}: {exc}")
    base = plan_path.resolve().parent

    def resolve(value: str, field: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = base / path
        if not path.is_file():
            raise RenderError(f"{field} not found: {value}")
        return path

    width = int(plan.get("width", DEFAULT_WIDTH))
    height = int(plan.get("height", DEFAULT_HEIGHT))
    fps = int(plan.get("fps", DEFAULT_FPS))
    crf = int(plan.get("crf", DEFAULT_CRF))
    if width <= 0 or height <= 0 or fps <= 0:
        raise RenderError("width, height and fps must be positive")
    audio = resolve(plan["audio"], "audio") if "audio" in plan else None
    if audio is None:
        raise RenderError('plan needs an "audio" file')
    shots = plan.get("shots") or []
    if not shots:
        raise RenderError('plan needs at least one shot in "shots"')

    cover = None
    if plan.get("cover"):
        cover = resolve(plan["cover"], "cover")

    # Nominal (visible) durations. A cover takes the first 0.5 s of the
    # first shot instead of adding time, so the total never changes.
    segments: list[dict] = []
    if cover is not None:
        first = float(shots[0]["duration"])
        if first <= COVER_SECONDS:
            raise RenderError(
                "first shot must be longer than 0.5 s when a cover is set"
            )
        segments.append({"kind": "cover", "image": cover,
                         "duration": COVER_SECONDS})
        segments.append({"kind": "shot", "image": resolve(shots[0]["image"], "shot image"),
                         "duration": first - COVER_SECONDS})
        start = 1
    else:
        start = 0
    for shot in shots[start:]:
        duration = float(shot["duration"])
        if duration <= 0:
            raise RenderError("shot durations must be positive")
        segments.append({"kind": "shot",
                         "image": resolve(shot["image"], "shot image"),
                         "duration": duration})

    total = sum(seg["duration"] for seg in segments)
    return {"width": width, "height": height, "fps": fps, "crf": crf,
            "audio": audio, "segments": segments, "total": total}


def render_segment(image: Path, kind: str, seconds: float, width: int,
                   height: int, fps: int, crf: int, out_path: Path,
                   ffmpeg: str) -> None:
    """Render one still image into a video segment of `seconds`."""
    if kind == "cover":
        # Static: fill the frame, no push-in.
        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},format=yuv420p"
        )
    else:
        # Slow push-in from a 2x upscale so the zoom stays sharp.
        video_filter = (
            f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
            f"crop={width * 2}:{height * 2},"
            f"zoompan=z='min(zoom+{ZOOM_STEP},{ZOOM_MAX})':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:"
            f"s={width}x{height}:fps={fps},format=yuv420p"
        )
    run([
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-framerate", str(fps), "-i", str(image),
        "-vf", video_filter,
        "-t", f"{seconds:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
        "-an", "-y", str(out_path),
    ], f"render segment {image.name}")


def join_segments(segment_files: list[Path], nominal: list[float],
                  width: int, height: int, fps: int, crf: int,
                  out_path: Path, ffmpeg: str, tmpdir: Path) -> None:
    """Crossfade the rendered segments into one silent video."""
    count = len(segment_files)
    if count == 1:
        run([
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", str(segment_files[0]),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
            "-an", "-y", str(out_path),
        ], "copy single segment")
        return
    # Per-boundary crossfade, clamped so it never exceeds either side.
    fades = [min(CROSSFADE_SECONDS, nominal[k], nominal[k + 1])
             for k in range(count - 1)]
    # Each segment was rendered longer by its outgoing fade; offsets are
    # cumulative nominal durations, so the total stays exact.
    offsets = [sum(nominal[:k + 1]) for k in range(count - 1)]
    inputs: list[str] = []
    for path in segment_files:
        inputs += ["-i", str(path)]
    filters: list[str] = []
    current = "0:v"
    for k in range(count - 1):
        out_label = f"x{k}"
        filters.append(
            f"[{current}][{k + 1}:v]xfade=transition=fade:"
            f"duration={fades[k]:.3f}:offset={offsets[k]:.3f}[{out_label}]"
        )
        current = out_label
    filters.append(f"[{current}]format=yuv420p[vout]")
    run([
        ffmpeg, "-hide_banner", "-loglevel", "error",
        *inputs, "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-r", str(fps),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
        "-an", "-y", str(out_path),
    ], "crossfade segments")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path,
                        help="plan.json file")
    parser.add_argument("--output", type=Path, default=Path("final.mp4"),
                        help="output mp4 file (default final.mp4)")
    parser.add_argument("--check-only", action="store_true",
                        help="validate the plan without rendering")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args(argv)

    try:
        plan = load_plan(args.plan)
    except RenderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    segments = plan["segments"]
    nominal = [seg["duration"] for seg in segments]
    print(f"plan: {len(segments)} segments, "
          f"{plan['width']}x{plan['height']} @{plan['fps']}fps, "
          f"total {plan['total']:.2f}s")
    for index, seg in enumerate(segments):
        print(f"  [{index}] {seg['kind']:5s} {seg['duration']:6.2f}s  "
              f"{seg['image'].name}")
    if args.check_only:
        return 0

    # Render each segment longer by its outgoing crossfade, except the
    # last one; the xfade overlaps then bring the total back to exact.
    fades = [min(CROSSFADE_SECONDS, nominal[k], nominal[k + 1])
             for k in range(len(nominal) - 1)]
    rendered = [
        nominal[k] + (fades[k] if k < len(fades) else 0.0)
        for k in range(len(nominal))
    ]

    with tempfile.TemporaryDirectory(prefix="liao-render-") as tmp:
        tmpdir = Path(tmp)
        segment_files: list[Path] = []
        for index, seg in enumerate(segments):
            out = tmpdir / f"seg{index:03d}.mp4"
            render_segment(seg["image"], seg["kind"], rendered[index],
                           plan["width"], plan["height"], plan["fps"],
                           plan["crf"], out, args.ffmpeg)
            segment_files.append(out)
        silent = tmpdir / "silent.mp4"
        join_segments(segment_files, nominal, plan["width"], plan["height"],
                      plan["fps"], plan["crf"], silent, args.ffmpeg, tmpdir)
        run([
            args.ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", str(silent), "-i", str(plan["audio"]),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-shortest", "-movflags", "+faststart",
            "-y", str(args.output),
        ], "mux audio")

    info = probe_video(args.output, args.ffprobe)
    audio_len = probe_duration(plan["audio"], args.ffprobe)
    print(f"done: {args.output}  {info['width']}x{info['height']} "
          f"@{info['fps']:.0f}fps  {info['duration']:.2f}s  "
          f"audio {'yes' if info['has_audio'] else 'NO'}")
    if abs(info["duration"] - audio_len) > 1.0:
        print(f"warning: video {info['duration']:.2f}s vs audio "
              f"{audio_len:.2f}s differ by more than 1 s; "
              f"check the segment durations", file=sys.stderr)
    if (info["width"], info["height"]) != (plan["width"], plan["height"]):
        print("warning: output size does not match the plan",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
