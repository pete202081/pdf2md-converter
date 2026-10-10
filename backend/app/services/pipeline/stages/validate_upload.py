"""Stage 1: validate the stored upload (magic bytes, readability, page count)."""

import asyncio
from pathlib import Path
from typing import Any

from app.domain.exceptions import ProcessingError, ValidationError
from app.domain.results import PipelineContext
from app.services.pdf_utils import count_pdf_pages
from app.services.pipeline.stages.base import BasePipelineStage

_PDF_MAGIC = b"%PDF-"


class ValidateUploadStage(BasePipelineStage):
    """Confirms the stored file is a readable PDF within configured limits."""

    name = "validate_upload"

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        await self.report(ctx, 2, "Validating uploaded PDF…")

        path = Path(ctx.upload_path)
        if not path.is_file():
            raise ProcessingError(
                "Uploaded file is missing from storage", details={"path": ctx.upload_path}
            )

        head = await asyncio.to_thread(self._read_head, path)
        if _PDF_MAGIC not in head:
            raise ValidationError("Stored file is not a valid PDF")

        page_count = await asyncio.to_thread(count_pdf_pages, str(path))
        if page_count > ctx.settings.max_pages:
            raise ValidationError(
                "PDF exceeds the maximum supported page count",
                details={"page_count": page_count, "max_pages": ctx.settings.max_pages},
            )

        ctx.data["page_count"] = page_count
        await self.report(ctx, 8, f"Valid PDF with {page_count} pages")
        return {"page_count": page_count}

    @staticmethod
    def _read_head(path: Path, size: int = 1024) -> bytes:
        with path.open("rb") as handle:
            return handle.read(size)
