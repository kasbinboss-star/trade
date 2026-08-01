"""
Non-blocking keyboard input for scrolling the live dashboards.

rich.live.Live(screen=True) draws into the terminal's alternate screen
buffer, which has no native scrollback -- so when a table is taller than
the window, the extra rows are simply clipped no matter how big you make
the window. To see all 50 stocks we poll the keyboard each tick and let
the user scroll the table window manually.

W/S (or W/D) scroll one row up/down. Page Up/Down, Home/End, and the arrow
keys also work where the terminal supports them.
"""

import os
import sys

try:
    import msvcrt
    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False

if not _HAS_MSVCRT:
    import select
    import termios
    import tty

_ARROW_PREFIX = (b"\x00", b"\xe0")
_WIN_KEY_MAP = {
    b"H": "UP",
    b"P": "DOWN",
    b"I": "PGUP",
    b"Q": "PGDN",
    b"G": "HOME",
    b"O": "END",
}

# Final byte of the xterm "ESC [ ... " sequence for each nav key.
_POSIX_ESCAPE_MAP = {
    "A": "UP",
    "B": "DOWN",
    "5": "PGUP",   # ESC [ 5 ~
    "6": "PGDN",   # ESC [ 6 ~
    "H": "HOME",
    "F": "END",
}

_posix_fd = None
_posix_old_settings = None


def enable_raw_mode() -> None:
    """Put stdin into cbreak mode so keys are readable one at a time (POSIX only)."""
    global _posix_fd, _posix_old_settings
    if _HAS_MSVCRT or not sys.stdin.isatty():
        return
    _posix_fd = sys.stdin.fileno()
    _posix_old_settings = termios.tcgetattr(_posix_fd)
    tty.setcbreak(_posix_fd)


def disable_raw_mode() -> None:
    """Restore the terminal to whatever mode it was in before enable_raw_mode() (POSIX only)."""
    if _posix_fd is not None and _posix_old_settings is not None:
        termios.tcsetattr(_posix_fd, termios.TCSADRAIN, _posix_old_settings)


def _read_posix_key():
    """Read one key from stdin, non-blocking. Returns a nav-key string, a single char, or None.

    Reads straight from the raw file descriptor via os.read() rather than
    sys.stdin.read(). Mixing select() (which polls the raw fd) with
    sys.stdin.read() (a buffered TextIOWrapper) is broken: the first read()
    call opportunistically slurps the whole escape sequence out of the fd
    into Python's internal buffer, so the *next* select() call sees an empty
    fd and bails out -- silently dropping arrow keys / PgUp / PgDn / Home /
    End, even though the bytes were sitting right there in the buffer.
    """
    fd = sys.stdin.fileno()
    if not select.select([fd], [], [], 0)[0]:
        return None
    chunk = os.read(fd, 8)
    if not chunk:
        return None

    if chunk[0:1] == b"\x1b":  # possible arrow / nav sequence: ESC [ <code> [~]
        if len(chunk) >= 3 and chunk[1:2] == b"[":
            return _POSIX_ESCAPE_MAP.get(chr(chunk[2]))
        return None  # lone/incomplete escape sequence

    return chunk[:1].decode(errors="replace")


def poll_key():
    """Return one of UP / DOWN / PGUP / PGDN / HOME / END / QUIT, or None.

    Non-blocking: returns immediately if no key is waiting.
    """
    if _HAS_MSVCRT:
        if not msvcrt.kbhit():
            return None
        ch = msvcrt.getch()
        if ch == b"\x03" or ch in (b"q", b"Q"):
            return "QUIT"
        if ch in (b"w", b"W"):
            return "UP"
        if ch in (b"s", b"S", b"d", b"D"):
            return "DOWN"
        if ch in _ARROW_PREFIX:
            return _WIN_KEY_MAP.get(msvcrt.getch())
        return None

    if not sys.stdin.isatty():
        return None

    key = _read_posix_key()
    if key is None:
        return None
    if key in ("UP", "DOWN", "PGUP", "PGDN", "HOME", "END"):
        return key
    if key in ("q", "Q", "\x03"):
        return "QUIT"
    if key in ("w", "W"):
        return "UP"
    if key in ("s", "S", "d", "D"):
        return "DOWN"
    return None


class ScrollState:
    """Tracks the current scroll offset (in rows) for a stock table."""

    def __init__(self):
        self.offset = 0

    def handle(self, key, total_rows: int, visible_rows: int) -> None:
        if key is None:
            return
        if key == "UP":
            self.offset -= 1
        elif key == "DOWN":
            self.offset += 1
        elif key == "PGUP":
            self.offset -= visible_rows
        elif key == "PGDN":
            self.offset += visible_rows
        elif key == "HOME":
            self.offset = 0
        elif key == "END":
            self.offset = max(0, total_rows - visible_rows)
        self.clamp(total_rows, visible_rows)

    def clamp(self, total_rows: int, visible_rows: int) -> None:
        max_offset = max(0, total_rows - visible_rows)
        self.offset = max(0, min(self.offset, max_offset))
