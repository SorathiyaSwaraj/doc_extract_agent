import math

import pytest

from doc_extract.ingestion.layout import Line, group_rows, lines_to_text, page_skew


def test_rows_are_rebuilt_from_scrambled_boxes():
    lines = [
        Line("6,779.00", (400, 52, 450, 62)),
        Line("Invoice #: A1", (50, 10, 120, 20)),
        Line("Total Amount", (300, 50, 360, 60)),
    ]

    assert lines_to_text(lines) == "Invoice #: A1\nTotal Amount   6,779.00"


def test_slightly_skewed_boxes_stay_on_one_row():
    lines = [Line("IGST", (300, 100, 330, 110)), Line("1,034.15", (400, 103, 450, 113))]

    assert len(group_rows(lines)) == 1


def skewed_row(y, degrees, cells):
    """Cells of one table row on a page rotated by `degrees`, each box tilted the same way."""
    slope = math.tan(math.radians(degrees))
    lines = []
    for text, left in cells:
        right = left + 8 * len(text)
        top = y + left * slope
        lines.append(
            Line(text, (left, top, right, top + 9 + (right - left) * slope), angle=degrees)
        )
    return lines


def test_skewed_table_rows_stay_whole():
    """At 1.5 degrees the last cell sits 13 pt below the first: more than half a line."""
    cells = [("1", 50), ("Laptop stand, aluminium", 80), ("8473", 300), ("37430.16", 500)]
    lines = skewed_row(100, 1.5, cells) + skewed_row(114, 1.5, cells)

    rows = group_rows(lines)

    assert page_skew(lines) == pytest.approx(1.5)
    assert [len(row) for row in rows] == [4, 4]
    assert [line.text for line in rows[0]] == [text for text, _ in cells]


def test_short_boxes_do_not_set_the_skew():
    lines = [Line("x", (0, 0, 8, 10), angle=30), Line("long line of text", (0, 20, 200, 30))]

    assert page_skew(lines) == 0


def test_close_words_join_with_one_space():
    lines = [Line("Round", (300, 100, 330, 110)), Line("Off", (332, 100, 350, 110))]

    assert lines_to_text(lines) == "Round Off"


def test_lines_half_a_line_apart_are_separate_rows():
    lines = [Line("first", (0, 0, 50, 10)), Line("second", (0, 6, 50, 16))]

    assert len(group_rows(lines)) == 2
