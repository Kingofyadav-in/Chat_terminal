import curses
from client.ui.colors import HEADER, ONLINE, ERROR_CLR
from shared.utils import trunc

VERSION = "v1.0"


def render(win, username: str, status: str, current_chat: str | None, unread: int) -> None:
    win.erase()
    h, w = win.getmaxyx()

    title = f" ChatterTerminal {VERSION}"
    if current_chat:
        title += f"  ›  {current_chat}"

    dot   = "●" if status == "online" else "○"
    badge = f" [{unread} unread]" if unread else ""
    right = f" {dot} {status}{badge} "

    title = trunc(title, w - len(right) - 1)
    pad   = w - len(title) - len(right)

    try:
        win.attron(curses.color_pair(HEADER) | curses.A_BOLD)
        win.addstr(0, 0, title + " " * max(pad, 0) + right)
        win.attroff(curses.color_pair(HEADER) | curses.A_BOLD)
    except curses.error:
        pass
