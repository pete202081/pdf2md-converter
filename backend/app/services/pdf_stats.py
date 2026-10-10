"""Shared PDF statistics helpers (used by chapter detection and text extraction)."""

from collections import Counter

import pymupdf

_DEFAULT_BODY_SIZE = 11.0


def estimate_body_size(document: pymupdf.Document, sample_pages: int = 20) -> float:
    """Estimate the body text size as the most frequent span size (weighted by length).

    Heading detection everywhere else is relative to this value, so it adapts to
    each document's typography instead of using hard-coded font sizes.
    """
    sizes: Counter[float] = Counter()
    for page_number in range(min(sample_pages, document.page_count)):
        for block in document[page_number].get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = round(span["size"], 1)
                    if size > 0:
                        sizes[size] += len(span["text"])
    if not sizes:
        return _DEFAULT_BODY_SIZE
    return sizes.most_common(1)[0][0]
