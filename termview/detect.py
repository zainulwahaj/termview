"""
Terminal capability detection.

Decides at runtime which graphics protocol the host terminal supports and
which color depth to use.  All decisions are conservative: when in doubt,
fall back to the path that "looks worse but works."

Detection categories
--------------------
RendererType   how images are drawn:
               kitty   (best, pixel-perfect, supported by Kitty/WezTerm/Ghostty)
               iterm2  (pixel-perfect, supported by iTerm2/Warp)
               sixel   (pixel-perfect, older protocol, xterm/foot/WT)
               block   (universal fallback, ANSI background fills)

ColorDepth     which ANSI escape format the block renderer should use:
               truecolor    \\033[48;2;R;G;Bm  (24-bit, modern terminals)
               palette_256  \\033[48;5;Nm       (xterm 256, with dithering)

Special environments
--------------------
$TMUX        tmux strips most graphics escapes by default.  We could wrap
             in tmux passthrough (\\033Ptmux;...\\033\\) but that requires
             tmux 3.4+ with `set -g allow-passthrough on`.  Safer default:
             force the block renderer when running under tmux unless the
             user explicitly opts in.
$SSH_TTY     iterm2 inline images don't survive SSH cleanly.  Kitty's
             protocol does survive if both ends speak it, but we have no
             way to verify the local side.  Conservative default: prefer
             block over inline-image protocols under SSH.
$WT_SESSION  Windows Terminal (cmd.exe doesn't set TERM_PROGRAM).  WT
             supports sixel as of late 2023.
"""

import os
import select
import shutil
import sys
import termios
import tty
from enum import Enum


class RendererType(Enum):
    KITTY = "kitty"
    ITERM2 = "iterm2"
    SIXEL = "sixel"
    BLOCK = "block"


class ColorDepth(Enum):
    TRUECOLOR = "truecolor"   # 24-bit RGB:  \033[48;2;R;G;Bm
    PALETTE_256 = "256"       # xterm 256:   \033[48;5;Nm  (with dithering)


_RENDERER_NAMES = {r.value: r for r in RendererType}


# ---------------------------------------------------------------------- public

def detect_color_depth(prefer: str | None = None) -> ColorDepth:
    """
    Choose the color escape format the block renderer should target.

    Rules, in priority order:
      1. Explicit override (CLI flag) wins.
      2. COLORTERM=truecolor|24bit -> truecolor.  This is the de facto
         standard signal; iTerm2, WezTerm, Ghostty, Warp, Alacritty, Kitty,
         and recent xterm all set it.
      3. macOS Terminal.app (TERM_PROGRAM=Apple_Terminal) -> 256-color.
         Apple still silently quantizes truecolor escapes with no dither,
         so we pre-dither ourselves.
      4. TERM ending in -direct -> truecolor (terminfo convention).
      5. Default truecolor — worst case matches the silent-quantize behavior
         the user would have gotten anyway.
    """
    if prefer:
        mapping = {"truecolor": ColorDepth.TRUECOLOR, "256": ColorDepth.PALETTE_256}
        if prefer in mapping:
            return mapping[prefer]

    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return ColorDepth.TRUECOLOR

    if os.environ.get("TERM_PROGRAM") == "Apple_Terminal":
        return ColorDepth.PALETTE_256

    if os.environ.get("TERM", "").endswith("-direct"):
        return ColorDepth.TRUECOLOR

    return ColorDepth.TRUECOLOR


def detect_renderer(prefer: str | None = None) -> RendererType:
    """
    Choose the graphics protocol to use for the current terminal.

    Conservative ordering: identify positive signals for a high-quality
    protocol, otherwise fall back to block fills.
    """
    if prefer:
        rt = _RENDERER_NAMES.get(prefer.lower())
        if rt:
            return rt

    # tmux and SSH: pixel protocols are unreliable across these transports.
    # Inline-image and kitty escapes get stripped or mangled.  Force block
    # unless the user explicitly opted in to a pixel renderer.
    if os.environ.get("TMUX") or os.environ.get("SSH_TTY"):
        return RendererType.BLOCK

    if os.environ.get("KITTY_WINDOW_ID"):
        return RendererType.KITTY

    term_program = os.environ.get("TERM_PROGRAM", "")
    if term_program == "iTerm.app":
        return RendererType.ITERM2

    # WezTerm and Ghostty both support the Kitty graphics protocol.
    if term_program in ("WezTerm", "ghostty"):
        return RendererType.KITTY

    # Windows Terminal — doesn't set TERM_PROGRAM but has its own marker.
    # Sixel support landed in WT in late 2023.
    if os.environ.get("WT_SESSION"):
        return RendererType.SIXEL

    if _check_sixel_support():
        return RendererType.SIXEL

    return RendererType.BLOCK


def detect_environment() -> dict[str, bool]:
    """
    Returns a flat dict describing the current terminal environment.
    Used by `tv -v` to give the user a single-line diagnosis when something
    looks wrong.
    """
    return {
        "tmux": bool(os.environ.get("TMUX")),
        "ssh": bool(os.environ.get("SSH_TTY")),
        "tty_stdout": sys.stdout.isatty(),
        "tty_stdin": sys.stdin.isatty(),
    }


def terminal_size() -> tuple[int, int]:
    """Return (columns, rows) of the controlling terminal."""
    size = shutil.get_terminal_size((80, 24))
    return size.columns, size.lines


# ---------------------------------------------------------------------- internal

def _check_sixel_support() -> bool:
    """Query terminal via DA1 (\\033[c) and look for capability 4."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return False
    try:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            sys.stdout.write("\033[c")
            sys.stdout.flush()

            ready, _, _ = select.select([sys.stdin], [], [], 0.3)
            if not ready:
                return False

            response = ""
            while True:
                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                if not ready:
                    break
                ch = sys.stdin.read(1)
                response += ch
                if ch == "c":
                    break

            # Response: ESC [ ? <cap1> ; <cap2> ; ... c
            inner = response.lstrip("\033[?").rstrip("c")
            return "4" in inner.split(";")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        return False
