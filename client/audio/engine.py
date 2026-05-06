"""
PyAudio capture and playback engine.

All PyAudio imports are wrapped in a try/except so this module loads
cleanly even when PyAudio (or the underlying PortAudio shared library)
is not installed.  Callers should check :attr:`AudioEngine.available`
before using capture or playback.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Awaitable, Callable

try:
    import pyaudio as _pyaudio
    _PYAUDIO_AVAILABLE = True
except ImportError:
    _pyaudio = None  # type: ignore[assignment]
    _PYAUDIO_AVAILABLE = False


class AudioEngine:
    """Async-friendly PyAudio capture and playback.

    Bridges the synchronous PyAudio callback thread with asyncio by
    routing captured PCM frames through an :class:`asyncio.Queue`.

    Args:
        sample_rate: Audio sample rate in Hz (default 48000).
        channels:    Number of audio channels (default 1 = mono).
        chunk_ms:    Capture chunk duration in milliseconds (default 20).
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 1,
        chunk_ms: int = 20,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunk_ms = chunk_ms
        self._frames_per_buffer: int = sample_rate * chunk_ms // 1000

        self._pa: "_pyaudio.PyAudio | None" = None
        self._capture_stream: "_pyaudio.Stream | None" = None
        self._playback_stream: "_pyaudio.Stream | None" = None

        # asyncio loop reference (set when start_capture/start_playback called)
        self._loop: asyncio.AbstractEventLoop | None = None

        # Queue bridges the PyAudio callback thread → asyncio consumer
        self._capture_queue: asyncio.Queue[bytes] | None = None

        self._capture_task: asyncio.Task | None = None
        self._running: bool = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """True if PyAudio is importable and usable on this system."""
        return _PYAUDIO_AVAILABLE

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_pyaudio(self) -> "_pyaudio.PyAudio":
        """Initialise and cache the PyAudio instance.

        Raises:
            RuntimeError: If PyAudio is not available.
        """
        if not _PYAUDIO_AVAILABLE:
            raise RuntimeError(
                "PyAudio is not available. Install it with: pip install pyaudio"
            )
        if self._pa is None:
            self._pa = _pyaudio.PyAudio()  # type: ignore[union-attr]
        return self._pa

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_capture(
        self,
        callback: Callable[[bytes], Awaitable[None]],
    ) -> None:
        """Open a PyAudio input stream and start delivering PCM frames.

        Captured 20 ms PCM frames are fed through *callback* as they arrive.
        The actual PyAudio callback runs in a PortAudio thread; frames are
        safely hand-off to the asyncio event loop via a queue.

        Args:
            callback: An async callable that receives raw PCM bytes.

        Raises:
            RuntimeError: If PyAudio is not available.
        """
        pa = self._ensure_pyaudio()
        self._loop = asyncio.get_event_loop()
        self._capture_queue = asyncio.Queue(maxsize=64)
        self._running = True

        def _pyaudio_callback(
            in_data: bytes,
            frame_count: int,
            time_info: dict,
            status: int,
        ) -> tuple[None, int]:
            """PyAudio input callback — called from PortAudio thread."""
            if self._running and self._loop is not None and self._capture_queue is not None:
                try:
                    # Thread-safe bridge: put_nowait into the asyncio queue
                    self._loop.call_soon_threadsafe(
                        self._capture_queue.put_nowait, in_data
                    )
                except asyncio.QueueFull:
                    pass  # Drop frame if consumer is too slow
            return (None, _pyaudio.paContinue)  # type: ignore[union-attr]

        self._capture_stream = pa.open(
            format=_pyaudio.paInt16,  # type: ignore[union-attr]
            channels=self._channels,
            rate=self._sample_rate,
            input=True,
            frames_per_buffer=self._frames_per_buffer,
            stream_callback=_pyaudio_callback,
        )
        self._capture_stream.start_stream()

        # Drain the capture queue and call the user callback
        async def _drain() -> None:
            assert self._capture_queue is not None
            while self._running:
                try:
                    pcm = await asyncio.wait_for(
                        self._capture_queue.get(), timeout=1.0
                    )
                    await callback(pcm)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception:
                    # Log-worthy but not fatal — keep draining
                    continue

        self._capture_task = asyncio.ensure_future(_drain())

    async def start_playback(self) -> None:
        """Open a PyAudio output stream ready to receive PCM frames.

        Call :meth:`play_frame` to write PCM data to the stream.

        Raises:
            RuntimeError: If PyAudio is not available.
        """
        pa = self._ensure_pyaudio()

        self._playback_stream = pa.open(
            format=_pyaudio.paInt16,  # type: ignore[union-attr]
            channels=self._channels,
            rate=self._sample_rate,
            output=True,
            frames_per_buffer=self._frames_per_buffer,
        )
        self._playback_stream.start_stream()

    def play_frame(self, pcm: bytes) -> None:
        """Write a PCM frame to the playback stream.

        This is a synchronous, non-blocking write.  It is safe to call
        from within an asyncio coroutine as long as the write buffer does
        not fill up.

        Args:
            pcm: Raw 16-bit little-endian PCM bytes.
        """
        if self._playback_stream is not None:
            try:
                self._playback_stream.write(pcm)
            except OSError:
                pass  # Stream closed or underrun — ignore

    def stop(self) -> None:
        """Stop and close both capture and playback streams."""
        self._running = False

        if self._capture_task is not None:
            self._capture_task.cancel()
            self._capture_task = None

        if self._capture_stream is not None:
            try:
                self._capture_stream.stop_stream()
                self._capture_stream.close()
            except Exception:
                pass
            self._capture_stream = None

        if self._playback_stream is not None:
            try:
                self._playback_stream.stop_stream()
                self._playback_stream.close()
            except Exception:
                pass
            self._playback_stream = None

        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
