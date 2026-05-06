import curses
from client.ui.colors import init_colors

SIDEBAR_W  = 22   # fixed sidebar width
HEADER_H   = 1
INPUT_H    = 1


class Screen:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.h = self.w = 0
        self.header_win = self.sidebar_win = None
        self.div_win    = self.chat_win    = None
        self.input_win  = None
        self._setup()

    def _setup(self) -> None:
        self.stdscr.keypad(True)
        curses.noecho()
        curses.cbreak()
        curses.curs_set(1)
        self.stdscr.nodelay(True)
        init_colors()
        self._rebuild()

    def _rebuild(self) -> None:
        self.h, self.w = self.stdscr.getmaxyx()
        mid_h   = max(self.h - HEADER_H - INPUT_H, 1)
        chat_w  = max(self.w - SIDEBAR_W - 1, 1)

        self.header_win  = curses.newwin(HEADER_H, self.w,          0,             0)
        self.sidebar_win = curses.newwin(mid_h,    SIDEBAR_W,       HEADER_H,      0)
        self.div_win     = curses.newwin(mid_h,    1,               HEADER_H,      SIDEBAR_W)
        self.chat_win    = curses.newwin(mid_h,    chat_w,          HEADER_H,      SIDEBAR_W + 1)
        self.input_win   = curses.newwin(INPUT_H,  self.w,          self.h - INPUT_H, 0)

    def resize(self) -> None:
        self.stdscr.clear()
        self._rebuild()

    def chat_size(self) -> tuple[int, int]:
        h, w = self.chat_win.getmaxyx()
        return h, w

    def noutrefresh_all(self) -> None:
        for win in (
            self.header_win,
            self.sidebar_win,
            self.div_win,
            self.chat_win,
            self.input_win,
        ):
            try:
                win.noutrefresh()
            except curses.error:
                pass

    def doupdate(self) -> None:
        curses.doupdate()
