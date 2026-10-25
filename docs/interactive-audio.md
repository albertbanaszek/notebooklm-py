# Interactive Audio Overviews

The Web backend supports interacting with an existing, completed Audio Overview.
Use `client.interactive_audio.connect(notebook_id, artifact_id)` as an async context
manager. This validates the artifact and fetches ICE configuration; it does not
generate an overview or connect a media transport on its own.

The SDK handles signaling and binary control messages. Your application owns a
WebRTC peer connection, microphone capture, playback, data channels and their
cleanup. There is no bundled media dependency. A caller-owned aiortc integration
is shown in [the example](../examples/interactive_audio.py).

Within the context:

1. Build your peer connection from `session.ice_servers` (parsed `IceServer`
   records) and honor `session.ice_transport_policy`. ICE credentials are private;
   do not log them or SDP.
2. Add your audio track and create a `data-channel`. Set your local description
   and finish ICE gathering. Pass the resulting SDP string to
   `await session.negotiate(offer_sdp)`, then apply its `AudioSessionDescription`
   as your remote description.
3. When the server-created data channel opens, send `session.setup_frame()` once.
   This binds the connection to the selected Audio Overview artifact.
4. Send `session.join_frame()` when the listener requests a turn. Overview
   playback alone does not open a listener turn. Decode microphone controls to
   track the server's response; successful local sends are not acknowledgments.
5. Feed binary data-channel frames serially into `session.receive(frame)`. It
   returns a tuple of typed events, or an empty tuple while a fragmented message
   is incomplete. Deliver events without blocking your transport callback.
6. Close the peer connection in `finally`, inside the SDK session context. Stop
   capture and playback resources owned by your application too.

`AudioTranscript` identifies input (`user`) or output (`model`), the server item ID
when present, the exact text field and the explicit `finished` marker. It does not
promise transcript delta/snapshot normalization. `AudioControl` identifies TTS,
send-audio, playback, microphone and status categories. Numeric `type` is exposed
only where verified; playback and status have `type=None`. These are control
notifications; audio media travels over WebRTC RTP. `AudioAgentError` signals an
agent-side error without exposing its unqualified payload. Unknown message kinds
are ignored.

There is no implicit renegotiation, reconnection or retry of signaling. A failed
or canceled SDP exchange retires the SDK session because the server may already
have allocated resources. Close your peer connection and explicitly start a new
session if desired. Malformed frames also retire the session. Reassembly is
bounded to 1 MiB across at most eight partial messages, with bounded gzip
expansion. There is no SDK background reader or event queue.

Session exit clears decoder state and prevents further SDK operations. Closing
the parent client stops further signaling, but cannot close caller-owned WebRTC
resources. Keep the peer and session nested inside the parent client context.

This API is currently Web-only. Unsupported backend selection raises
`UnsupportedOperationError` before any feature RPC. Android signaling was also
qualified in a separate spike, but is not exposed through this API yet. General
Android Live Voice (`StreamLiveSession`) is a separate notebook-bound protocol;
this API does not substitute an Audio Overview for it. MCP and REST do not expose
this media session.

The [wire-spike evidence](android/live-session-static-evidence.md#web-interactive-audio-wire-spike)
qualified signaling, Join, synthetic input recognition, RTP and response events on
one account. Native playback quality, other cohorts, natural quota refusals and
resume continuity remain unqualified.
