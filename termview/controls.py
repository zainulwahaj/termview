"""
Non-blocking keystroke reading for interactive video playback.

Reads stdin one byte at a time and decodes:
  - printable ASCII (space, q, m, f, comma, period, plus, minus, etc.)
  - escape sequences for arrow keys + shift-arrows
  - bare ESC (also returned for "quit")

Designed to be called once per frame with the frame-budget as timeout, so it
gives keystrokes near-immediate response while never blocking the render loop.
Assumes stdin is already in cbreak mode (see TerminalState).
"""

import select
import sys
from enum import Enum


class Key(Enum):
    NONE = "none"
    QUIT = "quit"
    PAUSE = "pause"            # space
    SEEK_BACK = "seek_back"    # left arrow
    SEEK_FWD = "seek_fwd"      # right arrow
    SEEK_BACK_BIG = "seek_back_big"   # shift+left or down
    SEEK_FWD_BIG = "seek_fwd_big"     # shift+right or up
    FRAME_PREV = "frame_prev"  # ,
    FRAME_NEXT = "frame_next"  # .
    MUTE = "mute"              # m
    VOL_UP = "vol_up"          # + or =
    VOL_DOWN = "vol_down"      # -
    RESTART = "restart"        # 0 or home


def read_key(timeout: float) -> Key:
    """
    Read at most one key event from stdin, waiting up to *timeout* seconds.
    Returns Key.NONE if nothing arrived in that window.

    Caller must ensure stdin is in cbreak (or raw) mode and is a TTY.
    """
    if not sys.stdin.isatty():
        return Key.NONE

    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return Key.NONE

    ch = sys.stdin.read(1)
    if not ch:
        return Key.NONE

    # Single-character bindings.
    simple = {
        " ": Key.PAUSE,
        "q": Key.QUIT,
        "Q": Key.QUIT,
        ",": Key.FRAME_PREV,
        "<": Key.FRAME_PREV,
        ".": Key.FRAME_NEXT,
        ">": Key.FRAME_NEXT,
        "m": Key.MUTE,
        "M": Key.MUTE,
        "+": Key.VOL_UP,
        "=": Key.VOL_UP,
        "-": Key.VOL_DOWN,
        "_": Key.VOL_DOWN,
        "0": Key.RESTART,
    }
    if ch in simple:
        return simple[ch]

    # ESC starts either a bare escape (= quit) or a CSI sequence.
    if ch != "\033":
        return Key.NONE

    # Peek for a follow-up byte; if none arrives in 50ms it was a bare ESC.
    ready, _, _ = select.select([sys.stdin], [], [], 0.05)
    if not ready:
        return Key.QUIT

    if sys.stdin.read(1) != "[":
        return Key.NONE  # unrecognized ESC-anything

    # CSI sequence.  Read the final byte (and the optional modifier digits).
    seq = ""
    while True:
        ready, _, _ = select.select([sys.stdin], [], [], 0.05)
        if not ready:
            break
        b = sys.stdin.read(1)
        seq += b
        # Final bytes of a CSI sequence are in the 0x40-0x7E range.
        if "@" <= b <= "~":
            break

    # Common arrow keys
    if seq == "A":
        return Key.SEEK_FWD_BIG     # up: +30s
    if seq == "B":
        return Key.SEEK_BACK_BIG    # down: -30s
    if seq == "C":
        return Key.SEEK_FWD         # right: +5s
    if seq == "D":
        return Key.SEEK_BACK        # left: -5s
    # Shift-arrows arrive as CSI 1;2A / 1;2B / 1;2C / 1;2D in xterm-style.
    if seq.endswith("C") and "2" in seq:
        return Key.SEEK_FWD_BIG
    if seq.endswith("D") and "2" in seq:
        return Key.SEEK_BACK_BIG
    if seq == "H":
        return Key.RESTART          # Home

    return Key.NONE
