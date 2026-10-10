"""Tests for image extraction (files on disk + positioned IMAGE blocks)."""

from pathlib import Path

import pymupdf

from app.adapters.storage.file_store import LocalFileStore
from app.services.image_extractor import ImageExtractor
from app.services.pipeline.blocks import IMAGE
from tests.helpers import make_png


def _build_pdf(tmp_path: Path) -> str:
    document = pymupdf.open()
    page = document.new_page()
    big = make_png(tmp_path / "big.png", width=100, height=80)
    page.insert_image(pymupdf.Rect(72, 72, 172, 152), filename=big)
    small = make_png(tmp_path / "small.png", width=20, height=20)
    page.insert_image(pymupdf.Rect(72, 300, 92, 320), filename=small)
    path = tmp_path / "images.pdf"
    document.save(str(path))
    document.close()
    return str(path)


async def test_extracts_large_images_and_skips_tiny_ones(tmp_path: Path) -> None:
    pdf_path = _build_pdf(tmp_path)
    file_store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    extractor = ImageExtractor(file_store)

    with pymupdf.open(pdf_path) as document:
        blocks = await extractor.extract_page(document, "job-1", 0)

    assert len(blocks) == 1  # the 20x20 image is treated as an icon and skipped
    block = blocks[0]
    assert block.kind == IMAGE
    assert block.page == 0
    assert block.image_path.startswith("assets/")
    assert block.alt == Path(block.image_path).name
    # The image file really exists under the job's output directory
    saved = Path(file_store.get_output_path("job-1", block.image_path))
    assert saved.is_file()
    assert saved.read_bytes().startswith(b"\x89PNG")


async def test_repeated_image_is_saved_once(tmp_path: Path) -> None:
    document = pymupdf.open()
    image = make_png(tmp_path / "logo.png", width=60, height=60)
    for _ in range(3):
        page = document.new_page()
        page.insert_image(pymupdf.Rect(72, 72, 132, 132), filename=image)
    pdf_path = tmp_path / "repeated.pdf"
    document.save(str(pdf_path))
    document.close()

    file_store = LocalFileStore(str(tmp_path / "uploads"), str(tmp_path / "outputs"))
    extractor = ImageExtractor(file_store)

    with pymupdf.open(str(pdf_path)) as doc:
        all_blocks = []
        for page_number in range(3):
            all_blocks.extend(await extractor.extract_page(doc, "job-2", page_number))

    assert len(all_blocks) == 3  # one block per page
    # but only ONE file on disk (deduplicated by xref)
    paths = {block.image_path for block in all_blocks}
    assert len(paths) == 1
    saved = Path(file_store.get_output_path("job-2", paths.pop()))
    assert saved.is_file()
