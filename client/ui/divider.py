import curses
from client.ui.colors import DIVIDER


def render(win) -> None:
    h, w = win.getmaxyx()
    attr = curses.color_pair(DIVIDER)
    for row in range(h):
        try:
            win.addch(row, 0, curses.ACS_VLINE, attr)
        except curses.error:
            pass
