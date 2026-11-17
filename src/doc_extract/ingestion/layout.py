"""Rebuild readable rows from positioned text boxes.

OCR returns each table cell as its own box, in no reliable order. Grouping boxes
into rows by vertical overlap keeps "Grand Total" next to its amount, which an
LLM needs to pair labels with values.

On a skewed scan a row slopes, so its last cell can sit more than half a line
below its first. OCR boxes report their own angle, so the page skew is taken as
the median angle of long boxes, and rows are grouped on skew-corrected heights.
"""

import math
import statistics
from dataclasses import dataclass

Box = tuple[float, float, float, float]  # left, top, right, bottom

# Horizontal gap, in average character widths, that becomes a wide column break
COLUMN_GAP_CHARS = 2.5
# Only boxes this many times wider than tall have a reliable angle
MIN_ASPECT_FOR_ANGLE = 4


@dataclass(frozen=True)
class Line:
    text: str
    box: Box
    score: float = 1.0  # OCR confidence; text-layer lines are exact
    angle: float = 0.0  # degrees, positive when the text runs down to the right


def page_skew(lines: tuple[Line, ...] | list[Line]) -> float:
    angles = [line.angle for line in lines if _width(line) >= MIN_ASPECT_FOR_ANGLE * _height(line)]
    return statistics.median(angles) if angles else 0.0


def group_rows(lines: tuple[Line, ...] | list[Line]) -> list[list[Line]]:
    """Group lines whose corrected vertical centres fall inside each other's height."""
    slope = math.tan(math.radians(page_skew(lines)))

    def level(line: Line) -> float:
        centre_x = (line.box[0] + line.box[2]) / 2
        return _centre(line) - centre_x * slope

    rows: list[list[Line]] = []
    for line in sorted(lines, key=lambda line: (level(line), line.box[0])):
        row = rows[-1] if rows else None
        if row and abs(level(line) - level(row[0])) < _height(row[0]) / 2:
            row.append(line)
        else:
            rows.append([line])
    return [sorted(row, key=lambda line: line.box[0]) for row in rows]


def lines_to_text(lines: tuple[Line, ...] | list[Line]) -> str:
    text_rows = []
    for row in group_rows(lines):
        parts = [row[0].text]
        for previous, line in zip(row, row[1:], strict=False):
            gap = line.box[0] - previous.box[2]
            char_width = _width(previous) / max(len(previous.text), 1)
            parts.append("   " if gap > COLUMN_GAP_CHARS * char_width else " ")
            parts.append(line.text)
        text_rows.append("".join(parts))
    return "\n".join(text_rows)


def _centre(line: Line) -> float:
    return (line.box[1] + line.box[3]) / 2


def _width(line: Line) -> float:
    return line.box[2] - line.box[0]


def _height(line: Line) -> float:
    return line.box[3] - line.box[1]
