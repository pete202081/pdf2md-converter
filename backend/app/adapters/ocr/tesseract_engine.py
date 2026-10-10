"""Tesseract OCR adapter (the tesseract binary is installed in the Docker image)."""

import io

import pytesseract
from PIL import Image

from app.adapters.ocr.base import IOcrEngine
from app.domain.exceptions import ProcessingError


class TesseractOcrEngine(IOcrEngine):
    """OCR via Tesseract through pytesseract."""

    def __init__(self, language: str = "eng") -> None:
        self._language = language

    def ocr_image(self, image_bytes: bytes) -> str:
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                return pytesseract.image_to_string(image, lang=self._language)
        except Exception as exc:
            raise ProcessingError("OCR failed — is the tesseract binary installed?") from exc
