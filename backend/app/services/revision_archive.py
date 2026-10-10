"""Safe copy-on-write ZIP update shared by diagram review and chapter edits."""

import json
import shutil
import zipfile
from pathlib import Path

from app.domain.exceptions import ConflictError


def write_revision(
    source: Path,
    destination: Path,
    chapter_path: str,
    chapter_text: str,
    manifest: dict[str, object],
) -> None:
    with zipfile.ZipFile(source) as existing, zipfile.ZipFile(destination, "w") as updated:
        names = set(existing.namelist())
        if chapter_path not in names or "chapters.json" not in names:
            raise ConflictError("ZIP is missing the expected chapter or manifest")
        for member in existing.infolist():
            if member.filename == chapter_path:
                updated.writestr(member, chapter_text.encode("utf-8"))
            elif member.filename == "chapters.json":
                updated.writestr(member, json.dumps(manifest, indent=2).encode("utf-8"))
            else:
                with existing.open(member) as reader, updated.open(member, "w") as writer:
                    shutil.copyfileobj(reader, writer, 1024 * 1024)
