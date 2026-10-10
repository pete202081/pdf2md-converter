"""OCR port (Dependency Inversion: the text extractor depends on this, not on Tesseract)."""

from abc import ABC, abstractmethod


class IOcrEngine(ABC):
    """Optical character recognition for page images."""

    @abstractmethod
    def ocr_image(self, image_bytes: bytes) -> str:
        """Return the recognized plain text for one page image (PNG bytes)."""
