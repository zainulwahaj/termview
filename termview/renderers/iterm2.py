import base64
import io

from PIL import Image

from .base import BaseRenderer


class ITerm2Renderer(BaseRenderer):
    """
    iTerm2 Inline Image Protocol renderer.
    Supported by: iTerm2, WezTerm (as a fallback), some other macOS terminals.

    The entire image is base64-encoded and sent inside a single OSC 1337 sequence.
    """

    def render(self, img: Image.Image) -> str:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()
        encoded = base64.standard_b64encode(raw).decode("ascii")

        w, h = img.size
        args = ";".join(
            [
                "inline=1",
                f"size={len(raw)}",
                f"width={w}px",
                f"height={h}px",
                "preserveAspectRatio=1",
            ]
        )
        # OSC 1337 ; File=<args> : <base64> BEL
        return f"\033]1337;File={args}:{encoded}\007\n"
