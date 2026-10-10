"""Shared regular expressions for text classification."""

import re

# Bulleted ("- item", "• item") or numbered ("1. item", "2) item") list lines.
LIST_PATTERN = re.compile(r"^(?:[-*•▪◦‣]|\d{1,3}[.)])\s+(?=\S)")
