"""
Safety number verification overlay.

Displays the 20-digit safety number shared between the local user and a
contact so they can verify each other's identity out-of-band (voice call,
in-person, etc.).  The number is formatted as 5 groups of 4 digits.

Usage::

    from client.ui import verify_screen

    verified = await verify_screen.show(stdscr, "alice", "1234 5678 9012 3456 7890")
    if verified:
        # mark contact as verified in DB
        ...

Key bindings inside the overlay:
    C / c  — mark the contact as verified and return True
    ESC    — cancel without marking verified, return False
"""

import asyncio
import curses
from typing import Optional

_ESC        = 27
_BOX_ROWS   = 18
_BOX_COLS   = 52
_TICK       = 0.05  # seconds between getch polls


def _box_origin(stdscr) -> tuple[int, int]:
    """Return (top-row, left-col) that centres the box on stdscr."""
    sh, sw = stdscr.getmaxyx()
    row = max(0, (sh - _BOX_ROWS) // 2)
    col = max(0, (sw - _BOX_COLS) // 2)
    return row, col


def _draw(stdscr, username: str, safety_num: str) -> None:
    """Render the verification box onto stdscr.

    Args:
        stdscr:     Root curses window.
        username:   Contact username being verified.
        safety_num: 20-digit safety number in "XXXX XXXX XXXX XXXX XXXX" format.
    """
    try:
        from client.ui.colors import HIGHLIGHT, MY_MSG
        title_attr  = curses.color_pair(HIGHLIGHT) | curses.A_BOLD
        number_attr = curses.color_pair(MY_MSG) | curses.A_BOLD
        border_attr = curses.color_pair(HIGHLIGHT)
    except Exception:
        title_attr  = curses.A_REVERSE | curses.A_BOLD
        number_attr = curses.A_BOLD
        border_attr = curses.A_REVERSE

    normal_attr = curses.A_NORMAL
    dim_attr    = curses.A_DIM

    row0, col0 = _box_origin(stdscr)
    sh, sw     = stdscr.getmaxyx()

    def _put(r: int, c: int, text: str, attr: int = curses.A_NORMAL) -> None:
        """Clipping-safe addstr."""
        if r < 0 or r >= sh or c < 0:
            return
        avail = sw - c
        if avail <= 0:
            return
        if len(text) > avail:
            text = text[:avail]
        try:
            stdscr.addstr(r, c, text, attr)
        except curses.error:
            pass

    def _line(r: int, content: str, attr: int, fill: str = " ") -> None:
        """Draw a full-width box row: | <content centred> |"""
        inner = content.center(_BOX_COLS - 2, fill[0])
        _put(row0 + r, col0, "|" + inner + "|", attr)

    # ── Box chrome ────────────────────────────────────────────────────────
    top = "+" + "-" * (_BOX_COLS - 2) + "+"
    bot = "+" + "-" * (_BOX_COLS - 2) + "+"
    sep = "|" + "-" * (_BOX_COLS - 2) + "|"

    _put(row0,              col0, top, border_attr)
    _put(row0 + _BOX_ROWS - 1, col0, bot, border_attr)

    # Title
    _line(1, "  End-to-End Encryption Verified  ", title_attr)
    _put(row0 + 2, col0, sep, border_attr)

    # Subtitle
    _line(3, "", normal_attr)
    _line(4, f"Safety number with {username}", normal_attr | curses.A_BOLD)
    _line(5, "", normal_attr)

    _put(row0 + 6, col0, sep, border_attr)

    # The safety number — display in large readable groups
    _line(7, "", normal_attr)
    _line(8, safety_num, number_attr)
    _line(9, "", normal_attr)

    _put(row0 + 10, col0, sep, border_attr)

    # Instructions — word-wrapped to fit in BOX_COLS - 4
    instructions = [
        "Compare this number with your contact",
        "over a separate channel. If they match,",
        "your conversation is secure.",
    ]
    for i, line in enumerate(instructions):
        _line(11 + i, line, dim_attr)

    _line(14, "", normal_attr)
    _put(row0 + 15, col0, sep, border_attr)
    _line(16, "[C] Mark Verified   [ESC] Cancel", border_attr | curses.A_BOLD)

    try:
        stdscr.refresh()
    except curses.error:
        pass


async def show(stdscr, username: str, safety_num: str) -> bool:
    """Display the safety number verification overlay and wait for user input.

    The function runs an async polling loop with a 50 ms tick so the rest of
    the event loop remains responsive.  The overlay is drawn on ``stdscr``
    using whatever color pairs are already initialised.

    Args:
        stdscr:     Root curses window (must have keypad enabled and nodelay
                    set; the function sets nodelay itself to be safe).
        username:   The contact's username being verified.
        safety_num: 20-digit safety number from
                    ``client.crypto.fingerprint.safety_number()``.

    Returns:
        True  if the user pressed C (confirmed match).
        False if the user pressed ESC (cancelled).
    """
    # Ensure non-blocking input
    stdscr.nodelay(True)
    stdscr.keypad(True)

    _draw(stdscr, username, safety_num)

    while True:
        await asyncio.sleep(_TICK)
        try:
            key = stdscr.getch()
        except curses.error:
            key = -1

        if key == -1:
            continue

        if key in (ord("c"), ord("C")):
            return True

        if key == _ESC:
            return False

        # Ignore all other keys — keep showing the overlay
        # Redraw in case something else triggered a screen update
        _draw(stdscr, username, safety_num)
