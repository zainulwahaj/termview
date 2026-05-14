import numpy as np
from PIL import Image

from .base import BaseRenderer


class SixelRenderer(BaseRenderer):
    """
    Sixel graphics renderer.
    Supported by: xterm (-ti vt340), mlterm, foot, Windows Terminal (partial).

    Sixel encodes pixels in vertical bands of 6 rows.  Each character in a band
    represents one column; its value (ASCII 63–126) is a bitmask over the 6 rows
    indicating which rows are lit for the current color.  We quantize to ≤256
    colors and apply run-length encoding (! count char) to compress repeated cells.
    """

    def render(self, img: Image.Image) -> str:
        img = img.convert("RGB")
        # MEDIANCUT gives good color fidelity; dither=0 for crisp sixel output.
        quantized = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=0)
        palette = quantized.getpalette()  # flat [r,g,b, r,g,b, ...] 0-255
        data = np.array(quantized, dtype=np.uint8)
        h, w = data.shape
        num_colors = len(palette) // 3

        out: list[str] = []
        out.append("\033Pq")  # DCS + q = sixel stream start

        # Emit color definitions: #index;2;r;g;b  (r,g,b scaled to 0-100)
        for i in range(num_colors):
            r = round(palette[i * 3] * 100 / 255)
            g = round(palette[i * 3 + 1] * 100 / 255)
            b = round(palette[i * 3 + 2] * 100 / 255)
            out.append(f"#{i};2;{r};{g};{b}")

        # Process image in horizontal bands of 6 pixel rows.
        for band_y in range(0, h, 6):
            band = data[band_y : band_y + 6]  # (≤6, w)

            # Pad the last band to exactly 6 rows so the bitmask math is uniform.
            if band.shape[0] < 6:
                pad = np.zeros((6 - band.shape[0], w), dtype=np.uint8)
                band = np.vstack([band, pad])

            colors_in_band = np.unique(band)
            first = True

            for ci in colors_in_band:
                if not first:
                    out.append("$")  # CR — return to start of this band for next color
                first = False

                out.append(f"#{ci}")

                # Vectorized: for each column compute a 6-bit mask.
                # bit k is set when row k of this band has color ci.
                mask = np.zeros(w, dtype=np.uint8)
                for row in range(6):
                    mask |= (band[row] == ci).astype(np.uint8) << row

                out.append(_rle_encode(mask))

            out.append("-")  # LF — advance to next band

        out.append("\033\\")  # ST — end of DCS
        return "".join(out)


def _rle_encode(mask: np.ndarray) -> str:
    """
    Convert a column-mask array to a sixel character string with RLE.
    Runs of ≥3 identical characters use the ! count char syntax.
    """
    result: list[str] = []
    i = 0
    n = len(mask)
    while i < n:
        ch = chr(63 + int(mask[i]))
        run = 1
        while i + run < n and mask[i + run] == mask[i] and run < 255:
            run += 1
        if run >= 3:
            result.append(f"!{run}{ch}")
        else:
            result.append(ch * run)
        i += run
    return "".join(result)
