"""
UDP audio jitter buffer for reordering out-of-order packets.

The jitter buffer holds a sliding window of received packets and returns
them in strict sequence-number order, tolerating network reordering within
the window size while keeping end-to-end latency bounded.
"""

from __future__ import annotations


class JitterBuffer:
    """A fixed-size sequence-ordered buffer for real-time UDP audio packets.

    Packets are stored by their sequence number and popped in order.
    Late packets (more than 2 * buffer_size behind the current head) are
    silently discarded.  If the buffer is full, the head advances to the
    earliest packet present to prevent stalling.

    Args:
        size: Maximum number of packets to hold before forcing a skip.
    """

    def __init__(self, size: int = 8) -> None:
        if size < 1:
            raise ValueError("JitterBuffer size must be at least 1")
        self._max_size: int = size
        # Maps seq → frame bytes
        self._buffer: dict[int, bytes] = {}
        # The next sequence number we expect to pop
        self._next_seq: int = 0
        # Whether the first packet has been received (to initialise _next_seq)
        self._initialised: bool = False
        # True once at least one packet has been successfully popped; after
        # the first pop we never lower _next_seq again (stream is advancing).
        self._popped: bool = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def push(self, seq: int, frame: bytes) -> None:
        """Insert a packet into the buffer.

        Late packets (seq < _next_seq - 2*_max_size) are dropped.
        Duplicate sequence numbers are silently ignored.

        Args:
            seq:   Packet sequence number.
            frame: Encoded audio bytes for this packet.
        """
        if not self._initialised:
            # Bootstrap: anchor the expected sequence to the first arrival
            self._next_seq = seq
            self._initialised = True
        elif not self._popped and seq < self._next_seq:
            # Before any packet has been delivered, lower the anchor if an
            # earlier sequence number arrives (handles out-of-order first window).
            self._next_seq = seq

        # Drop packets that are too far in the past
        if seq < self._next_seq - 2 * self._max_size:
            return

        # Ignore duplicates
        if seq in self._buffer:
            return

        self._buffer[seq] = frame

        # If buffer is over capacity, advance head to the earliest packet
        if len(self._buffer) > self._max_size:
            self._advance_to_earliest()

    def pop(self) -> bytes | None:
        """Return the next expected packet in sequence order.

        If the expected packet has not yet arrived, returns None.  If the
        buffer is full (more than _max_size packets accumulated), the head
        advances to the earliest available packet to break the stall.

        Returns:
            Encoded audio bytes, or None if the next packet is not yet available.
        """
        if not self._initialised:
            return None

        # Force-advance if buffer is completely full and next expected is missing
        if len(self._buffer) >= self._max_size and self._next_seq not in self._buffer:
            self._advance_to_earliest()

        if self._next_seq in self._buffer:
            frame = self._buffer.pop(self._next_seq)
            self._next_seq += 1
            self._popped = True
            return frame

        return None

    def reset(self) -> None:
        """Clear all buffered packets and reset the sequence counter."""
        self._buffer.clear()
        self._next_seq = 0
        self._initialised = False
        self._popped = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        """Current number of packets held in the buffer."""
        return len(self._buffer)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _advance_to_earliest(self) -> None:
        """Skip the head forward to the smallest sequence number present."""
        if not self._buffer:
            return
        earliest = min(self._buffer.keys())
        if earliest > self._next_seq:
            self._next_seq = earliest
