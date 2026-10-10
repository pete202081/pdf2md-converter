"""Conservative validation of untrusted model output."""

import pytest

from app.domain.exceptions import ProviderError
from app.services.mermaid_validation import validate_mermaid


@pytest.mark.parametrize(
    "response,expected",
    [
        ("flowchart TD\nA[Start] --> B[End]", "flowchart TD\nA[Start] --> B[End]"),
        (
            "```mermaid\nsequenceDiagram\nAlice->>Bob: Hello\n```",
            "sequenceDiagram\nAlice->>Bob: Hello",
        ),
        ("stateDiagram-v2\nA --> B", "stateDiagram-v2\nA --> B"),
    ],
)
def test_accepts_supported_diagrams(response: str, expected: str) -> None:
    assert validate_mermaid(response) == expected


@pytest.mark.parametrize(
    "response",
    [
        "",
        "Here is your flowchart:\nflowchart TD\nA-->B",
        "flowchart TD",  # no content
        "flowchart TD\n%% only a comment",
        "flowchart TD\nA-->B\n```python\nprint(1)",
        "flowchart TD\n%%{init: {'theme': 'dark'}}%%\nA-->B",
        "flowchart TD\nclick A https://evil.example\nA-->B",
        "flowchart TD\nA[<script>alert(1)</script>]-->B",
        "flowchart TD\nA[<img src=x onerror=alert(1)>]-->B",
        "flowchart TD\nA-->B\nlinkStyle 0 stroke:red",
        "flowchart TD\nA-->B\n@{shape: rect}",
        "flowchart TD\nA-->B\n" + "x" * 8000,
    ],
)
def test_rejects_malformed_or_interactive_diagrams(response: str) -> None:
    with pytest.raises(ProviderError):
        validate_mermaid(response)
