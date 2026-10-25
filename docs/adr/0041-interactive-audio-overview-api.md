# ADR-0041: Web Interactive Audio Overview signaling API

## Status

Accepted — initial Web implementation, with caller-owned WebRTC and media.

## Context

[Live spikes](../android/live-session-static-evidence.md#web-interactive-audio-wire-spike)
qualified Web and Android ICE/SDP signaling, artifact binding, Join control,
synthetic speech recognition and subsequent agent response events. Interactive
Audio Overviews use an existing artifact and WebRTC. They are distinct from
[Android Live Voice](0040-live-voice-session-api.md), which uses notebook-bound
bidirectional gRPC and protobuf audio bytes.

## Decision

Expose `client.interactive_audio.connect(notebook_id, artifact_id)` on Web. Require
an existing completed Audio Overview in that notebook; never generate one
implicitly. The context fetches parsed ICE configuration. Its session negotiates
one SDP exchange and builds setup/Join frames for caller-owned data channels.
Keep transport RPCs behind this feature and disable their retries globally.

The caller owns the peer connection, media tracks and transport callbacks. The SDK
owns signaling and a bounded synchronous AgentComms decoder, with no background
reader, lifetime RPC permit, event queue or native media dependency. Consequently,
parent-client closure cannot shut down the caller's WebRTC connection. Document
nested ownership and explicit peer cleanup. SDK session close is idempotent and
prevents further signaling or decoding. Canceling or failing negotiation retires
the session rather than risking replay after uncertain server allocation.

Return typed transcript, control and error notifications. Preserve observed
utterance fields without claiming normalized replacement/delta semantics. Expose
numeric control types only where verified; do not infer meanings for unqualified
status or playback payloads. Ignore unknown event kinds. Suppress raw private
error payloads. SDP, ICE credentials and transcript text are excluded from reprs.

Bound frames and aggregate partial-message reservations to 1 MiB and at most eight
incomplete messages. Enforce contiguous fragment offsets and consistent totals
and compression. Limit decompressed output too. Malformed or over-limit frames
raise sanitized `DecodingError` and retire the session.

Do not add automatic reconnect, SDP renegotiation, MCP, REST or Android dispatch
in this initial implementation. Preserve the immediate unsupported-backend error.
Android interactivity can later reuse the codec through its own qualified signaling
adapter; it does not make Web support general notebook Live Voice.

## Validation and limits

Replay the private Web and Android spike frames locally against the decoder,
without committing source-derived transcripts or credentials. Unit tests cover
signaling shapes, artifact selection, lifecycle, cancellation, chunking,
compression limits and sanitized failures. Pin Web RPC shapes in synthetic goldens
and their no-retry classifications in the policy registry tests.

The aiortc example is an optional integration sketch, not a qualified native-media
implementation. Live evidence used Chromium; waveform quality, all account
cohorts, quota details and reconnection remain unqualified.
