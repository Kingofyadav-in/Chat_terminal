import argparse
import asyncio
import curses
import logging
import os
import sys

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _run(stdscr):
    from client.app import App

    app = App()
    asyncio.run(app.run(stdscr))


def main():
    parser = argparse.ArgumentParser(prog="ct-client")
    parser.add_argument(
        "--db",
        help="override the local SQLite path for this client instance",
    )
    parser.add_argument(
        "--profile",
        help="use a named local profile under ~/.chatterminal/profiles/",
    )
    parser.add_argument(
        "--reset-local",
        action="store_true",
        help="clear local SQLite state before starting",
    )
    args = parser.parse_args()

    if args.db:
        os.environ["CT_CLIENT_DB"] = args.db
    if args.profile:
        os.environ["CT_CLIENT_PROFILE"] = args.profile

    from client.db.schema import clear_local_data

    if args.reset_local:
        asyncio.run(clear_local_data())
    try:
        curses.wrapper(_run)
    except KeyboardInterrupt:
        pass
    sys.stdout.write("\033[?25h")   # restore cursor if curses hid it
    sys.stdout.flush()


if __name__ == "__main__":
    main()
