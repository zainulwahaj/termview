import numpy as np
from PIL import Image, ImageFilter

from .detect import RendererType

# Conservative cell-pixel estimates for pixel-based protocols. Terminals
# re-scale internally, so these only affect target quality, not correctness.
_CELL_W = 8
_CELL_H = 16


def fit_image(
    img: Image.Image,
    cols: int,
    rows: int,
    renderer: RendererType,
    crop: bool = True,
) -> Image.Image:
    """See module docstring.  Video callers pass crop=False because per-frame
    autocrop reads each frame's top-left pixel and can pick different bboxes
    across frames, producing a jittering image."""
    """
    Prepare *img* for terminal display:
      1. Crop uniform borders so the subject uses the full character grid.
      2. Resize, preserving on-screen aspect ratio for the chosen renderer.
      3. For the block renderer, apply unsharp mask to recover detail lost
         to LANCZOS downsampling.

    Reserves one terminal row to keep the next prompt from pushing the top
    of the image off-screen.
    """
    if crop:
        img = autocrop(img)

    w, h = img.size

    if renderer == RendererType.BLOCK:
        return _fit_block(img, w, h, cols, rows)

    # Pixel protocols: terminals re-scale, so we just need a reasonable
    # pixel budget and the protocol handles the cell math.
    max_w = cols * _CELL_W
    max_h = max(_CELL_H, (rows - 1) * _CELL_H)
    scale = min(max_w / w, max_h / h, 1.0)
    if scale < 1.0:
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return img


def _fit_block(img: Image.Image, w: int, h: int, cols: int, rows: int) -> Image.Image:
    """
    Fit for the block (background-fill) renderer: one image pixel per cell.

    Terminal cells are roughly 2× taller than wide. To preserve the image's
    true on-screen aspect ratio, sample twice as densely horizontally as
    vertically. Concretely, fit (w, h) into a (cols, rows) cell grid such that
    every cell that ends up displayed corresponds to one pixel of a (w, h/2)-
    aspect target.
    """
    max_cols = cols
    max_rows = max(1, rows - 1)

    # Allow upscaling for small images so pixel art fills the terminal
    # rather than appearing as a postage stamp.
    scale = min(max_cols / w, (max_rows * 2) / h)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale / 2))

    if (new_w, new_h) == (w, h):
        return img

    if scale < 1.0:
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        # Restore high-frequency detail that LANCZOS averaged away.
        img = img.filter(ImageFilter.UnsharpMask(radius=0.6, percent=110, threshold=2))
    else:
        # Upscaling: preserve crisp edges (pixel art, sprites, icons).
        img = img.resize((new_w, new_h), Image.Resampling.NEAREST)
    return img


def autocrop(img: Image.Image, tolerance: int = 12) -> Image.Image:
    """
    Crop uniform borders. Handles two cases:
      - Transparent borders (RGBA/LA): crop to the alpha bounding box.
      - Solid-color borders (white, black, brand background, etc.): crop to
        the bounding box of pixels that differ from the top-left corner color
        by more than *tolerance* (per-channel max absolute difference).

    Returns the original image if no meaningful crop is possible (e.g. the
    image is already tight, or it's uniformly the border color).
    """
    if img.mode in ("RGBA", "LA"):
        alpha = img.split()[-1]
        bbox = alpha.getbbox()
        if bbox and bbox != (0, 0, *img.size):
            img = img.crop(bbox)

    arr = np.asarray(img.convert("RGB"), dtype=np.int16)
    h, w = arr.shape[:2]
    if h < 4 or w < 4:
        return img

    bg = arr[0, 0]
    diff = np.max(np.abs(arr - bg), axis=2)
    mask = diff > tolerance
    if not mask.any():
        return img

    rows_idx = np.where(mask.any(axis=1))[0]
    cols_idx = np.where(mask.any(axis=0))[0]
    top, bottom = int(rows_idx[0]), int(rows_idx[-1]) + 1
    left, right = int(cols_idx[0]), int(cols_idx[-1]) + 1

    if (left, top, right, bottom) == (0, 0, w, h):
        return img

    return img.crop((left, top, right, bottom))
