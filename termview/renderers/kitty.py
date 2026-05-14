"""
Kitty Graphics Protocol renderer.
Supported by: Kitty, WezTerm, Ghostty.

Images are transmitted via APC escape sequences (ESC _ G ... ESC \\) carrying
base64-encoded pixel data, chunked when the payload exceeds the protocol's
single-message limit.

For maximum responsiveness we send raw RGBA bytes (f=32) rather than
PNG (f=100).  Raw RGBA is larger on the wire but skips PNG compression,
which is the dominant per-frame cost.  Single-image responsiveness is
unaffected; video responsiveness improves by ~5×.

For video playback we reuse a single image ID and let the terminal replace
the previous image in place — without this, every frame leaks into the
terminal's image memory until it eventually evicts older entries.
"""

import base64
import io

from PIL import Image

from .base import BaseRenderer

# Kitty's APC payload limit is 4 096 base64 chars per chunk.
_CHUNK = 4096

# Reserved image ID for in-place playback.  Any user-installed Kitty session
# could theoretically conflict; pick a high number that's unlikely to clash.
_VIDEO_IMAGE_ID = 9091


class KittyRenderer(BaseRenderer):
    def __init__(self, for_video: bool = False) -> None:
        self.for_video = for_video

    def render(self, img: Image.Image) -> str:
        img = img.convert("RGBA")
        w, h = img.size

        # Raw RGBA bytes, base64-encoded.  PIL's tobytes() is contiguous in
        # the expected row-major order for f=32.
        encoded = base64.standard_b64encode(img.tobytes()).decode("ascii")
        chunks = [encoded[i : i + _CHUNK] for i in range(0, len(encoded), _CHUNK)]

        parts: list[str] = []
        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            m = 0 if is_last else 1
            if i == 0:
                # a=T  : transmit + display now
                # f=32 : RGBA, 4 bytes per pixel
                # s,v  : image pixel dimensions
                # q=2  : suppress per-frame OK/ERROR responses (would dirty stdout)
                header = f"a=T,f=32,s={w},v={h},q=2,m={m}"
                if self.for_video:
                    # Same image ID every frame -> Kitty replaces in place.
                    header += f",i={_VIDEO_IMAGE_ID}"
            else:
                header = f"m={m}"
            parts.append(f"\033_G{header};{chunk}\033\\")

        return "".join(parts) + "\n"
