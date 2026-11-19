#!/usr/bin/env python3
"""Synthesize a narration segment with Doubao V3 and verify the resulting MP3."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import suppress
from enum import IntEnum
import getpass
import gzip
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import uuid


ENDPOINT = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
RESOURCE_ID = "seed-tts-2.0"
NAMESPACE = "BidirectionalTTS"
SERVICE_OK = 20000000
RECEIVE_TIMEOUT = 120
TEXT_CHUNK_BYTES = 800

PRESET_VOICES = {
    "zh_male_m191_uranus_bigtts": "云舟",
    "zh_male_liufei_uranus_bigtts": "刘飞 2.0",
    "zh_female_xiaohe_uranus_bigtts": "小何 2.0",
    "zh_female_vv_uranus_bigtts": "Vivi",
}
DEFAULT_SPEAKER = "zh_male_m191_uranus_bigtts"


class Event(IntEnum):
    START_CONNECTION = 1
    FINISH_CONNECTION = 2
    CONNECTION_STARTED = 50
    CONNECTION_FAILED = 51
    CONNECTION_FINISHED = 52
    START_SESSION = 100
    FINISH_SESSION = 102
    SESSION_STARTED = 150
    SESSION_CANCELED = 151
    SESSION_FINISHED = 152
    SESSION_FAILED = 153
    TASK_REQUEST = 200
    AUDIO = 352


class TtsError(Exception):
    """A user-facing problem with the input, audio file, or service."""


def read_api_key(key_stdin: bool) -> str:
    if not key_stdin:
        key = os.environ.get("DOUBAO_TTS_API_KEY", "").strip()
    elif sys.stdin.isatty():
        key = getpass.getpass("Doubao speech API key: ").strip()
    else:
        key = sys.stdin.readline().strip()
    if not key:
        raise TtsError("set DOUBAO_TTS_API_KEY or pass the key with --key-stdin")
    return key


def request_frame(event: Event, payload: dict, session: bytes = b"") -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    event_data = struct.pack(">I", event)
    if session:
        event_data += struct.pack(">I", len(session)) + session
    # V1, one 4-byte header word, full client request with event, JSON.
    return bytes.fromhex("11 14 10 00") + event_data + struct.pack(">I", len(body)) + body


def response_frame(frame: bytes) -> tuple[int, bytes, bytes]:
    if not isinstance(frame, bytes) or len(frame) < 4 or frame[0] >> 4 != 1:
        raise TtsError("invalid Doubao binary response")
    position = (frame[0] & 15) * 4
    if position < 4 or position > len(frame):
        raise TtsError("invalid Doubao header length")

    def read(size: int) -> bytes:
        nonlocal position
        end = position + size
        if end > len(frame):
            raise TtsError("truncated Doubao response")
        value = frame[position:end]
        position = end
        return value

    def read_number() -> int:
        return int.from_bytes(read(4), "big")

    kind, flags = divmod(frame[1], 16)
    if kind == 15:
        raise TtsError(f"Doubao service error {read_number()}")
    has_event = bool(flags & 4)
    if kind != 11 and (kind != 9 or not has_event):
        raise TtsError("unexpected Doubao response type")
    if flags & 1:
        read(4)  # Optional sequence number.

    event = read_number() if has_event else 0
    # Event-bearing frames carry a connection or session identifier. Raw
    # audio can arrive without an event in this dedicated connection.
    identifier = read(read_number()) if has_event else b""
    payload = read(read_number())
    if position != len(frame):
        raise TtsError("unexpected trailing data in Doubao response")

    compression = frame[2] & 15
    if compression == 1:
        try:
            payload = gzip.decompress(payload)
        except (OSError, EOFError) as error:
            raise TtsError("invalid compressed Doubao response") from error
    elif compression != 0:
        raise TtsError("unsupported Doubao response compression")

    if kind == 9 and event in (
        Event.CONNECTION_FAILED, Event.SESSION_CANCELED,
        Event.SESSION_FAILED, Event.SESSION_FINISHED,
    ):
        try:
            status = json.loads(payload)
            code = status.get("status_code", status.get("code"))
        except (ValueError, AttributeError) as error:
            raise TtsError(f"invalid Doubao status for event {event}") from error
        if event != Event.SESSION_FINISHED or code not in (None, SERVICE_OK):
            raise TtsError(f"Doubao event {event} failed (status {code})")
    if event == Event.AUDIO and kind != 11:
        raise TtsError("expected a binary Doubao audio response")
    return event, identifier, payload


def text_chunks(text: str) -> Iterator[str]:
    start = 0
    size = 0
    for index, character in enumerate(text):
        width = len(character.encode("utf-8"))
        if size + width > TEXT_CHUNK_BYTES:
            yield text[start:index]
            start = index
            size = 0
        size += width
    if start < len(text):
        yield text[start:]


def synthesise(text: str, speaker: str, api_key: str) -> bytes:
    try:
        from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
        from websockets.sync.client import connect
    except ImportError as error:
        raise TtsError('install websockets with: python3 -m pip install "websockets>=15,<17"') from error

    headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": RESOURCE_ID,
        "X-Api-Connect-Id": str(uuid.uuid4()),
    }
    session = str(uuid.uuid4()).encode("ascii")
    settings = {
        "speaker": speaker,
        "audio_params": {"format": "mp3", "sample_rate": 24000},
    }

    try:
        with connect(ENDPOINT, additional_headers=headers, open_timeout=15, close_timeout=3) as socket:
            socket.send(request_frame(Event.START_CONNECTION, {}))
            event, _, _ = response_frame(socket.recv(timeout=RECEIVE_TIMEOUT))
            if event != Event.CONNECTION_STARTED:
                raise TtsError(f"expected connection start, got event {event}")

            socket.send(request_frame(Event.START_SESSION, {
                "event": Event.START_SESSION, "namespace": NAMESPACE, "req_params": settings,
            }, session))
            event, identifier, _ = response_frame(socket.recv(timeout=RECEIVE_TIMEOUT))
            if event != Event.SESSION_STARTED or identifier != session:
                raise TtsError("Doubao did not confirm the requested synthesis session")

            for chunk in text_chunks(text):
                socket.send(request_frame(Event.TASK_REQUEST, {
                    "event": Event.TASK_REQUEST, "namespace": NAMESPACE,
                    "req_params": {**settings, "text": chunk},
                }, session))
            socket.send(request_frame(Event.FINISH_SESSION, {}, session))

            audio = bytearray()
            while True:
                event, identifier, data = response_frame(socket.recv(timeout=RECEIVE_TIMEOUT))
                if event == 0 and not identifier:
                    audio.extend(data)
                    continue
                if identifier != session:
                    raise TtsError(f"unexpected Doubao session in event {event}")
                if event == Event.SESSION_FINISHED:
                    break
                if event == Event.AUDIO:
                    audio.extend(data)

            if not audio:
                raise TtsError("Doubao finished the session without returning audio")
            with suppress(ConnectionClosed, TimeoutError, OSError, TtsError):
                socket.send(request_frame(Event.FINISH_CONNECTION, {}))
                response_frame(socket.recv(timeout=3))
            return bytes(audio)
    except InvalidStatus as error:
        raise TtsError(f"Doubao rejected the WebSocket connection (HTTP {error.response.status_code})") from error
    except TimeoutError as error:
        raise TtsError("Doubao timed out; check request status before retrying") from error
    except (OSError, WebSocketException) as error:
        raise TtsError("Doubao connection ended before synthesis finished; check request status before retrying") from error


def verify_mp3(path: Path) -> float:
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=format_name,duration",
        "-of", "json", str(path),
    ], capture_output=True, text=True)
    if probe.returncode:
        raise TtsError("ffprobe could not read the returned MP3")
    try:
        info = json.loads(probe.stdout)["format"]
        duration = float(info["duration"])
    except (ValueError, KeyError, TypeError) as error:
        raise TtsError("ffprobe could not verify the MP3 duration") from error
    if info.get("format_name") != "mp3" or not math.isfinite(duration) or duration <= 0:
        raise TtsError("Doubao returned invalid MP3 data")
    decoded = subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
        "-map", "0:a:0", "-f", "null", "-",
    ], capture_output=True)
    if decoded.returncode:
        raise TtsError("the returned MP3 could not be fully decoded")
    return duration


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-file", required=True, type=Path, help="one copy-ready narration segment")
    parser.add_argument("--output", required=True, type=Path, help="destination MP3")
    parser.add_argument("--speaker", choices=PRESET_VOICES, default=DEFAULT_SPEAKER)
    parser.add_argument("--resource-id", choices=[RESOURCE_ID], default=RESOURCE_ID)
    parser.add_argument("--key-stdin", action="store_true", help="read the key without using command arguments")
    parser.add_argument("--dry-run", action="store_true", help="check local inputs without calling Doubao")
    parser.add_argument("--force", action="store_true", help="overwrite an existing output after verification")
    args = parser.parse_args(argv)

    try:
        if args.output.suffix.lower() != ".mp3":
            raise TtsError("output must end in .mp3")
        if args.output.exists() and not args.force:
            raise TtsError(f"{args.output} already exists; pass --force to replace it")
        text = args.text_file.read_text(encoding="utf-8-sig").strip()
        if not text:
            raise TtsError(f"{args.text_file} is empty")

        print(f"voice: {PRESET_VOICES[args.speaker]} ({args.speaker}), "
              f"resource {RESOURCE_ID}, {len(text)} characters")
        if args.dry_run:
            print("dry run: local inputs checked; no authentication or service request")
            return 0

        if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
            raise TtsError("ffmpeg and ffprobe are required before requesting audio")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".doubao-", dir=args.output.parent) as folder:
            pending = Path(folder) / "segment.mp3"
            audio = synthesise(text, args.speaker, read_api_key(args.key_stdin))
            pending.write_bytes(audio)
            seconds = verify_mp3(pending)
            if args.force:
                os.replace(pending, args.output)
            else:
                os.link(pending, args.output)
        print(f"wrote {args.output} ({seconds:.2f}s, verified)")
        return 0
    except (TtsError, OSError, UnicodeError, EOFError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
