#!/usr/bin/env python3
"""Minimal Doubao TTS client for liao-video-studio.

Synthesises one narration segment to mp3 using the Doubao speech
synthesis 2.0 service (single-direction streaming HTTP API). Only the
four built-in preset voices are accepted; there is no voice cloning
and no custom speaker.

Preset voices (resource_id is always ``seed-tts-2.0``):

- 云舟 (default)  ``zh_male_m191_uranus_bigtts``
- 刘飞 2.0        ``zh_male_liufei_uranus_bigtts``
- 小何 2.0        ``zh_female_xiaohe_uranus_bigtts``
- Vivi            ``zh_female_vv_uranus_bigtts``

The API key is never written to a file. Provide it either through the
``DOUBAO_TTS_API_KEY`` environment variable or with ``--key-stdin``
(reads one line from standard input, no echo, no storage).

Usage::

    export DOUBAO_TTS_API_KEY="your key"
    python3 doubao_tts.py --text-file seg1.txt --output seg1.mp3
    python3 doubao_tts.py --text-file seg1.txt --output seg1.mp3 --dry-run
    python3 doubao_tts.py --text-file seg1.txt --output seg1.mp3 \\
        --speaker zh_female_xiaohe_uranus_bigtts

The service answers with newline-delimited JSON; each line carries a
base64 audio chunk, and a final line with code 20000000 marks the end.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
RESOURCE_ID = "seed-tts-2.0"
SUCCESS_CODE = 20000000

PRESET_VOICES = {
    "zh_male_m191_uranus_bigtts": "云舟",
    "zh_male_liufei_uranus_bigtts": "刘飞 2.0",
    "zh_female_xiaohe_uranus_bigtts": "小何 2.0",
    "zh_female_vv_uranus_bigtts": "Vivi",
}
DEFAULT_SPEAKER = "zh_male_m191_uranus_bigtts"


class TtsError(Exception):
    """A user-facing problem with the request or the service."""


def read_api_key(key_stdin: bool) -> str:
    """Return the API key from the environment or secure stdin input."""
    if key_stdin:
        key = sys.stdin.readline().strip()
    else:
        key = os.environ.get("DOUBAO_TTS_API_KEY", "").strip()
    if not key:
        raise TtsError(
            "no API key: set DOUBAO_TTS_API_KEY or pass --key-stdin "
            "(the key is read once and never stored)"
        )
    return key


def build_request(text: str, speaker: str) -> dict:
    """Build the V3 unidirectional request body."""
    return {
        "user": {"uid": "liao-video-studio"},
        "req_params": {
            "speaker": speaker,
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24000,
                "speech_rate": 0,
                "loudness_rate": 0,
            },
            "text_type": "plain",
        },
        "text": text,
    }


def synthesise(text: str, speaker: str, api_key: str) -> bytes:
    """Send one synthesis request; return the decoded mp3 bytes."""
    payload = json.dumps(build_request(text, speaker)).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT, data=payload, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": api_key,
            "X-Api-Resource-Id": RESOURCE_ID,
        },
    )
    try:
        response = urllib.request.urlopen(request, timeout=120)
    except urllib.error.HTTPError as exc:
        raise TtsError(f"service returned HTTP {exc.code}: {exc.reason}")
    except urllib.error.URLError as exc:
        raise TtsError(f"network error: {exc.reason}")

    audio = bytearray()
    finished = False
    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                raise TtsError("service returned a line that is not JSON")
            code = message.get("code")
            if code != SUCCESS_CODE:
                detail = message.get("message") or message.get("msg") or ""
                raise TtsError(f"service error code {code}: {detail}")
            chunk = message.get("data")
            if chunk:
                try:
                    audio += base64.b64decode(chunk)
                except ValueError:
                    raise TtsError("service returned a bad audio chunk")
            else:
                # A success line with no audio chunk marks the end.
                finished = True
    if not finished and not audio:
        raise TtsError("service ended without audio and without a finish mark")
    if not audio:
        raise TtsError("service returned no audio")
    return bytes(audio)


def check_mp3(path: Path) -> None:
    """Decode-check the written mp3 with ffprobe when available."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        print("warning: ffprobe could not verify the mp3 "
              "(is ffprobe installed?)", file=sys.stderr)
        return
    try:
        seconds = float(result.stdout.strip())
    except ValueError:
        print("warning: ffprobe returned no duration for the mp3",
              file=sys.stderr)
        return
    print(f"verified: {seconds:.2f}s of audio")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-file", required=True, type=Path,
                        help="narration text file (one segment)")
    parser.add_argument("--output", required=True, type=Path,
                        help="output mp3 file")
    parser.add_argument("--speaker", default=DEFAULT_SPEAKER,
                        help="preset voice id (default 云舟)")
    parser.add_argument("--resource-id", default=RESOURCE_ID,
                        help="must stay seed-tts-2.0 for preset voices")
    parser.add_argument("--key-stdin", action="store_true",
                        help="read the API key from stdin instead of the "
                             "environment")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate everything without sending a request")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing output file")
    args = parser.parse_args(argv)

    if args.speaker not in PRESET_VOICES:
        print(f"error: unknown speaker {args.speaker!r}; "
              f"choose one of: {', '.join(PRESET_VOICES)}",
              file=sys.stderr)
        return 2
    if args.speaker.startswith("S_"):
        print("error: cloned voices are not supported", file=sys.stderr)
        return 2
    if args.resource_id != RESOURCE_ID:
        print(f"error: resource_id must be {RESOURCE_ID} for preset voices",
              file=sys.stderr)
        return 2
    if args.output.exists() and not args.force:
        print(f"error: {args.output} already exists; pass --force to "
              f"overwrite it", file=sys.stderr)
        return 2
    try:
        text = args.text_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"error: cannot read {args.text_file}: {exc}", file=sys.stderr)
        return 2
    if not text:
        print(f"error: {args.text_file} is empty", file=sys.stderr)
        return 2

    label = PRESET_VOICES[args.speaker]
    print(f"voice: {label} ({args.speaker}), resource {RESOURCE_ID}, "
          f"{len(text)} characters")
    if args.dry_run:
        print("dry run: request validated, nothing sent, no charge")
        return 0

    try:
        api_key = read_api_key(args.key_stdin)
        audio = synthesise(text, args.speaker, api_key)
    except TtsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    args.output.write_bytes(audio)
    print(f"wrote {args.output} ({len(audio)} bytes)")
    check_mp3(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
