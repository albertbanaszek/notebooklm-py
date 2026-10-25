"""Public signaling and event types for Interactive Audio Overviews.

Callers own their WebRTC peer connection, data channels and media tracks. This
module has no WebRTC dependency and does not capture or play audio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class IceServer:
    """Parsed ICE server configuration; credentials are excluded from repr."""

    urls: tuple[str, ...]
    username: str | None = field(default=None, repr=False)
    credential: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class AudioSessionDescription:
    """SDP answer suitable for a caller-owned WebRTC peer connection."""

    type: Literal["answer"]
    sdp: str = field(repr=False)


@dataclass(frozen=True)
class AudioTranscript:
    """Server utterance text; retain the server item ID when supplied.

    No delta/snapshot normalization is promised. Text is the exact text field
    from this utterance message; ``finished`` is the explicit wire marker.
    """

    role: Literal["user", "model"]
    text: str = field(repr=False)
    item_id: str | None = None
    finished: bool = False


@dataclass(frozen=True)
class AudioControl:
    """Observed control category and numeric wire type, without guessed semantics."""

    kind: Literal["tts", "send_audio", "playback", "microphone", "status"]
    type: int | None


@dataclass(frozen=True)
class AudioAgentError:
    """An agent error event, without exposing its unqualified private payload."""


AudioEvent = AudioTranscript | AudioControl | AudioAgentError


__all__ = [
    "IceServer",
    "AudioSessionDescription",
    "AudioTranscript",
    "AudioControl",
    "AudioAgentError",
    "AudioEvent",
]
