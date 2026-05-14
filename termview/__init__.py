"""
termview — view images, animations, and videos in the terminal.

Public API:

    from termview import detect_renderer, get_renderer, fit_image, load_image
    from termview import stream_video, stream_animation

Most users want the `tv` CLI command rather than the library API.
"""

from .audio import AudioPlayer, is_available as audio_available
from .controls import Key, read_key
from .detect import (
    ColorDepth,
    RendererType,
    detect_color_depth,
    detect_environment,
    detect_renderer,
    terminal_size,
)
from .loader import (
    is_animated,
    is_image,
    is_video,
    iter_frames,
    load_image,
)
from .renderers import get_renderer
from .resize import fit_image
from .stream import stream_animation, stream_video
from .terminal import TerminalState

__all__ = [
    "AudioPlayer",
    "ColorDepth",
    "Key",
    "RendererType",
    "TerminalState",
    "audio_available",
    "detect_color_depth",
    "detect_environment",
    "detect_renderer",
    "fit_image",
    "get_renderer",
    "is_animated",
    "is_image",
    "is_video",
    "iter_frames",
    "load_image",
    "read_key",
    "stream_animation",
    "stream_video",
    "terminal_size",
]
