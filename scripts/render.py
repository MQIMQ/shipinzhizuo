#!/usr/bin/env python3
"""Slideshow renderer for video-story-workshop.

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
import os
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


def probe_media(path: Path, ffprobe: str) -> dict:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode:
        raise RenderError(f"ffprobe could not read {path}")
    return json.loads(result.stdout)


def audio_duration(info: dict, path: Path) -> float:
    audio = next((stream for stream in info["streams"] if stream["codec_type"] == "audio"), None)
    if audio is None:
        raise RenderError(f"no audio stream in {path}")
    try:
        duration = float(audio.get("duration") or info["format"]["duration"])
    except (KeyError, ValueError, TypeError) as exc:
        raise RenderError(f"no readable audio duration in {path}") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RenderError(f"invalid audio duration in {path}")
    return duration


def verify_output(path: Path, plan: dict, ffmpeg: str, ffprobe: str) -> None:
    info = probe_media(path, ffprobe)
    video = [stream for stream in info["streams"] if stream["codec_type"] == "video"]
    if len(video) != 1 or len(info["streams"]) != 2:
        raise RenderError("final MP4 needs exactly one video and one audio stream")
    stream = video[0]
    numerator, denominator = map(int, stream["r_frame_rate"].split("/"))
    if (
        (stream["width"], stream["height"]) != (plan["width"], plan["height"])
        or numerator != plan["fps"] * denominator
        or int(stream.get("nb_frames", 0)) != plan["frames"]
    ):
        raise RenderError("output dimensions, frame rate or frame count do not match the plan")
    if abs(audio_duration(info, path) - plan["audio_seconds"]) > plan["tolerance"]:
        raise RenderError("final MP4 does not retain the planned narration duration")
    run([ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", str(path),
         "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], "verify final MP4")


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
    if width <= 0 or height <= 0 or width % 2 or height % 2 or not 1 <= fps <= 120:
        raise RenderError("width and height must be positive even numbers; fps must be 1–120")
    if not 0 <= crf <= 51:
        raise RenderError("crf must be between 0 and 51")
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
        if not math.isfinite(first) or first <= COVER_SECONDS:
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
        if not math.isfinite(duration) or duration <= 0:
            raise RenderError("shot durations must be positive")
        segments.append({"kind": "shot",
                         "image": resolve(shot["image"], "shot image"),
                         "duration": duration})

    elapsed, cursor = 0.0, 0
    for segment in segments:
        elapsed += segment["duration"]
        end_frame = round(elapsed * fps)
        if end_frame <= cursor:
            raise RenderError(f"shot {segment['image'].name} is shorter than one output frame")
        segment["frames"] = end_frame - cursor
        cursor = end_frame

    return {"width": width, "height": height, "fps": fps, "crf": crf,
            "audio": audio, "segments": segments, "total": cursor / fps, "frames": cursor}


def check_narration(plan: dict, ffprobe: str) -> None:
    info = probe_media(plan["audio"], ffprobe)
    measured = audio_duration(info, plan["audio"])
    # MP3 container duration can include encoder padding. Quantized
    # picture boundaries can differ from the planned time by one frame.
    tolerance = 1 / plan["fps"] + 0.05
    if abs(plan["total"] - measured) > tolerance:
        raise RenderError(
            f"timeline {plan['total']:.3f}s vs narration {measured:.3f}s; "
            "adjust shot durations before rendering"
        )
    plan["audio_seconds"] = measured
    plan["tolerance"] = tolerance


def render_segment(image: Path, kind: str, frames: int, width: int,
                   height: int, fps: int, crf: int, out_path: Path,
                   ffmpeg: str) -> None:
    """Render exactly the requested number of frames from a still image."""
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
        ffmpeg, "-hide_banner", "-loglevel", "error", "-filter_threads", "1",
        "-loop", "1", "-framerate", str(fps), "-i", str(image),
        "-vf", video_filter,
        "-frames:v", str(frames),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
        "-an", "-y", str(out_path),
    ], f"render segment {image.name}")


def fade_lengths(frames: list[int], fps: int) -> list[int]:
    maximum = max(1, round(CROSSFADE_SECONDS * fps))
    return [min(maximum, left, right) for left, right in zip(frames, frames[1:])]


def join_segments(segment_files: list[Path], nominal: list[int], fades: list[int],
                  fps: int, crf: int, out_path: Path, ffmpeg: str) -> None:
    """Crossfade padded clips at the frame boundaries in the plan."""
    inputs = [token for path in segment_files for token in ("-i", str(path))]
    filters = []
    current = "0:v"
    offset = 0
    for index, fade in enumerate(fades, 1):
        offset += nominal[index - 1]
        target = f"x{index}"
        filters.append(
            f"[{current}][{index}:v]xfade=transition=fade:"
            f"duration={fade / fps:.9f}:offset={offset / fps:.9f}[{target}]"
        )
        current = target
    filters.append(f"[{current}]format=yuv420p[vout]")
    run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-filter_complex_threads", "1",
        *inputs, "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-r", str(fps),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
        "-an", "-y", str(out_path),
    ], "crossfade segments")


def render_project(plan: dict, output: Path, ffmpeg: str, ffprobe: str, force: bool) -> None:
    if output.suffix.lower() != ".mp4":
        raise RenderError("output must end in .mp4")
    if output.exists() and not force:
        raise RenderError(f"{output} already exists; choose a new filename or use --force")
    output.parent.mkdir(parents=True, exist_ok=True)
    segments = plan["segments"]
    nominal = [segment["frames"] for segment in segments]
    fades = fade_lengths(nominal, plan["fps"])

    with tempfile.TemporaryDirectory(prefix=".story-render-", dir=output.parent) as folder:
        directory = Path(folder)
        clips = []
        for index, segment in enumerate(segments):
            clip = directory / f"seg{index:03d}.mp4"
            clip_frames = nominal[index] + (fades[index] if index < len(fades) else 0)
            render_segment(segment["image"], segment["kind"], clip_frames,
                           plan["width"], plan["height"], plan["fps"], plan["crf"],
                           clip, ffmpeg)
            clips.append(clip)

        silent = clips[0]
        if fades:
            silent = directory / "silent.mp4"
            join_segments(clips, nominal, fades, plan["fps"], plan["crf"], silent, ffmpeg)
        final = directory / "final.mp4"
        run([
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", str(silent), "-i", str(plan["audio"]),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", str(final),
        ], "mux full narration")
        verify_output(final, plan, ffmpeg, ffprobe)
        if force:
            os.replace(final, output)
        else:
            os.link(final, output)


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
    parser.add_argument("--force", action="store_true", help="replace an existing verified output")
    args = parser.parse_args(argv)

    try:
        plan = load_plan(args.plan)
        check_narration(plan, args.ffprobe)
        print(f"plan: {len(plan['segments'])} segments, "
              f"{plan['width']}x{plan['height']} @{plan['fps']}fps, "
              f"{plan['frames']} frames, narration {plan['audio_seconds']:.3f}s")
        if not args.check_only:
            render_project(plan, args.output, args.ffmpeg, args.ffprobe, args.force)
            print(f"done: {args.output} ({plan['total']:.3f}s, frame count and audio verified)")
        return 0
    except (OSError, RenderError, KeyError, ValueError, TypeError, OverflowError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
