"""Check a cut timeline and optionally render a review MP4."""

import argparse
import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


@dataclass(frozen=True)
class Shot:
    path: Path
    kind: str
    frames: int
    source_ms: int
    motion: str


@dataclass(frozen=True)
class Timeline:
    width: int
    height: int
    fps: int
    shots: list[Shot]
    audio: list[tuple[Path, float]]

    @property
    def frames(self):
        return sum(shot.frames for shot in self.shots)


def integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def media(root, value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: expected a media path")
    path = (root / value).resolve()
    if not path.is_file():
        raise ValueError(f"{label}: file does not exist: {path}")
    return path


def load_timeline(path):
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError("timeline must be a JSON object")

    width = integer(manifest.get("width", 1080), "width", 2)
    height = integer(manifest.get("height", 1920), "height", 2)
    fps = integer(manifest.get("fps", 30), "fps", 1)
    if width % 2 or height % 2 or fps > 120:
        raise ValueError("width and height must be even, and fps must be <= 120")

    entries = manifest.get("shots")
    if not isinstance(entries, list) or not entries:
        raise ValueError("shots must be a nonempty array")

    shots = []
    seen = set()
    previous_end = 0
    previous_frame = 0
    for index, item in enumerate(entries, 1):
        label = f"shot {index}"
        if not isinstance(item, dict):
            raise ValueError(f"{label}: expected an object")
        shot_id = item.get("id")
        if not isinstance(shot_id, str) or not shot_id.strip() or shot_id in seen:
            raise ValueError(f"{label}: expected a unique nonempty id")
        seen.add(shot_id)
        label = f"shot {shot_id}"
        start = integer(item.get("start_ms"), f"{label} start_ms")
        end = integer(item.get("end_ms"), f"{label} end_ms")
        if start != previous_end or end <= start:
            raise ValueError(f"{label}: must start at {previous_end} ms and end later")
        end_frame = (end * fps + 500) // 1000
        if end_frame <= previous_frame:
            raise ValueError(f"{label}: no output frame after quantization")

        kind = item.get("kind")
        motion = item.get("motion", "none")
        source_ms = integer(item.get("source_ms", 0), f"{label} source_ms")
        if kind not in ("image", "video"):
            raise ValueError(f"{label}: kind must be image or video")
        if motion not in ("none", "push") or (kind == "video" and motion != "none"):
            raise ValueError(f"{label}: push motion requires a still image")
        if kind == "image" and source_ms:
            raise ValueError(f"{label}: source_ms requires video")
        source = media(path.parent, item.get("file"), label)
        shots.append(Shot(source, kind, end_frame - previous_frame, source_ms, motion))
        previous_end = end
        previous_frame = end_frame

    tracks = manifest.get("audio", [])
    if not isinstance(tracks, list):
        raise ValueError("audio must be an array")
    audio = []
    for index, item in enumerate(tracks, 1):
        label = f"audio {index}"
        if not isinstance(item, dict):
            raise ValueError(f"{label}: expected an object")
        gain = item.get("gain", 1.0)
        if type(gain) not in (int, float) or not 0 <= gain <= 2:
            raise ValueError(f"{label}: gain must be between 0 and 2")
        audio.append((media(path.parent, item.get("file"), label), float(gain)))

    return Timeline(width, height, fps, shots, audio)


def run(command, label):
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode:
        raise ValueError(f"{label}: {completed.stderr.strip() or 'command failed'}")
    return completed.stdout


def probe(path):
    return json.loads(run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        f"probe {path.name}",
    ))["streams"]


def render_shot(timeline, shot, destination):
    width, height, fps = timeline.width, timeline.height, timeline.fps
    command = ["ffmpeg", "-nostdin", "-v", "error", "-filter_threads", "1"]
    if shot.kind == "video":
        command += ["-ss", f"{shot.source_ms / 1000:.3f}"]
    elif shot.motion == "none":
        command += ["-loop", "1", "-framerate", str(fps)]
    command += ["-i", str(shot.path), "-map", "0:v:0"]

    multiplier = 2 if shot.motion == "push" else 1
    work_width, work_height = width * multiplier, height * multiplier
    filters = [
        f"scale={work_width}:{work_height}:force_original_aspect_ratio=increase",
        f"crop={work_width}:{work_height}",
        "setsar=1",
    ]
    if shot.motion == "push":
        span = max(1, shot.frames - 1)
        filters.append(
            f"zoompan=z='1+0.04*(1-cos(PI*on/{span}))':"
            f"x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
            f"d={shot.frames}:s={width}x{height}:fps={fps}"
        )
    else:
        filters.append(f"fps={fps}")

    command += [
        "-vf", ",".join(filters), "-frames:v", str(shot.frames), "-an",
        "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-threads", "2",
        "-pix_fmt", "yuv420p", "-video_track_timescale", str(fps * 1000),
        str(destination),
    ]
    run(command, f"render {shot.path.name}")
    streams = probe(destination)
    if not streams or int(streams[0].get("nb_frames", 0)) != shot.frames:
        raise ValueError(f"{shot.path.name}: source does not cover the requested frames")


def assemble(timeline, directory, target):
    manifest = directory / "cuts.txt"
    with manifest.open("w", encoding="utf-8") as handle:
        for index, shot in enumerate(timeline.shots):
            name = f"cut-{index:04}.mp4"
            render_shot(timeline, shot, directory / name)
            handle.write(f"file {name}\nduration {shot.frames / timeline.fps:.9f}\n")

    duration = timeline.frames / timeline.fps
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-f", "concat", "-safe", "1",
        "-i", str(manifest),
    ]
    for source, _ in timeline.audio:
        command += ["-i", str(source)]
    command += ["-map", "0:v:0", "-c:v", "copy"]
    if timeline.audio:
        filters = [
            f"[{index}:a:0]aresample=48000,aformat=channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS,volume={gain},apad,atrim=duration={duration:.9f}[a{index}]"
            for index, (_, gain) in enumerate(timeline.audio, 1)
        ]
        inputs = "".join(f"[a{index}]" for index in range(1, len(timeline.audio) + 1))
        filters.append(
            f"{inputs}amix=inputs={len(timeline.audio)}:normalize=0,"
            "alimiter=limit=0.95:level=0:latency=1[mix]"
        )
        command += [
            "-filter_complex", ";".join(filters), "-map", "[mix]", "-c:a", "aac",
            "-b:a", "192k",
        ]
    run(command + ["-movflags", "+faststart", str(target)], "assemble MP4")


def render(timeline, output):
    if output.suffix.lower() != ".mp4":
        raise ValueError("output must end in .mp4")
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            raise ValueError(f"{binary} is required to render")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".render-", dir=output.parent) as work:
        directory = Path(work)
        target = directory / "preview.mp4"
        assemble(timeline, directory, target)
        streams = probe(target)
        video = [stream for stream in streams if stream["codec_type"] == "video"]
        audio = [stream for stream in streams if stream["codec_type"] == "audio"]
        if len(video) != 1 or int(video[0].get("nb_frames", 0)) != timeline.frames:
            raise ValueError("output frame count does not match the timeline")
        if (video[0]["width"], video[0]["height"]) != (timeline.width, timeline.height):
            raise ValueError("output dimensions do not match the timeline")
        if len(audio) != bool(timeline.audio):
            raise ValueError("output audio stream does not match the timeline")
        if audio and not math.isclose(
            float(audio[0].get("duration", 0)), timeline.frames / timeline.fps, abs_tol=0.05
        ):
            raise ValueError("output audio duration does not match the timeline")
        os.link(target, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("timeline", type=Path)
    parser.add_argument("--output", type=Path, help="write a verified review MP4")
    args = parser.parse_args()
    try:
        timeline = load_timeline(args.timeline.resolve())
        if args.output:
            render(timeline, args.output.absolute())
            print(f"Rendered {args.output}")
        print(f"{len(timeline.shots)} shots, {timeline.frames} frames at {timeline.fps} fps")
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
