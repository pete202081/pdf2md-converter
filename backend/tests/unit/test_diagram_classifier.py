"""Conservative diagram heuristic and normalized vision image."""

import io
import random

from PIL import Image

from app.services.diagram_classifier import DiagramClassifier, prepare_vision_image
from tests.helpers import flowchart_png


def test_flowchart_is_candidate_and_full_page_scan_is_not() -> None:
    classifier = DiagramClassifier()
    image = flowchart_png()
    assert classifier.is_diagram(image)
    assert not classifier.is_diagram(image, bbox=(0, 0, 590, 830))


def test_photo_like_noise_blank_image_and_invalid_bytes_are_not_candidates() -> None:
    classifier = DiagramClassifier()
    randomizer = random.Random(5)
    photo = Image.new("RGB", (200, 120))
    photo.putdata(
        [
            (randomizer.randrange(256), randomizer.randrange(256), randomizer.randrange(256))
            for _ in range(200 * 120)
        ]
    )
    output = io.BytesIO()
    photo.save(output, format="PNG")
    assert not classifier.is_diagram(output.getvalue())
    blank = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(blank, format="PNG")
    assert not classifier.is_diagram(blank.getvalue())
    assert not classifier.is_diagram(b"not an image")


def test_preparation_normalizes_to_bounded_png() -> None:
    output = io.BytesIO()
    Image.open(io.BytesIO(flowchart_png())).save(output, format="JPEG")
    normalized = prepare_vision_image(output.getvalue())
    with Image.open(io.BytesIO(normalized)) as image:
        assert image.format == "PNG"
        assert image.size == (600, 400)
