"""Bounded AgentComms framing recovered from the shipped Web notebook bundle."""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Literal

from .exceptions import DecodingError
from .interactive_audio import AudioAgentError, AudioControl, AudioEvent, AudioTranscript


def varint(value: int) -> bytes:
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def field(number: int, value: bytes | str) -> bytes:
    if isinstance(value, str):
        value = value.encode()
    return varint(number * 8 + 2) + varint(len(value)) + value


def fields(data: bytes) -> dict[int, int | bytes]:
    """Read singular fields, skipping unknown fixed-width fields safely."""
    pos = 0

    def read_varint() -> int:
        nonlocal pos
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(data):
                raise ValueError
            byte = data[pos]
            pos += 1
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
        raise ValueError

    result: dict[int, int | bytes] = {}
    while pos < len(data):
        key = read_varint()
        number, wire = key >> 3, key & 7
        if not number:
            raise ValueError
        if wire == 0:
            result[number] = read_varint()
            continue
        if wire == 2:
            size = read_varint()
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
        else:
            raise ValueError
        end = pos + size
        if end > len(data):
            raise ValueError
        if wire == 2:
            result[number] = data[pos:end]
        pos = end
    return result


def blob(fs: dict[int, int | bytes], number: int, default: bytes = b"") -> bytes:
    value = fs.get(number, default)
    if not isinstance(value, bytes):
        raise ValueError
    return value


def integer(fs: dict[int, int | bytes], number: int) -> int:
    value = fs.get(number, 0)
    if not isinstance(value, int):
        raise ValueError
    return value


def setup_frame(artifact_id: str, message_id: int) -> bytes:
    config = field(1, artifact_id) + field(2, b"") + b"\x20\x01"
    any_config = field(1, "type.googleapis.com/gemfm.agents.GroundedAgentConfig") + field(2, config)
    payload = field(1, field(1, field(1, "grounded_agent") + field(2, any_config)))
    return envelope(payload, message_id)


def envelope(payload: bytes, message_id: int) -> bytes:
    chunk = b"\x08" + varint(message_id) + b"\x10" + varint(len(payload))
    return field(2, chunk + b"\x18\x00" + field(4, payload))


@dataclass
class _Chunk:
    total: int
    compression: int
    data: bytearray


class Decoder:
    """Reassemble bounded messages; reject malformed or ambiguous fragments."""

    def __init__(self, max_bytes: int = 1024 * 1024) -> None:
        self.max_bytes = max_bytes
        self.chunks: dict[int, _Chunk] = {}

    def clear(self) -> None:
        self.chunks.clear()

    def feed(self, data: bytes) -> tuple[AudioEvent, ...]:
        try:
            return self._feed(data)
        except (ValueError, UnicodeError, zlib.error):
            self.clear()
            raise DecodingError("Invalid Interactive Audio Overview data-channel message") from None

    def _feed(self, data: bytes) -> tuple[AudioEvent, ...]:
        if len(data) > self.max_bytes:
            raise ValueError
        outer = fields(data)
        compression = integer(outer, 3)
        if compression not in (0, 1) or (1 in outer and 2 in outer):
            raise ValueError
        payload = blob(outer, 1)
        if 2 in outer:
            fragment = fields(blob(outer, 2))
            ident, total, offset = (integer(fragment, n) for n in (1, 2, 3))
            part = blob(fragment, 4)
            if total <= 0 or total > self.max_bytes or not part:
                raise ValueError
            chunk = self.chunks.get(ident)
            if chunk is None:
                if offset or len(self.chunks) >= 8:
                    raise ValueError
                # Reserve declared sizes, not just received bytes.
                if sum(c.total for c in self.chunks.values()) + total > self.max_bytes:
                    raise ValueError
                chunk = _Chunk(total, compression, bytearray())
                self.chunks[ident] = chunk
            if (total, compression, offset) != (chunk.total, chunk.compression, len(chunk.data)):
                raise ValueError
            if len(part) > total - offset:
                raise ValueError
            chunk.data.extend(part)
            if len(chunk.data) != total:
                return ()
            payload = bytes(chunk.data)
            del self.chunks[ident]
        if not payload:
            return ()
        if compression == 1:
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            payload = decoder.decompress(payload, self.max_bytes + 1)
            if len(payload) > self.max_bytes or not decoder.eof or decoder.unused_data:
                raise ValueError
        return decode_events(payload)


def decode_events(payload: bytes) -> tuple[AudioEvent, ...]:
    result: list[AudioEvent] = []
    for number, value in fields(payload).items():
        if number not in range(1, 10):
            continue
        if not isinstance(value, bytes):
            raise ValueError
        if number in (1, 2):
            utterance = fields(value)
            result.append(
                AudioTranscript(
                    role="user" if number == 1 else "model",
                    text=blob(utterance, 3).decode(),
                    item_id=blob(utterance, 5).decode() or None,
                    finished=bool(integer(utterance, 4)),
                )
            )
        elif number == 9:
            result.append(AudioAgentError())
        elif number != 5:
            kinds: dict[int, Literal["tts", "send_audio", "playback", "microphone", "status"]] = {
                3: "tts",
                4: "send_audio",
                6: "playback",
                7: "microphone",
                8: "status",
            }
            result.append(
                AudioControl(
                    kinds[number],
                    integer(fields(value), 1) if number in (3, 4, 7) else None,
                )
            )
    return tuple(result)
