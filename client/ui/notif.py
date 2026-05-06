import curses


def bell() -> None:
    """Emit the terminal bell if supported."""
    try:
        curses.beep()
    except curses.error:
        pass


def flash() -> None:
    """Flash the terminal screen if supported."""
    try:
        curses.flash()
    except curses.error:
        pass
