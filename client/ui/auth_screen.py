"""
Auth screen rendered inside the curses context.
Uses non-blocking getch() polled via asyncio so the event loop stays alive.
"""
import asyncio
import curses
from client.ui.colors import HEADER, MY_MSG, ERROR_CLR, THEIR_MSG

_BANNER = [
    "  ██████╗██╗  ██╗ █████╗ ████████╗████████╗███████╗██████╗ ",
    " ██╔════╝██║  ██║██╔══██╗╚══██╔══╝╚══██╔══╝██╔════╝██╔══██╗",
    " ██║     ███████║███████║   ██║      ██║   █████╗  ██████╔╝",
    " ██║     ██╔══██║██╔══██║   ██║      ██║   ██╔══╝  ██╔══██╗",
    " ╚██████╗██║  ██║██║  ██║   ██║      ██║   ███████╗██║  ██║",
    "  ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝  ╚═╝      ╚═╝   ╚══════╝╚═╝  ╚═╝",
    "  ████████╗███████╗██████╗ ███╗   ███╗██╗███╗   ██╗ █████╗ ██╗",
    "  ╚══██╔══╝██╔════╝██╔══██╗████╗ ████║██║████╗  ██║██╔══██╗██║",
    "     ██║   █████╗  ██████╔╝██╔████╔██║██║██╔██╗ ██║███████║██║",
    "     ██║   ██╔══╝  ██╔══██╗██║╚██╔╝██║██║██║╚██╗██║██╔══██║██║",
    "     ██║   ███████╗██║  ██║██║ ╚═╝ ██║██║██║ ╚████║██║  ██║███████╗",
    "     ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝╚══════╝",
]

_TAGLINE = "  Self-hosted · E2E Encrypted · Terminal Chat"


async def _get_line(stdscr, row: int, col: int, label: str, secret: bool = False) -> str:
    """Non-blocking char-by-char input inside asyncio event loop."""
    buf = ""
    stdscr.move(row, col + len(label))
    stdscr.refresh()

    while True:
        key = stdscr.getch()
        if key == -1:
            await asyncio.sleep(0.03)
            continue
        if key in (curses.KEY_ENTER, 10, 13):
            return buf
        if key in (curses.KEY_BACKSPACE, 127, 8):
            if buf:
                buf = buf[:-1]
                stdscr.move(row, col + len(label))
                stdscr.addstr(" " * 40)
                stdscr.move(row, col + len(label))
                display = "*" * len(buf) if secret else buf
                stdscr.addstr(display)
                stdscr.move(row, col + len(label) + len(buf))
                stdscr.refresh()
        elif 32 <= key <= 126:
            buf += chr(key)
            stdscr.move(row, col + len(label) + len(buf) - 1)
            ch = "*" if secret else chr(key)
            try:
                stdscr.addch(ch)
            except curses.error:
                pass
            stdscr.refresh()
        await asyncio.sleep(0.01)


async def show(stdscr) -> dict | None:
    """
    Returns {"mode": "login"|"register", "username": str, "password": str}
    or None if the user quit.
    """
    curses.curs_set(1)
    curses.noecho()
    stdscr.nodelay(True)
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    # Banner
    banner_start = max((h - len(_BANNER) - 10) // 2, 0)
    for i, line in enumerate(_BANNER):
        row = banner_start + i
        col = max((w - len(line)) // 2, 0)
        if row < h:
            try:
                stdscr.addstr(row, col, line, curses.color_pair(MY_MSG) | curses.A_BOLD)
            except curses.error:
                pass

    tag_row = banner_start + len(_BANNER)
    if tag_row < h:
        try:
            stdscr.addstr(
                tag_row,
                max((w - len(_TAGLINE)) // 2, 0),
                _TAGLINE,
                curses.A_DIM,
            )
        except curses.error:
            pass

    # Mode selection
    sel_row = tag_row + 2
    sel_msg = "  [L] Login    [R] Register    [Q] Quit  "
    if sel_row < h:
        try:
            stdscr.addstr(
                sel_row,
                max((w - len(sel_msg)) // 2, 0),
                sel_msg,
                curses.color_pair(HEADER) | curses.A_BOLD,
            )
        except curses.error:
            pass

    stdscr.refresh()

    # Wait for L / R / Q
    mode = None
    while mode is None:
        key = stdscr.getch()
        if key == -1:
            await asyncio.sleep(0.03)
            continue
        if key in (ord("l"), ord("L")):
            mode = "login"
        elif key in (ord("r"), ord("R")):
            mode = "register"
        elif key in (ord("q"), ord("Q"), 3, 27):
            return None

    # Form
    form_row = sel_row + 2
    col      = max((w - 40) // 2, 2)

    def label_row(r: int, text: str) -> None:
        if r < h:
            try:
                stdscr.addstr(r, col, text, curses.color_pair(THEIR_MSG))
            except curses.error:
                pass

    title = "Register" if mode == "register" else "Login"
    label_row(form_row,     f"  ── {title} ──────────────────────────")
    label_row(form_row + 2, "  Username : ")
    label_row(form_row + 4, "  Password : ")
    stdscr.refresh()

    error_row = form_row + 6

    username = await _get_line(stdscr, form_row + 2, col, "  Username : ")
    username = username.strip()

    password = await _get_line(stdscr, form_row + 4, col, "  Password : ", secret=True)

    if not username or not password:
        if error_row < h:
            try:
                stdscr.addstr(
                    error_row, col,
                    "  Username and password cannot be empty.",
                    curses.color_pair(ERROR_CLR),
                )
                stdscr.refresh()
            except curses.error:
                pass
        await asyncio.sleep(1.5)
        return await show(stdscr)   # retry

    return {"mode": mode, "username": username, "password": password}


async def unlock(stdscr, username: str) -> str | None:
    """Prompt for the local password to unlock the stored private key.

    Returns:
        The password string if the user typed one and pressed Enter.
        None if the user pressed Enter on an empty field (skip encryption)
        or pressed ESC / Q / Ctrl-C (switch account — caller should show
        the full auth screen instead).

    The caller can distinguish "skip" from "switch" by the return value:
        - non-empty str  → unlock attempt
        - empty str ""   → user explicitly wants to skip E2E unlock
        - None           → user wants to switch / register a new account
    """
    curses.curs_set(1)
    curses.noecho()
    stdscr.nodelay(True)
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    box_w   = min(60, w - 4)
    box_col = max((w - box_w) // 2, 0)
    mid     = h // 2

    lines = [
        ("ChatterTerminal", curses.A_BOLD),
        ("", 0),
        (f"Welcome back, {username}!", curses.A_BOLD),
        ("", 0),
        ("Enter your password to unlock E2E encryption.", 0),
        ("", 0),
        ("Password: ", 0),
        ("", 0),
        ("[Enter] unlock    [S] skip encryption    [N] new account    [Q] quit", curses.A_DIM),
    ]

    for i, (text, attr) in enumerate(lines):
        row = mid - len(lines) // 2 + i
        if 0 <= row < h:
            try:
                stdscr.addstr(row, max((w - len(text)) // 2, 0), text, attr)
            except curses.error:
                pass

    stdscr.refresh()

    pw_row = mid - len(lines) // 2 + 6   # "Password: " line
    pw_col = max((w - len("Password: ") - 20) // 2, 0)

    buf = ""
    while True:
        key = stdscr.getch()
        if key == -1:
            await asyncio.sleep(0.03)
            continue

        # ESC, Q, Ctrl-C → switch account
        if key in (27, ord("q"), ord("Q"), 3):
            return None

        # S → skip (continue without decrypting)
        if key in (ord("s"), ord("S")):
            return ""

        # N → new account (same as ESC but intent is clearer)
        if key in (ord("n"), ord("N")):
            return None

        # Enter → submit
        if key in (curses.KEY_ENTER, 10, 13):
            return buf

        # Backspace
        if key in (curses.KEY_BACKSPACE, 127, 8):
            if buf:
                buf = buf[:-1]
                try:
                    stdscr.move(pw_row, pw_col + len("Password: "))
                    stdscr.addstr(" " * 40)
                    stdscr.move(pw_row, pw_col + len("Password: "))
                    stdscr.addstr("*" * len(buf))
                    stdscr.move(pw_row, pw_col + len("Password: ") + len(buf))
                    stdscr.refresh()
                except curses.error:
                    pass
            continue

        if 32 <= key <= 126:
            buf += chr(key)
            try:
                stdscr.move(pw_row, pw_col + len("Password: ") + len(buf) - 1)
                stdscr.addch("*")
                stdscr.refresh()
            except curses.error:
                pass
