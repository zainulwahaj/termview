"""
Block renderer: paint one image pixel per terminal cell using ANSI background
fills with a space character.  Works in every terminal that supports 24-bit
or 256-color ANSI escapes.  Background fills paint through inter-row line
spacing, so output is gap-free regardless of font or line-height settings.

Two code paths:
  truecolor (24-bit) — \\033[48;2;R;G;Bm  (modern terminals)
  256-color palette  — \\033[48;5;Nm        (Apple Terminal etc.)

Performance: row encoding is vectorized.  We compute "color change points"
with numpy (one comparison per pixel, in C), then emit one escape sequence
per run instead of one per pixel.  Typical photographic frame at 100×50:
~5000 cells collapse to ~500 escape sequences.

For 256-color, dithering is optional; on video frames we turn it off (see
stream.py for the reasoning).
"""

import numpy as np
from PIL import Image

from ..detect import ColorDepth
from .base import BaseRenderer

_RESET = "\033[0m"

# Standard xterm 256-color palette (per the xterm and ITU specifications).
#   indices 16..231  : 6×6×6 color cube, axis values [0, 95, 135, 175, 215, 255]
#   indices 232..255 : 24-step grayscale ramp, value = 8 + 10*i
_CUBE_AXIS = (0, 95, 135, 175, 215, 255)


# ---------------------------------------------------------------------- palette

def _build_xterm256_palette_image() -> Image.Image:
    """
    Build a PIL palette image mirroring the xterm 256-color layout for
    indices 16..255 (the 240 entries that aren't the user-themable base 16).

    PIL palette images carry exactly 256 RGB entries.  Layout:
        positions 0..215   <- ANSI 16..231   (color cube)
        positions 216..239 <- ANSI 232..255  (grayscale ramp)
        positions 240..255 <- duplicate of cube black so any spurious match
                              still maps to a valid, sensible color.
    """
    entries: list[int] = []
    for r in _CUBE_AXIS:
        for g in _CUBE_AXIS:
            for b in _CUBE_AXIS:
                entries.extend([r, g, b])
    for i in range(24):
        v = 8 + i * 10
        entries.extend([v, v, v])
    while len(entries) < 256 * 3:
        entries.extend([0, 0, 0])

    p_img = Image.new("P", (1, 1))
    p_img.putpalette(entries)
    return p_img


def _build_palette_idx_to_ansi() -> np.ndarray:
    """Pre-compute a 256-entry lookup table from palette index -> ANSI code."""
    lut = np.zeros(256, dtype=np.int32)
    for i in range(216):
        lut[i] = 16 + i
    for i in range(216, 240):
        lut[i] = 232 + (i - 216)
    for i in range(240, 256):
        lut[i] = 16  # padding -> cube black
    return lut


_PALETTE_IMG: Image.Image | None = None
_ANSI_LUT: np.ndarray | None = None


def _palette_img() -> Image.Image:
    global _PALETTE_IMG
    if _PALETTE_IMG is None:
        _PALETTE_IMG = _build_xterm256_palette_image()
    return _PALETTE_IMG


def _ansi_lut() -> np.ndarray:
    global _ANSI_LUT
    if _ANSI_LUT is None:
        _ANSI_LUT = _build_palette_idx_to_ansi()
    return _ANSI_LUT


# ---------------------------------------------------------------------- renderer

class BlockRenderer(BaseRenderer):
    """ANSI background-fill renderer.  See module docstring for details."""

    def __init__(
        self,
        color_depth: ColorDepth = ColorDepth.TRUECOLOR,
        dither: bool = True,
    ) -> None:
        self.color_depth = color_depth
        # Dithering helps still-image fidelity in the 256-color path but is
        # destructive for video: FS dither re-rolls per frame (because pixel
        # values shift slightly), producing flickering static and defeating
        # the per-row color-change dedup.  Video callers pass dither=False.
        self.dither = dither

    def render(self, img: Image.Image) -> str:
        # Flatten transparency so RGBA pixels don't leak raw bytes.
        if img.mode == "RGBA":
            flat = Image.new("RGB", img.size, (0, 0, 0))
            flat.paste(img, mask=img.split()[-1])
            img = flat
        else:
            img = img.convert("RGB")

        if self.color_depth == ColorDepth.TRUECOLOR:
            codes = self._truecolor_codes(np.asarray(img, dtype=np.uint8))
        else:
            codes = self._palette_codes(img)

        return self._encode_runs(codes)

    # ------------------------------------------------------------------ codes

    def _truecolor_codes(self, arr: np.ndarray) -> np.ndarray:
        """
        Encode pixels as a single int32 per cell so equality comparison is
        one numpy op.  We pack RGB into the low 24 bits.
        """
        # arr shape: (H, W, 3), dtype uint8
        packed = (
            (arr[..., 0].astype(np.int32) << 16)
            | (arr[..., 1].astype(np.int32) << 8)
            | arr[..., 2].astype(np.int32)
        )
        return packed

    def _palette_codes(self, img: Image.Image) -> np.ndarray:
        """Quantize to xterm 256 palette and map indices -> ANSI codes."""
        quantized = img.quantize(
            palette=_palette_img(),
            dither=Image.Dither.FLOYDSTEINBERG if self.dither else Image.Dither.NONE,
        )
        indices = np.asarray(quantized, dtype=np.uint8)
        return _ansi_lut()[indices]  # shape (H, W), values are ANSI codes

    # ------------------------------------------------------------------ encoding

    def _encode_runs(self, codes: np.ndarray) -> str:
        """
        Emit one ANSI escape per *run* of identical codes (rather than per
        pixel).  This is where the vectorized hot-path pays off — we compute
        run starts with a single numpy compare per row.
        """
        h, w = codes.shape
        is_truecolor = self.color_depth == ColorDepth.TRUECOLOR

        lines: list[str] = []
        for y in range(h):
            row = codes[y]
            # Run starts are: index 0, plus any index where row[i] != row[i-1].
            changes = np.concatenate(([True], row[1:] != row[:-1]))
            starts = np.flatnonzero(changes)
            # Lengths of runs from each start.  np.diff(starts) gives the
            # gap to the *next* start; the last run extends to end of row.
            lengths = np.diff(np.concatenate((starts, [w])))

            parts: list[str] = []
            for start, length in zip(starts.tolist(), lengths.tolist()):
                code = int(row[start])
                if is_truecolor:
                    r = (code >> 16) & 0xFF
                    g = (code >> 8) & 0xFF
                    b = code & 0xFF
                    parts.append(f"\033[48;2;{r};{g};{b}m")
                else:
                    parts.append(f"\033[48;5;{code}m")
                parts.append(" " * length)
            parts.append(_RESET)
            lines.append("".join(parts))

        return "\n".join(lines) + "\n"
