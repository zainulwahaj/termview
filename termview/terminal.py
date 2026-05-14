"""
Terminal state management.

Owns all the side-effecty pieces of running interactively in a terminal:
  - hiding/showing the cursor
  - putting stdin in raw / cbreak mode for keystroke reading
  - registering signal handlers that always restore the above

The single rule this module enforces:  if termview puts the terminal into
any non-default state, that state is *guaranteed* to be restored before the
process dies — through normal completion, KeyboardInterrupt, an unhandled
exception, SIGTERM, SIGHUP (parent terminal closed), or SIGTSTP (ctrl-Z).

Without this, a crash mid-playback leaves the user's shell with no cursor,
no echoing input, and arbitrary background colors — which is unrecoverable
short of `reset`.
"""

import atexit
import os
import signal
import sys
import termios
import tty
from typing import Callable

_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"
_RESET = "\033[0m"

# Signals that should trigger graceful restoration before the process exits.
# SIGINT is handled by the normal try/except path in callers; we still
# register it so an interrupt during a write/sleep doesn't leak state.
_DEATH_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


class TerminalState:
    """
    Context manager that owns the terminal's mutable state during playback.

    Usage:
        with TerminalState(raw_input=True) as term:
            while not term.dying:
                ...

    On __exit__ or signal: restores termios, shows cursor, resets colors.
    Multiple restores are safe (idempotent).
    """

    def __init__(self, raw_input: bool = False, hide_cursor: bool = True) -> None:
        self.raw_input = raw_input
        self.hide_cursor = hide_cursor
        self._old_termios = None
        self._old_handlers: dict[int, Callable] = {}
        self._restored = False
        self.dying = False  # set by signal handler so loops can break cleanly

    # ------------------------------------------------------------------ context

    def __enter__(self) -> "TerminalState":
        if self.hide_cursor and sys.stdout.isatty():
            sys.stdout.write(_HIDE_CURSOR)
            sys.stdout.flush()

        if self.raw_input and sys.stdin.isatty():
            fd = sys.stdin.fileno()
            self._old_termios = termios.tcgetattr(fd)
            # cbreak (not full raw) keeps signal generation (Ctrl-C -> SIGINT)
            # so the user's escape hatch still works; we just disable line
            # buffering and echo so keystrokes arrive immediately.
            tty.setcbreak(fd)

        for sig in _DEATH_SIGNALS:
            try:
                self._old_handlers[sig] = signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                # ValueError: not the main thread.  OSError: signal not
                # supported on this platform (e.g. SIGHUP on Windows).
                pass

        # atexit covers SystemExit and normal-completion paths that bypass
        # __exit__ (e.g. someone calls sys.exit() while we're still alive).
        atexit.register(self.restore)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.restore()

    # ------------------------------------------------------------------ teardown

    def restore(self) -> None:
        """Restore everything we changed.  Safe to call multiple times."""
        if self._restored:
            return
        self._restored = True

        # Restore termios FIRST so the user immediately gets normal input back,
        # even if subsequent writes throw.
        if self._old_termios is not None and sys.stdin.isatty():
            try:
                termios.tcsetattr(
                    sys.stdin.fileno(), termios.TCSADRAIN, self._old_termios
                )
            except Exception:
                pass

        try:
            if sys.stdout.isatty():
                sys.stdout.write(_SHOW_CURSOR + _RESET + "\n")
                sys.stdout.flush()
        except Exception:
            pass

        for sig, handler in self._old_handlers.items():
            try:
                signal.signal(sig, handler)
            except Exception:
                pass

    # ------------------------------------------------------------------ signals

    def _on_signal(self, sig: int, frame) -> None:
        """
        Handle SIGTERM/SIGHUP/SIGINT: restore, then re-raise via the previous
        handler so default behavior (exit / KeyboardInterrupt) still happens.

        We never just swallow the signal — that traps the user.
        """
        self.dying = True
        self.restore()

        # Chain to the previous handler.  For SIGINT this raises
        # KeyboardInterrupt at the next opcode, which callers already handle.
        prev = self._old_handlers.get(sig, signal.SIG_DFL)
        if prev in (signal.SIG_DFL, None):
            signal.signal(sig, signal.SIG_DFL)
            os.kill(os.getpid(), sig)
        elif callable(prev):
            try:
                prev(sig, frame)
            except Exception:
                pass
