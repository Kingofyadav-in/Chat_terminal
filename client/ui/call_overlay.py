"""
Curses overlay for an incoming call notification.

The overlay is drawn directly on stdscr (no subwindow) so it floats on top
of whatever content is already rendered.  HIGHLIGHT (yellow on black) is
used for the box border and labels; the caller name is shown in bold white.

Usage::

    from client.ui import call_overlay

    call_overlay.render(stdscr, "alice")
    key = stdscr.getch()
    if key in (ord('a'), ord('A')):
        ...  # accept
    elif key in (ord('d'), ord('D')):
        ...  # decline
    call_overlay.clear(stdscr)
"""

import curses

_BOX_ROWS = 12
_BOX_COLS = 40


def _box_origin(stdscr) -> tuple[int, int]:
    """Return the (top-row, left-col) that centres the overlay on stdscr."""
    sh, sw = stdscr.getmaxyx()
    row = max(0, (sh - _BOX_ROWS) // 2)
    col = max(0, (sw - _BOX_COLS) // 2)
    return row, col


def render(stdscr, caller: str) -> None:
    """Draw the incoming-call overlay on top of the current screen.

    The overlay is a 12×40 box centred on ``stdscr``.  It shows:
      - A top border and title bar ("Incoming Call")
      - The caller's username
      - Accept / Decline key hints

    Colors: HIGHLIGHT pair (yellow on black) for the chrome; bold white for
    the caller name line.

    Args:
        stdscr: The root curses window (must already be initialised).
        caller: Username of the person calling.
    """
    # Lazy import to avoid circular dependency issues at module load time
    try:
        from client.ui.colors import HIGHLIGHT
        yellow_attr  = curses.color_pair(HIGHLIGHT)
    except Exception:
        yellow_attr  = curses.A_REVERSE

    bold_white = curses.A_BOLD

    row0, col0 = _box_origin(stdscr)
    sh, sw     = stdscr.getmaxyx()

    def _safe_addstr(row: int, col: int, text: str, attr: int = 0) -> None:
        """Write text, clipping silently at window edges."""
        if row < 0 or row >= sh:
            return
        # Clip text to available columns
        avail = sw - col
        if avail <= 0:
            return
        if len(text) > avail:
            text = text[:avail]
        try:
            stdscr.addstr(row, col, text, attr)
        except curses.error:
            pass

    # ── Top border ────────────────────────────────────────────────────────
    top_border = "+" + "-" * (_BOX_COLS - 2) + "+"
    _safe_addstr(row0, col0, top_border, yellow_attr)

    # ── Title row ─────────────────────────────────────────────────────────
    title      = "  Incoming Call  "
    title_line = "|" + title.center(_BOX_COLS - 2) + "|"
    _safe_addstr(row0 + 1, col0, title_line, yellow_attr | curses.A_BOLD)

    # ── Separator ─────────────────────────────────────────────────────────
    sep_line = "|" + "-" * (_BOX_COLS - 2) + "|"
    _safe_addstr(row0 + 2, col0, sep_line, yellow_attr)

    # ── Empty padding rows ────────────────────────────────────────────────
    empty_line = "|" + " " * (_BOX_COLS - 2) + "|"
    for r in (row0 + 3, row0 + 4, row0 + 6, row0 + 7, row0 + 9):
        _safe_addstr(r, col0, empty_line, yellow_attr)

    # ── Caller name ───────────────────────────────────────────────────────
    # Truncate caller name so it always fits inside the box
    max_name = _BOX_COLS - 4
    display_name = caller if len(caller) <= max_name else caller[: max_name - 1] + "…"
    caller_line  = "|" + display_name.center(_BOX_COLS - 2) + "|"
    _safe_addstr(row0 + 5, col0, caller_line, yellow_attr | bold_white)

    # ── Separator ─────────────────────────────────────────────────────────
    _safe_addstr(row0 + 8, col0, sep_line, yellow_attr)

    # ── Key hint row ──────────────────────────────────────────────────────
    hints      = "[A] Accept  [D] Decline"
    hints_line = "|" + hints.center(_BOX_COLS - 2) + "|"
    _safe_addstr(row0 + 10, col0, hints_line, yellow_attr)

    # ── Bottom border ─────────────────────────────────────────────────────
    bot_border = "+" + "-" * (_BOX_COLS - 2) + "+"
    _safe_addstr(row0 + 11, col0, bot_border, yellow_attr)

    try:
        stdscr.refresh()
    except curses.error:
        pass


def clear(stdscr) -> None:
    """Erase the overlay area by overwriting it with spaces in the default color.

    After calling this the caller should redraw the underlying screen content
    (e.g. by marking _dirty = True in the App).

    Args:
        stdscr: The root curses window.
    """
    row0, col0 = _box_origin(stdscr)
    sh, sw     = stdscr.getmaxyx()
    blank      = " " * _BOX_COLS

    for r in range(_BOX_ROWS):
        row = row0 + r
        if row < 0 or row >= sh:
            continue
        avail = sw - col0
        if avail <= 0:
            continue
        chunk = blank[:avail]
        try:
            stdscr.addstr(row, col0, chunk, curses.A_NORMAL)
        except curses.error:
            pass

    try:
        stdscr.refresh()
    except curses.error:
        pass
