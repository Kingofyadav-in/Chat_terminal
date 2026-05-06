import curses
import textwrap
from client.ui.colors import MY_MSG, THEIR_MSG, TIMESTAMP
from shared.utils import fmt_time, trunc


def render(win, messages: list[dict], self_user: str, peer: str | None) -> None:
    win.erase()
    h, w = win.getmaxyx()

    if not peer:
        _draw_empty(win, h, w)
        return

    if not messages:
        _draw_empty_chat(win, h, w, peer)
        return

    # Build list of rendered lines bottom-up
    rendered: list[tuple[str, int]] = []   # (text, attr)

    for msg in messages:
        if msg.get("deleted"):
            continue

        sender    = msg["from_user"]
        text      = msg.get("plaintext", "")
        ts        = fmt_time(msg["timestamp"])
        is_me     = sender == self_user
        max_w     = max(w - 4, 10)
        short_id  = str(msg.get("id", ""))[:8]
        edited    = " edited" if msg.get("edited") else ""
        receipt   = ""
        if is_me:
            if msg.get("read_status"):
                receipt = " read"
            elif msg.get("delivered"):
                receipt = " sent"

        if is_me:
            header_attr = curses.color_pair(MY_MSG)   | curses.A_BOLD
            body_attr   = curses.color_pair(MY_MSG)
            ts_attr     = curses.color_pair(TIMESTAMP) | curses.A_DIM
            header      = f"  You  {ts}  {short_id}{receipt}{edited}"
        else:
            header_attr = curses.color_pair(THEIR_MSG) | curses.A_BOLD
            body_attr   = curses.color_pair(THEIR_MSG)
            ts_attr     = curses.color_pair(TIMESTAMP) | curses.A_DIM
            header      = f"  {trunc(sender, 16)}  {ts}  {short_id}{edited}"

        rendered.append(("", 0))   # blank spacer above each message
        rendered.append((header, ts_attr))

        wrapped = textwrap.wrap(text, max_w) or [""]
        for line in wrapped:
            if is_me:
                padded = line.rjust(w - 2)
                rendered.append((" " + padded, body_attr))
            else:
                rendered.append(("  " + line, body_attr))

    # Show the last `h` lines
    visible = rendered[-h:] if len(rendered) > h else rendered
    # pad top if fewer lines than window height
    start_row = h - len(visible)

    for i, (text, attr) in enumerate(visible):
        row = start_row + i
        if row < 0 or row >= h:
            continue
        try:
            win.addnstr(row, 0, text.ljust(w), w, attr)
        except curses.error:
            pass


def _draw_empty(win, h: int, w: int) -> None:
    msg = "Select a contact with /dm @username"
    row = h // 2
    col = max((w - len(msg)) // 2, 0)
    try:
        win.addstr(row, col, msg, curses.A_DIM)
    except curses.error:
        pass


def _draw_empty_chat(win, h: int, w: int, peer: str) -> None:
    lines = [
        f"Open chat with {trunc(peer, max(w - 18, 10))}",
        "No messages yet.",
    ]
    start = max(h // 2 - 1, 0)
    for i, msg in enumerate(lines):
        row = start + i
        if row >= h:
            break
        col = max((w - len(msg)) // 2, 0)
        try:
            win.addstr(row, col, msg, curses.A_DIM)
        except curses.error:
            pass
