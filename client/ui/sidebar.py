import curses
from client.ui.colors import SIDEBAR_H, ONLINE, OFFLINE, HIGHLIGHT, UNREAD
from shared.utils import trunc

_TITLE = "CHATS"
_SEP   = "─" * 18


def render(
    win,
    contacts: list[dict],
    current_chat: str | None,
    unread_map: dict[str, int],
) -> None:
    win.erase()
    h, w = win.getmaxyx()
    row  = 0

    def addrow(text: str, attr: int = 0) -> None:
        nonlocal row
        if row >= h:
            return
        try:
            win.addnstr(row, 0, text.ljust(w), w, attr)
        except curses.error:
            pass
        row += 1

    addrow(f" {_TITLE}", curses.color_pair(SIDEBAR_H) | curses.A_BOLD)
    addrow(f" {_SEP}",   curses.color_pair(SIDEBAR_H))

    for c in contacts:
        uname  = c["username"]
        is_room = c.get("type") == "room"
        online = c.get("status", "offline") == "online"
        dot    = "#" if is_room else ("●" if online else "○")
        clr    = curses.color_pair(ONLINE) if online else curses.color_pair(OFFLINE)
        badge  = unread_map.get(uname, 0)
        label  = trunc(c.get("label", uname), w - 5)

        if uname == current_chat:
            attr = curses.color_pair(HIGHLIGHT) | curses.A_BOLD
            line = f" {dot} {label}"
        else:
            attr = clr
            line = f" {dot} {label}"

        if badge:
            bstr = f"[{badge}]"
            line = trunc(line, w - len(bstr) - 1) + bstr

        addrow(line, attr)

    if not contacts:
        addrow(" (no contacts)", curses.A_DIM)

    # fill remaining
    while row < h:
        addrow("")
