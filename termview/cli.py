"""
Command-line entry point for `tv`.

Responsibilities:
  - parse arguments
  - dispatch to the appropriate playback path (image / animation / video)
  - handle stdout-not-a-TTY and broken-pipe scenarios cleanly
  - keep the actual rendering / playback logic out of this file
"""

import argparse
import errno
import sys
from pathlib import Path

from .detect import (
    ColorDepth,
    RendererType,
    detect_color_depth,
    detect_environment,
    detect_renderer,
    terminal_size,
)
from .loader import is_animated, is_image, is_video, load_image
from .renderers import get_renderer
from .resize import fit_image
from .stream import stream_animation, stream_video

_MIN_COLS = 20
_MIN_ROWS = 8


def main() -> None:
    try:
        _main()
    except BrokenPipeError:
        # Piped output closed before we finished writing (e.g. `tv x.png | head`).
        # Suppress and exit cleanly so we don't dump a traceback.
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)


def _main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    path: Path = args.file
    if not path.exists():
        parser.error(f"{path}: no such file or directory")

    renderer_type = detect_renderer(args.renderer)
    color_depth = detect_color_depth(args.depth)
    cols, rows = terminal_size()
    if args.width:
        cols = args.width

    env = detect_environment()

    # Refuse to draw graphics into a non-TTY stdout — we'd corrupt whatever
    # file or pipe the user redirected to with cursor/clear escapes.  Allow
    # `--renderer block` + image to still work if user is piping (it's just
    # text), but disable cursor sequences in that case.
    if not env["tty_stdout"] and is_video(path):
        parser.error("video playback requires stdout to be a terminal")

    if args.verbose:
        _print_diagnostics(renderer_type, color_depth, cols, rows, env)

    # Tiny-terminal guard.
    if cols < _MIN_COLS or rows < _MIN_ROWS:
        sys.stderr.write(
            f"tv: terminal too small ({cols}x{rows}); "
            f"need at least {_MIN_COLS}x{_MIN_ROWS}.\n"
        )
        sys.exit(2)

    if is_image(path):
        img = load_image(path)
        if is_animated(img):
            stream_animation(
                img,
                renderer_type,
                color_depth=color_depth,
                enable_controls=not args.no_controls,
                loop=args.loop,
            )
        else:
            _display_image(img, renderer_type, color_depth, cols, rows, args)
    elif is_video(path):
        stream_video(
            path,
            renderer_type,
            color_depth=color_depth,
            fps_limit=args.fps,
            enable_audio=not args.no_audio,
            enable_controls=not args.no_controls,
            verbose=args.verbose,
        )
    else:
        parser.error(f"{path}: unsupported file type")


def _display_image(img, renderer_type, color_depth, cols, rows, args) -> None:
    original_size = img.size
    fitted = fit_image(img, cols, rows, renderer_type, crop=not args.no_crop)
    if args.verbose:
        print(
            f"[tv] image {original_size[0]}x{original_size[1]} "
            f"-> render {fitted.size[0]}x{fitted.size[1]}",
            file=sys.stderr,
        )
    renderer = get_renderer(renderer_type, color_depth=color_depth)
    renderer.display(fitted)


def _print_diagnostics(renderer_type, color_depth, cols, rows, env) -> None:
    parts = [
        f"renderer={renderer_type.value}",
        f"color={color_depth.value}",
        f"terminal={cols}x{rows}",
    ]
    if env["tmux"]:
        parts.append("tmux=yes")
    if env["ssh"]:
        parts.append("ssh=yes")
    print(f"[tv] {' '.join(parts)}", file=sys.stderr)


# ---------------------------------------------------------------------- argparse

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tv",
        description="View images, animations, and videos in the terminal.",
        epilog=_CONTROLS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("file", type=Path, help="Image, animated GIF, or video file")

    g_render = p.add_argument_group("rendering")
    g_render.add_argument(
        "--renderer",
        choices=[r.value for r in RendererType],
        metavar="NAME",
        help="Force renderer: kitty | iterm2 | sixel | block (default: auto)",
    )
    g_render.add_argument(
        "--depth",
        choices=["truecolor", "256"],
        metavar="DEPTH",
        help="Force color depth (default: auto)",
    )
    g_render.add_argument(
        "--width",
        type=int,
        metavar="COLS",
        help="Override terminal width in columns",
    )
    g_render.add_argument(
        "--no-crop",
        action="store_true",
        help="Disable automatic border cropping (still images only)",
    )

    g_video = p.add_argument_group("video / animation")
    g_video.add_argument(
        "--fps",
        type=float,
        metavar="N",
        help="Limit playback frame rate (default: auto; 12 on 256-color)",
    )
    g_video.add_argument(
        "--no-audio",
        action="store_true",
        help="Disable audio playback (video only)",
    )
    g_video.add_argument(
        "--no-controls",
        action="store_true",
        help="Disable keyboard controls (no pause/seek; for scripts/recording)",
    )
    g_video.add_argument(
        "--loop",
        action="store_true",
        help="Loop animated images (GIF/WebP/APNG). Default: loop forever.",
        default=True,
    )

    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print detection results and per-frame info to stderr",
    )
    return p


_CONTROLS_HELP = """\
Playback controls (video):
  space         play / pause
  ← / →         seek -5s / +5s
  ↓ / ↑         seek -30s / +30s
  , / .         previous / next frame (while paused)
  m             mute / unmute
  + / -         volume up / down
  0             restart from beginning
  q / esc       quit

Requires ffmpeg for audio. Without it, video plays silently.
"""
