import curses

# Color pair IDs
HEADER     = 1
SIDEBAR_H  = 2
ONLINE     = 3
OFFLINE    = 4
MY_MSG     = 5
THEIR_MSG  = 6
TIMESTAMP  = 7
INPUT_BAR  = 8
DIVIDER    = 9
UNREAD     = 10
ERROR_CLR  = 11
HIGHLIGHT  = 12


def init_colors() -> None:
    curses.start_color()
    curses.use_default_colors()

    curses.init_pair(HEADER,    curses.COLOR_BLACK,  curses.COLOR_CYAN)
    curses.init_pair(SIDEBAR_H, curses.COLOR_CYAN,   -1)
    curses.init_pair(ONLINE,    curses.COLOR_GREEN,  -1)
    curses.init_pair(OFFLINE,   curses.COLOR_WHITE,  -1)
    curses.init_pair(MY_MSG,    curses.COLOR_CYAN,   -1)
    curses.init_pair(THEIR_MSG, curses.COLOR_WHITE,  -1)
    curses.init_pair(TIMESTAMP, curses.COLOR_BLACK,  -1)   # will be dim
    curses.init_pair(INPUT_BAR, curses.COLOR_WHITE,  -1)
    curses.init_pair(DIVIDER,   curses.COLOR_CYAN,   -1)
    curses.init_pair(UNREAD,    curses.COLOR_BLACK,  curses.COLOR_RED)
    curses.init_pair(ERROR_CLR, curses.COLOR_RED,    -1)
    curses.init_pair(HIGHLIGHT, curses.COLOR_BLACK,  curses.COLOR_YELLOW)
