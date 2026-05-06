# ChatterTerminal Audio

Call signaling runs over WebSocket:

1. Caller sends `CALL_INVITE`.
2. Server forwards to callee.
3. Callee sends `CALL_ACCEPT` or `CALL_DECLINE`.
4. Both sides derive a session-bound call key.
5. Audio starts in relay mode and can use UDP relay fallback.

Audio pipeline:

```text
PyAudio capture
  -> Opus encode, or PCM fallback
  -> AES-256-GCM encrypt
  -> sequence + nonce + ciphertext
  -> WebSocket/UDP relay
  -> decrypt
  -> jitter buffer
  -> Opus decode, or PCM fallback
  -> PyAudio playback
```

Dependencies:

- `pyaudio` and system PortAudio are required for real microphone/speaker I/O.
- `opuslib` enables Opus compression. Without it, PCM fallback keeps the pipeline functional at higher bandwidth.

Commands:

```text
/call start @user
/call accept [session_id]
/call decline [session_id]
/call end [session_id]
/call mute
/call unmute
/call ptt on|off
/call talk on|off
/call history
```
