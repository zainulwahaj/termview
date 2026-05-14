from pathlib import Path
from typing import Iterator

from PIL import Image

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".ico"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v", ".ts"}

# Subset of formats that may carry an animation (multi-frame).  We don't list
# everything PIL can decode; only the ones users actually animate.
ANIMATED_EXTENSIONS = {".gif", ".webp", ".png", ".apng"}


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def is_animated(img: Image.Image) -> bool:
    """True if *img* is a multi-frame image (animated GIF/WebP/APNG)."""
    return getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1


def load_image(path: Path) -> Image.Image:
    """
    Load an image.  Returns the PIL Image with n_frames + is_animated
    attributes intact, so callers can detect animation themselves.

    Note: for animated formats we deliberately do NOT call .load() up-front,
    because that would force all frames into memory.  The caller iterates
    frames via iter_frames() instead.
    """
    img = Image.open(path)
    if is_animated(img):
        # Don't pre-convert — frames are loaded lazily by iter_frames().
        return img
    img.load()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    return img


def iter_frames(img: Image.Image) -> Iterator[tuple[Image.Image, float]]:
    """
    Yield (frame_image, duration_seconds) pairs for an animated image.

    Each frame is composited into RGB so transparent GIF frames don't show
    stale pixels from prior frames.  Per-frame duration is read from the
    GIF/WebP `duration` metadata in ms (default 100ms when missing —
    matches Pillow's documented fallback).
    """
    n = getattr(img, "n_frames", 1)
    for i in range(n):
        img.seek(i)
        duration_ms = img.info.get("duration", 100)
        frame = img.convert("RGBA")  # composite vs background later
        yield frame, max(0.02, duration_ms / 1000.0)


def load_video(path: Path):
    """Return an OpenCV VideoCapture for the given path."""
    try:
        import cv2
    except ImportError:
        raise ImportError(
            "opencv-python-headless is required for video playback.\n"
            "Install it with: pip install termview[video]"
        ) from None

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise IOError(f"Cannot open video: {path}")
    return cap


def video_fps(cap) -> float:
    import cv2

    fps = cap.get(cv2.CAP_PROP_FPS)
    return fps if fps > 0 else 24.0


def frame_to_image(frame) -> Image.Image:
    """Convert an OpenCV BGR frame to a PIL RGB image."""
    import cv2

    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
