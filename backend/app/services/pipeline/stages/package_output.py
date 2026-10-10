"""Stage: package the output tree (ordered chapter folders + manifest) into a ZIP."""

import asyncio
import zipfile
from pathlib import Path
from typing import Any

from app.domain.results import PipelineContext
from app.services.pipeline.stages.base import BasePipelineStage


class PackageOutputStage(BasePipelineStage):
    """Zips the job's output directory; sets ``ctx.data['output_path']``."""

    name = "package_output"

    async def execute(self, ctx: PipelineContext) -> dict[str, Any]:
        await self.report(ctx, 95, "Packaging output…")
        output_root = Path(ctx.output_dir)
        # The ZIP lives next to the output tree so it never contains itself.
        zip_path = output_root.parent / f"{ctx.job_id}.zip"
        stats = await asyncio.to_thread(self._zip_tree, output_root, zip_path)
        ctx.data["output_path"] = str(zip_path)
        await self.report(ctx, 98, f"Packaged {stats['files']} file(s) into a ZIP")
        return stats

    @staticmethod
    def _zip_tree(source: Path, destination: Path) -> dict[str, Any]:
        files = 0
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(source).as_posix())
                    files += 1
        return {"files": files, "zip_path": str(destination)}
