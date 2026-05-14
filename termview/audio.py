"""
Audio playback for video files via an external subprocess.

We do *not* decode or mix audio in-process — that would require either PyAV
(50 MB+ C extension) or PyAudio + manual A/V sync, both of which are vastly
more complex than the actual problem.  Instead we shell out to the first
available audio player on the system:

    ffplay      — cross-platform, ships with ffmpeg, handles every format
    afplay      — built into macOS, handles mp3/m4a/wav but not raw video
    paplay      — Linux PulseAudio, handles wav only

ffplay is the only one that decodes audio out of arbitrary video containers,
so it's the one we actively support.  The others are documented fallbacks for
when the user has audio files (.mp3 etc.) rather than video.

Sync model
----------
We don't try to read the audio process's clock.  Instead:
  - audio subprocess is started at wall-clock T₀ and paces itself.
  - the video render loop also derives its target frame time from T₀.
  - both run off the same origin → drift is bounded by the audio player's
    internal A/V skew correction (ffplay's is good for hours of playback).

Pause / seek invalidate the audio subprocess and we respawn at the new
position with `-ss <seconds>`.  Spawn latency is ~250ms, accepted as the
cost of audio sync.
"""

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path


def _find_player() -> str | None:
    """Return the path to ffplay if installed, else None."""
    return shutil.which("ffplay")


def is_available() -> bool:
    return _find_player() is not None


def install_hint() -> str:
    if sys.platform == "darwin":
        return "Install with:  brew install ffmpeg"
    if sys.platform.startswith("linux"):
        return "Install with:  apt install ffmpeg   (or your distro's equivalent)"
    return "Install ffmpeg from https://ffmpeg.org/download.html"


class AudioPlayer:
    """
    Wraps an ffplay subprocess. Each method is best-effort — audio is a
    nice-to-have; failures must never crash video playback.
    """

    def __init__(self, path: Path, volume: int = 100) -> None:
        self.path = path
        self.volume = max(0, min(100, volume))
        self.muted = False
        self._proc: subprocess.Popen | None = None
        self._start_offset: float = 0.0  # seconds into the file
        self._player = _find_player()

    @property
    def available(self) -> bool:
        return self._player is not None

    # ------------------------------------------------------------------ control

    def start(self, position_sec: float = 0.0) -> None:
        """Start (or restart) audio at the given position."""
        if not self.available:
            return
        self.stop()
        self._start_offset = max(0.0, position_sec)

        vol = 0 if self.muted else self.volume
        cmd = [
            self._player,
            "-nodisp",                # no video window
            "-autoexit",              # die when file ends
            "-loglevel", "quiet",
            "-volume", str(vol),
            "-ss", f"{self._start_offset:.3f}",
            str(self.path),
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # New process group so SIGINT to our terminal doesn't reach it
                # before we get a chance to cleanly SIGTERM.
                start_new_session=True,
            )
        except (OSError, FileNotFoundError):
            self._proc = None

    def stop(self) -> None:
        """Terminate the audio subprocess if running.  Idempotent."""
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                # SIGTERM gives ffplay a chance to close its audio device cleanly,
                # which avoids the "audio drop-out into next thing you play"
                # CoreAudio bug on macOS.
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                try:
                    self._proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        finally:
            self._proc = None

    def toggle_mute(self) -> bool:
        """Toggle mute. Returns new muted state.  Requires respawn."""
        self.muted = not self.muted
        return self.muted

    def set_volume(self, vol: int) -> None:
        self.volume = max(0, min(100, vol))

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------ context

    def __enter__(self) -> "AudioPlayer":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
