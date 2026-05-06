import curses
from client.ui.colors import INPUT_BAR, ERROR_CLR

PROMPT = "> "


def render(win, buf: str, cursor: int, hint: str = "") -> None:
    win.erase()
    h, w = win.getmaxyx()

    right = f"  [Enter] send  [^C] quit" if not hint else f"  {hint}"
    right = right[: w // 3]

    avail = w - len(PROMPT) - len(right) - 1

    # scroll buf so cursor is always visible
    if avail > 0:
        start = max(0, cursor - avail + 1)
        visible = buf[start : start + avail]
        cur_col = len(PROMPT) + (cursor - start)
    else:
        visible = ""
        cur_col = len(PROMPT)

    line = PROMPT + visible
    pad  = w - len(line) - len(right)

    try:
        win.attron(curses.color_pair(INPUT_BAR))
        win.addnstr(0, 0, line + " " * max(pad, 0) + right, w)
        win.attroff(curses.color_pair(INPUT_BAR))
        # position cursor
        if 0 <= cur_col < w:
            win.move(0, cur_col)
    except curses.error:
        pass
