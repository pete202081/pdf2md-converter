"""Image extraction: embedded PDF images → files on disk + positioned blocks.

Images are stored once per unique image (xref) under ``assets/`` in the job's
output directory; repeated images (e.g. a logo on every page) are deduplicated
and only referenced multiple times. Tiny images (icons, bullets) are skipped.
"""

from pathlib import Path

import pymupdf

from app.adapters.storage.file_store import IFileStore
from app.logging import get_logger
from app.services.pipeline.blocks import IMAGE, ContentBlock

logger = get_logger("ImageExtractor")

_MIN_IMAGE_SIZE = 40  # pixels — anything smaller is treated as an icon/bullet


class ImageExtractor:
    """Extracts embedded images from PDF pages into the job's output directory."""

    def __init__(self, file_store: IFileStore) -> None:
        self._file_store = file_store
        self._logger = get_logger("ImageExtractor")
        self._xref_cache: dict[int, str] = {}

    async def extract_page(
        self, document: pymupdf.Document, job_id: str, page_number: int
    ) -> list[ContentBlock]:
        """Return IMAGE blocks for one page (0-based ``page_number``)."""
        page = document[page_number]
        try:
            images = page.get_images(full=True)
        except Exception:
            self._logger.exception("image_listing_failed", page=page_number)
            return []

        candidates: list[int] = []
        seen: set[int] = set()
        for image in images:
            xref, width, height = image[0], image[2], image[3]
            if xref in seen:
                continue
            seen.add(xref)
            if width < _MIN_IMAGE_SIZE or height < _MIN_IMAGE_SIZE:
                continue
            candidates.append(xref)

        blocks: list[ContentBlock] = []
        for xref in candidates:
            relative_path = await self._materialize(document, job_id, xref, page_number)
            if not relative_path:
                continue
            alt = Path(relative_path).name
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                rects = []
            if rects:
                for rect in rects:
                    blocks.append(
                        ContentBlock(
                            kind=IMAGE,
                            page=page_number,
                            y=float(rect.y0),
                            image_path=relative_path,
                            alt=alt,
                            bbox=(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
                        )
                    )
            else:
                # No placement info — anchor the image at the bottom of the page.
                blocks.append(
                    ContentBlock(
                        kind=IMAGE,
                        page=page_number,
                        y=float(page.rect.height),
                        image_path=relative_path,
                        alt=alt,
                    )
                )
        return blocks

    async def _materialize(
        self, document: pymupdf.Document, job_id: str, xref: int, page_number: int
    ) -> str | None:
        """Save the image bytes once and return the output-relative path."""
        if xref in self._xref_cache:
            return self._xref_cache[xref]
        try:
            extracted = document.extract_image(xref)
        except Exception:
            self._logger.exception("image_extraction_failed", xref=xref)
            return None
        relative_path = f"assets/img_p{page_number + 1}_x{xref}.{extracted['ext']}"
        try:
            await self._file_store.save_output_bytes(job_id, relative_path, extracted["image"])
        except Exception:
            self._logger.exception("image_save_failed", xref=xref)
            return None
        self._xref_cache[xref] = relative_path
        return relative_path
