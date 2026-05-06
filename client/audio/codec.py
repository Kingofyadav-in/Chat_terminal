"""
Opus audio codec with transparent PCM fallback.

Attempts to import opuslib at module load time.  If opuslib is not
installed, all encode/decode calls pass the raw PCM bytes through
unchanged so the rest of the audio pipeline can still function (at the
cost of much higher bandwidth usage).
"""

from __future__ import annotations

try:
    import opuslib
    import opuslib.api.encoder as _opus_enc_api
    import opuslib.api.decoder as _opus_dec_api
    _OPUSLIB_AVAILABLE = True
except ImportError:
    opuslib = None  # type: ignore[assignment]
    _OPUSLIB_AVAILABLE = False


class AudioCodec:
    """Opus encoder/decoder with PCM passthrough fallback.

    Args:
        sample_rate:        Audio sample rate in Hz (default 48000).
        channels:           Number of audio channels (default 1 = mono).
        frame_duration_ms:  Frame duration in milliseconds (default 20).
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 1,
        frame_duration_ms: int = 20,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._frame_duration_ms = frame_duration_ms

        self._encoder = None
        self._decoder = None

        if _OPUSLIB_AVAILABLE:
            try:
                self._encoder = opuslib.Encoder(
                    sample_rate,
                    channels,
                    opuslib.APPLICATION_VOIP,
                )
                self._decoder = opuslib.Decoder(sample_rate, channels)
            except Exception:
                # opuslib present but Opus shared library missing or broken
                self._encoder = None
                self._decoder = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def frame_size(self) -> int:
        """Number of PCM samples per frame.

        For 48 kHz mono audio at 20 ms per frame: 48000 * 20 // 1000 = 960.
        """
        return self._sample_rate * self._frame_duration_ms // 1000

    @property
    def using_opus(self) -> bool:
        """True if the Opus codec is active; False if falling back to PCM."""
        return self._encoder is not None and self._decoder is not None

    # ------------------------------------------------------------------
    # Encode / Decode
    # ------------------------------------------------------------------

    def encode(self, pcm_bytes: bytes) -> bytes:
        """Encode a PCM frame to Opus.

        If Opus is not available the PCM bytes are returned unchanged.

        Args:
            pcm_bytes: Raw 16-bit little-endian PCM samples
                       (length = frame_size * channels * 2).

        Returns:
            Opus-encoded bytes, or the original PCM bytes if Opus is unavailable.
        """
        if not self.using_opus:
            return pcm_bytes

        try:
            encoded: bytes = self._encoder.encode(pcm_bytes, self.frame_size)
            return encoded
        except Exception:
            # Graceful degradation: return raw PCM on encoder error
            return pcm_bytes

    def decode(self, encoded: bytes) -> bytes:
        """Decode an Opus frame to raw 16-bit PCM.

        If Opus is not available the encoded bytes are returned unchanged
        (assuming they are already raw PCM in fallback mode).

        Args:
            encoded: Opus-encoded frame bytes (or raw PCM in fallback mode).

        Returns:
            Raw 16-bit little-endian PCM bytes, or the input unchanged if
            Opus is unavailable.
        """
        if not self.using_opus:
            return encoded

        try:
            decoded: bytes = self._decoder.decode(encoded, self.frame_size)
            return decoded
        except Exception:
            # Graceful degradation: return the bytes as-is on decoder error
            return encoded
