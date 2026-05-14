"""
Interactive video playback in the terminal.

This is the orchestrator that pulls everything together:
  - decodes frames via OpenCV
  - sends them to the chosen renderer
  - paces playback against a wall clock
  - mirrors playback position into an audio subprocess (ffplay)
  - reads keyboard input for pause/seek/quit/volume
  - polls terminal size every frame for resize handling
  - owns its terminal state via TerminalState (cursor, signals, raw stdin)

Sync model
----------
Wall-clock pacing: the playback origin T₀ is set on start.  Each loop
iteration computes target_time = monotonic() - T₀ + accumulated_pause_time,
seeks the video to that timestamp, decodes, and renders.  Audio is started
at T₀ too and self-paces.  Both drift around the same anchor, so A/V skew
stays under ~100ms over arbitrary-length playback (limited mostly by
ffplay's internal correction, which is solid).
"""

import sys
import time
from pathlib import Path

from PIL import Image

from .audio import AudioPlayer, install_hint, is_available as audio_available
from .controls import Key, read_key
from .detect import ColorDepth, RendererType, terminal_size
from .loader import frame_to_image, iter_frames, load_video, video_fps
from .renderers import get_renderer
from .resize import fit_image
from .terminal import TerminalState

_HOME = "\033[H"
_CLEAR = "\033[2J"
_CLEAR_BELOW = "\033[0J"

# Terminal.app and other 256-color terminals can't process the volume of
# escape codes a 24fps video emits at full resolution.  Cap so the renderer
# doesn't fall behind and produce torn/overlapping frames.
_DEFAULT_FPS_CAP_256 = 12.0

# Default seek deltas (seconds).
_SEEK_SMALL = 5.0
_SEEK_LARGE = 30.0

# Minimum terminal size below which we refuse to render — anything smaller is
# unreadable, and OpenCV-on-low-res-cells produces noise the user won't want.
_MIN_COLS = 20
_MIN_ROWS = 8


def stream_video(
    path: Path,
    renderer_type: RendererType,
    color_depth: ColorDepth = ColorDepth.TRUECOLOR,
    fps_limit: float | None = None,
    enable_audio: bool = True,
    enable_controls: bool = True,
    verbose: bool = False,
) -> None:
    """
    Decode and display a video frame-by-frame in the terminal, with optional
    audio and keyboard controls.

    Controls (when enable_controls=True):
        space       play / pause
        ← / →       seek -5s / +5s
        ↓ / ↑       seek -30s / +30s
        , / .       previous / next frame (while paused, advances by one)
        m           mute / unmute
        + / -       volume up / down
        0           restart from beginning
        q  /  esc   quit
        Ctrl-C      quit

    Audio uses ffplay (from ffmpeg).  Falls back to silent playback if not
    installed, with a one-line warning at start.
    """
    import cv2  # local import — opencv is an optional dep

    cap = load_video(path)
    native_fps = video_fps(cap)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total_frames / native_fps if total_frames > 0 else 0.0

    # Auto-cap fps on slow renderers so the terminal can keep up.
    if fps_limit is None and color_depth == ColorDepth.PALETTE_256:
        target_fps = min(native_fps, _DEFAULT_FPS_CAP_256)
    elif fps_limit is not None:
        target_fps = min(fps_limit, native_fps)
    else:
        target_fps = native_fps
    frame_delay = 1.0 / target_fps

    # Renderer — dither off for video (see BlockRenderer docstring).
    renderer = get_renderer(renderer_type, color_depth=color_depth, dither=False, for_video=True)

    # Audio (best-effort, never fails the playback).
    audio: AudioPlayer | None = None
    if enable_audio:
        if audio_available():
            audio = AudioPlayer(path)
        elif verbose:
            print(
                f"[tv] audio: ffplay not found — playing without sound. "
                f"{install_hint()}",
                file=sys.stderr,
            )

    if verbose:
        ctl = "controls=on" if enable_controls else "controls=off"
        aud = "audio=on" if audio else "audio=off"
        print(
            f"[tv] video {target_fps:.1f}fps "
            f"duration={duration:.1f}s {aud} {ctl}",
            file=sys.stderr,
        )

    raw = enable_controls and sys.stdin.isatty()
    with TerminalState(raw_input=raw) as term:
        try:
            _play_loop(
                cap=cap,
                cv2=cv2,
                renderer=renderer,
                renderer_type=renderer_type,
                native_fps=native_fps,
                target_fps=target_fps,
                frame_delay=frame_delay,
                duration=duration,
                total_frames=total_frames,
                audio=audio,
                enable_controls=enable_controls,
                term=term,
            )
        finally:
            if audio is not None:
                audio.stop()
            cap.release()


# ---------------------------------------------------------------------- play loop

def _play_loop(
    *,
    cap,
    cv2,
    renderer,
    renderer_type: RendererType,
    native_fps: float,
    target_fps: float,
    frame_delay: float,
    duration: float,
    total_frames: int,
    audio: AudioPlayer | None,
    enable_controls: bool,
    term: TerminalState,
) -> None:
    """
    Inner loop.  Pulled out so the surrounding setup/teardown stays readable.

    State machine:
      - playback_origin: wall-clock monotonic time corresponding to t=0 in
        the video.  Adjusted on seek + pause-resume so that
        (monotonic() - playback_origin) is always the current playback
        position in seconds.
      - paused: when True we hold the current frame and only advance on
        explicit frame-step keys.
    """
    sys.stdout.write(_CLEAR + _HOME)
    sys.stdout.flush()
    last_size = terminal_size()

    # Cold-start: read the first frame and display it.  Then start audio
    # and the wall clock together, so audio start latency doesn't desync.
    ret, frame = cap.read()
    if not ret:
        return
    current_pos_sec = 0.0
    last_rendered_frame = frame

    playback_origin = time.monotonic()
    if audio is not None:
        audio.start(position_sec=0.0)

    paused = False
    quit_requested = False

    _render_frame(frame, renderer, renderer_type, last_size, force_clear=True)

    while not quit_requested and not term.dying:
        # 1) Handle terminal resize.
        current_size = terminal_size()
        size_changed = current_size != last_size
        if size_changed:
            sys.stdout.write(_CLEAR + _HOME)
            last_size = current_size

        cols, rows = current_size
        if cols < _MIN_COLS or rows < _MIN_ROWS:
            # Too small to render usefully.  Don't error out — the user
            # might be resizing through a small intermediate state.
            time.sleep(0.1)
            continue

        # 2) Determine target playback position.
        if paused:
            target_pos = current_pos_sec
        else:
            target_pos = time.monotonic() - playback_origin

        # 3) Did playback finish?
        if duration > 0 and target_pos >= duration:
            break

        # 4) Decode the right frame.  When playing forward in real time we
        #    just call cap.read() — it's much faster than seeking.  When we
        #    drift more than ~half a frame from the target we seek to catch up.
        if not paused:
            expected_pos_msec = target_pos * 1000
            actual_pos_msec = cap.get(cv2.CAP_PROP_POS_MSEC)
            drift_ms = expected_pos_msec - actual_pos_msec

            if abs(drift_ms) > (1000 / target_fps) * 1.5:
                # Out of sync — seek directly.
                cap.set(cv2.CAP_PROP_POS_MSEC, expected_pos_msec)

            ret, frame = cap.read()
            if not ret:
                break
            last_rendered_frame = frame
            current_pos_sec = target_pos

            _render_frame(frame, renderer, renderer_type, current_size, force_clear=size_changed)

        elif size_changed:
            # Paused but terminal resized — re-render the held frame at the
            # new size so the image fills the window correctly.
            _render_frame(
                last_rendered_frame, renderer, renderer_type, current_size, force_clear=True
            )

        # 5) Sleep up to one frame interval, but wake on keystrokes for
        #    snappy controls.
        if enable_controls:
            key = read_key(timeout=frame_delay)
        else:
            time.sleep(frame_delay)
            key = Key.NONE

        # 6) Dispatch.
        if key == Key.QUIT:
            quit_requested = True

        elif key == Key.PAUSE:
            paused = not paused
            if paused:
                if audio is not None:
                    audio.stop()
            else:
                # Resume: re-anchor wall clock so playback continues from
                # exactly where we left off.
                playback_origin = time.monotonic() - current_pos_sec
                if audio is not None:
                    audio.start(position_sec=current_pos_sec)

        elif key in (Key.SEEK_BACK, Key.SEEK_FWD, Key.SEEK_BACK_BIG, Key.SEEK_FWD_BIG):
            delta = {
                Key.SEEK_BACK: -_SEEK_SMALL,
                Key.SEEK_FWD: +_SEEK_SMALL,
                Key.SEEK_BACK_BIG: -_SEEK_LARGE,
                Key.SEEK_FWD_BIG: +_SEEK_LARGE,
            }[key]
            new_pos = max(0.0, min(duration - 0.1 if duration else 1e9, current_pos_sec + delta))
            current_pos_sec = new_pos
            cap.set(cv2.CAP_PROP_POS_MSEC, new_pos * 1000)
            playback_origin = time.monotonic() - new_pos
            if audio is not None and not paused:
                audio.start(position_sec=new_pos)

        elif key == Key.RESTART:
            current_pos_sec = 0.0
            cap.set(cv2.CAP_PROP_POS_MSEC, 0.0)
            playback_origin = time.monotonic()
            paused = False
            if audio is not None:
                audio.start(position_sec=0.0)

        elif key in (Key.FRAME_PREV, Key.FRAME_NEXT) and paused:
            step = 1.0 / native_fps
            new_pos = max(0.0, current_pos_sec + (step if key == Key.FRAME_NEXT else -step))
            current_pos_sec = new_pos
            cap.set(cv2.CAP_PROP_POS_MSEC, new_pos * 1000)
            ret, frame = cap.read()
            if ret:
                last_rendered_frame = frame
                _render_frame(frame, renderer, renderer_type, current_size, force_clear=False)

        elif key == Key.MUTE and audio is not None:
            audio.toggle_mute()
            if not paused:
                audio.start(position_sec=current_pos_sec)

        elif key in (Key.VOL_UP, Key.VOL_DOWN) and audio is not None:
            step = 10 if key == Key.VOL_UP else -10
            audio.set_volume(audio.volume + step)
            if not paused:
                audio.start(position_sec=current_pos_sec)


def stream_animation(
    img: Image.Image,
    renderer_type: RendererType,
    color_depth: ColorDepth = ColorDepth.TRUECOLOR,
    enable_controls: bool = True,
    loop: bool = True,
) -> None:
    """
    Play an animated GIF / WebP / APNG.

    Same renderer + terminal-state plumbing as stream_video, but timing
    comes from the per-frame duration metadata baked into the file rather
    than wall-clock pacing.  No audio path (these formats don't carry it).

    Controls (subset of video controls):
      space / p   pause-resume
      q / esc     quit
      0           restart from frame 0
    """
    renderer = get_renderer(renderer_type, color_depth=color_depth, dither=False, for_video=True)
    # Materialize frames up-front: typical GIFs are <100 frames so this is
    # cheap, and it lets us pause/seek without re-decoding the file.
    frames: list[tuple[Image.Image, float]] = list(iter_frames(img))
    if not frames:
        return

    raw = enable_controls and sys.stdin.isatty()
    with TerminalState(raw_input=raw) as term:
        sys.stdout.write(_CLEAR + _HOME)
        sys.stdout.flush()
        last_size = terminal_size()
        idx = 0
        paused = False

        while not term.dying:
            current_size = terminal_size()
            if current_size != last_size:
                sys.stdout.write(_CLEAR + _HOME)
                last_size = current_size
            cols, rows = current_size
            if cols < _MIN_COLS or rows < _MIN_ROWS:
                time.sleep(0.1)
                continue

            frame, duration = frames[idx]
            img_rgb = _flatten_rgba(frame)
            fitted = fit_image(img_rgb, cols, rows, renderer_type, crop=False)
            sys.stdout.write(_HOME)
            sys.stdout.write(renderer.render(fitted))
            sys.stdout.write(_CLEAR_BELOW)
            sys.stdout.flush()

            wait_for = duration if not paused else 0.1
            key = read_key(timeout=wait_for) if enable_controls else Key.NONE

            if key == Key.QUIT:
                break
            if key == Key.PAUSE:
                paused = not paused
                continue
            if key == Key.RESTART:
                idx = 0
                paused = False
                continue
            if paused:
                continue

            idx += 1
            if idx >= len(frames):
                if not loop:
                    break
                idx = 0


def _flatten_rgba(img: Image.Image) -> Image.Image:
    """Composite an RGBA frame against black so transparent pixels read as black
    instead of leaking through to the prior frame's pixels."""
    if img.mode != "RGBA":
        return img
    bg = Image.new("RGB", img.size, (0, 0, 0))
    bg.paste(img, mask=img.split()[-1])
    return bg


def _render_frame(frame, renderer, renderer_type, size, force_clear: bool) -> None:
    """Render one decoded frame to stdout at the given terminal size."""
    cols, rows = size
    img = frame_to_image(frame)
    # crop=False: per-frame autocrop reads each frame's top-left pixel and
    # picks a (potentially different) crop box, causing visible jitter.
    img = fit_image(img, cols, rows, renderer_type, crop=False)

    sys.stdout.write(_HOME)
    sys.stdout.write(renderer.render(img))
    sys.stdout.write(_CLEAR_BELOW)
    sys.stdout.flush()
