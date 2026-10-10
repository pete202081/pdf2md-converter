"""Conservative image heuristic: likely diagrams vs photos/scans/icons.

Looks for large, mostly light and flat regions together with visible edges.
False negatives keep the original image; uncertain images never incur a VLM
request. This is intentionally heuristic, not a semantic vision model.
"""

import io

from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError

_MIN_WIDTH = 160
_MIN_HEIGHT = 100
_MAX_PIXELS = 30_000_000


class DiagramClassifier:
    """Select images likely to depict flowcharts or diagrams."""

    def is_diagram(self, image_bytes: bytes, bbox: tuple[float, ...] | None = None) -> bool:
        # Full-page scanned book pages should go to OCR, never the vision model.
        if bbox and bbox[2] - bbox[0] > 450 and bbox[3] - bbox[1] > 600:
            return False
        try:
            with Image.open(io.BytesIO(image_bytes)) as source:
                width, height = source.size
                if width < _MIN_WIDTH or height < _MIN_HEIGHT or width * height > _MAX_PIXELS:
                    return False
                image = source.convert("RGB")
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            return False

        image.thumbnail((256, 256))
        if max(ImageStat.Stat(image).stddev) < 8:
            return False  # empty/near-solid pages are not diagrams
        raw = image.tobytes()
        pixels = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]
        n = len(pixels)
        light = sum(min(pixel) > 205 for pixel in pixels) / n
        # Count coarse colors; actual photos exhibit many shades, flat-colored
        # diagrams mostly share a small palette (white + a few box colors).
        colors: dict[tuple[int, int, int], int] = {}
        for pixel in pixels:
            coarse = (pixel[0] // 32, pixel[1] // 32, pixel[2] // 32)
            colors[coarse] = colors.get(coarse, 0) + 1
        flatness = sum(sorted(colors.values(), reverse=True)[:8]) / n
        edge_image = image.convert("L").filter(ImageFilter.FIND_EDGES)
        edge_mean = ImageStat.Stat(edge_image).mean[0]
        return light >= 0.45 and flatness >= 0.78 and 3 <= edge_mean <= 65


def prepare_vision_image(image_bytes: bytes) -> bytes:
    """Normalize PDF-embedded formats (including JPEG2000) to bounded PNG."""
    with Image.open(io.BytesIO(image_bytes)) as source:
        width, height = source.size
        if width * height > _MAX_PIXELS:
            raise ValueError("Image exceeds the vision size limit")
        image = source.convert("RGB")
    image.thumbnail((1600, 1600))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
