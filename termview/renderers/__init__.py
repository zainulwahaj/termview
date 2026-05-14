from ..detect import ColorDepth, RendererType
from .base import BaseRenderer
from .block import BlockRenderer
from .iterm2 import ITerm2Renderer
from .kitty import KittyRenderer
from .sixel import SixelRenderer


def get_renderer(
    renderer_type: RendererType,
    color_depth: ColorDepth = ColorDepth.TRUECOLOR,
    dither: bool = True,
    for_video: bool = False,
) -> BaseRenderer:
    """
    Build a configured renderer.

    Args:
        renderer_type: which graphics protocol.
        color_depth:   24-bit vs 256-color  (block renderer only).
        dither:        Floyd-Steinberg on/off  (block renderer only).
                       Must be False for video — dither flickers per-frame
                       and destroys the per-row dedup.
        for_video:     hint that the renderer will be called many times in
                       succession.  Currently used by KittyRenderer to reuse
                       an image ID so old frames don't accumulate in the
                       terminal's image memory.
    """
    if renderer_type == RendererType.BLOCK:
        return BlockRenderer(color_depth=color_depth, dither=dither)
    if renderer_type == RendererType.KITTY:
        return KittyRenderer(for_video=for_video)
    if renderer_type == RendererType.ITERM2:
        return ITerm2Renderer()
    if renderer_type == RendererType.SIXEL:
        return SixelRenderer()
    raise ValueError(f"Unknown renderer: {renderer_type}")
