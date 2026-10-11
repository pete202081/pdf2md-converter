"""Stage: assemble chapter Markdown files, chapter folders and the manifest.

This is the "ChapterWriter" from the plan: it writes ``NN_Title/chapter.md``
files in correct order, persists one :class:`Chapter` row per chapter and writes
the ``chapters.json`` manifest at the output root.
"""

import json
from typing import Any

from app.adapters.storage.file_store import IFileStore
from app.db.models import Chapter, DiagramReviewItem
from app.domain.chapters import ChapterInfo
from app.domain.results import PipelineContext
from app.repositories.base import IChapterRepository, IDiagramReviewRepository
from app.services.markdown_assembler import MarkdownAssembler
from app.services.pipeline.blocks import IMAGE, MERMAID, ContentBlock
from app.services.pipeline.stages.base import BasePipelineStage
from app.services.progress_service import ProgressService


class AssembleChaptersStage(BasePipelineStage):
    """Builds and writes the per-chapter Markdown files + manifest."""

    name = "assemble_chapters"

    def __init__(
        self,
        progress: ProgressService,
        file_store: IFileStore,
        chapter_repository: IChapterRepository,
        assembler: MarkdownAssembler | None = None,
        review_repository: IDiagramReviewRepository | None = None,
    ) -> None:
        super().__init__(progress)
        self._file_store = file_store
        self._chapter_repository = chapter_repository
        self._assembler = assembler or MarkdownAssembler()
        self._reviews = review_repository

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        chapters: list[ChapterInfo] = ctx.data.get("chapters") or []
        blocks: list[ContentBlock] = ctx.data.get("blocks") or []
        total = max(len(chapters), 1)
        manifest: list[dict[str, Any]] = []

        for chapter in chapters:
            await ctx.ensure_active()
            chapter_blocks = [
                block for block in blocks if chapter.start_page <= block.page <= chapter.end_page
            ]
            markdown = self._assembler.build(chapter, chapter_blocks)

            relative_md = f"{chapter.folder_name}/chapter.md"
            absolute_md = await self._file_store.save_output_text(ctx.job_id, relative_md, markdown)

            image_count = sum(
                1
                for block in chapter_blocks
                if block.kind == IMAGE or (block.kind == MERMAID and block.image_path)
            )
            mermaid_count = sum(1 for block in chapter_blocks if block.kind == MERMAID)
            word_count = len(markdown.split())

            await self._chapter_repository.create(
                Chapter(
                    job_id=ctx.job_id,
                    order_index=chapter.order_index,
                    title=chapter.title,
                    folder_name=chapter.folder_name,
                    md_path=absolute_md,
                    mermaid_count=mermaid_count,
                    image_count=image_count,
                    word_count=word_count,
                )
            )
            if self._reviews:
                review_data = {item["id"]: item for item in ctx.data.get("diagram_reviews", [])}
                for block in chapter_blocks:
                    if block.review_id and block.review_id in review_data:
                        item = review_data[block.review_id]
                        await self._reviews.create(
                            DiagramReviewItem(
                                id=block.review_id,
                                job_id=ctx.job_id,
                                chapter_order=chapter.order_index,
                                page_number=int(item["page_number"]),
                                occurrence_index=int(item["occurrence_index"]),
                                position_y=float(item["position_y"]),
                                asset_name=str(item["asset_name"]),
                                reason=str(item["reason"]),
                            )
                        )
            manifest.append(
                {
                    "order": chapter.order_index,
                    "title": chapter.title,
                    "folder": chapter.folder_name,
                    "file": relative_md,
                    "source_pages": [chapter.start_page + 1, chapter.end_page + 1],
                    "word_count": word_count,
                    "image_count": image_count,
                    "mermaid_count": mermaid_count,
                }
            )
            percent = 83 + int(chapter.order_index / total * 10)
            await self.report(ctx, percent, f"Assembled {chapter.folder_name}")

        manifest_document = {
            "job_id": ctx.job_id,
            "chapter_count": len(chapters),
            "chapters": manifest,
        }
        await self._file_store.save_output_text(
            ctx.job_id, "chapters.json", json.dumps(manifest_document, indent=2)
        )

        ctx.data["chapter_count"] = len(chapters)
        await self.report(ctx, 93, f"Assembled {len(chapters)} chapter file(s)")
        return {"chapters": len(chapters)}
