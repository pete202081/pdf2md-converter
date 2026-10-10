"""Conservative structural validation of untrusted VLM-generated Mermaid.

This is not a full grammar parser. The output is constrained to known diagram
headers and disallows Mermaid directives, interactive links, HTML tags and
code-fence injection. The Phase-4 frontend must still render Mermaid with
``securityLevel: 'strict'`` and ideally validate with ``mermaid.parse()``.
"""

import re

from app.domain.exceptions import ProviderError

_MAX_CHARS = 8000
_HEADER = re.compile(
    r"^(?:flowchart\s+(?:TB|TD|BT|RL|LR)|graph\s+(?:TB|TD|BT|RL|LR)|"
    r"sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|"
    r"gantt|journey|pie|mindmap|timeline|gitGraph)$",
    re.IGNORECASE,
)
_FORBIDDEN = re.compile(
    r"(?:```|%%\s*\{|\b(?:click|href|callback|linkStyle)\b|"
    r"<\s*/?\s*[a-zA-Z]|javascript\s*:|data\s*:|@\s*\{)",
    re.IGNORECASE,
)


def validate_mermaid(response: str) -> str:
    """Strip a single optional Mermaid fence and validate a diagram body.

    Raises ``ProviderError`` on invalid output so fallback can try another
    provider. The caller decides whether to preserve the source image.
    """
    code = response.strip()
    if code.startswith("```mermaid\n") and code.endswith("```"):
        code = code[len("```mermaid\n") : -3].strip()
    if not code or len(code) > _MAX_CHARS or _FORBIDDEN.search(code):
        raise ProviderError("Provider returned unsafe or empty Mermaid code")
    lines = [line.strip() for line in code.splitlines() if line.strip()]
    if len(lines) < 2 or not _HEADER.fullmatch(lines[0]):
        raise ProviderError("Provider returned invalid Mermaid syntax")
    if not any(not line.startswith("%%") for line in lines[1:]):
        raise ProviderError("Provider returned a Mermaid diagram with no content")
    return code
