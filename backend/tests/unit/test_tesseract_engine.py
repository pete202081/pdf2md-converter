"""The OCR adapter decodes a PNG and forwards it to Tesseract."""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.adapters.ocr.tesseract_engine import TesseractOcrEngine
from app.domain.exceptions import ProcessingError
from tests.helpers import make_png


def test_ocr_decodes_png_and_passes_language(tmp_path: Path) -> None:
    image = Path(make_png(tmp_path / "scan.png")).read_bytes()
    engine = TesseractOcrEngine(language="eng")
    with patch("app.adapters.ocr.tesseract_engine.pytesseract.image_to_string") as run:
        run.return_value = "Scanned text"
        assert engine.ocr_image(image) == "Scanned text"
        assert run.call_args.kwargs["lang"] == "eng"
        assert run.call_args.args[0].size == (100, 80)


def test_ocr_errors_are_typed(tmp_path: Path) -> None:
    image = Path(make_png(tmp_path / "scan.png")).read_bytes()
    engine = TesseractOcrEngine()
    with patch("app.adapters.ocr.tesseract_engine.pytesseract.image_to_string") as run:
        run.side_effect = OSError("tesseract is not installed")
        with pytest.raises(ProcessingError, match="OCR failed"):
            engine.ocr_image(image)
