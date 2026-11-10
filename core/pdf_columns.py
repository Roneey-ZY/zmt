"""Conservative left-aligned, text-layer table previews for PDFs."""

from bisect import bisect_right
from collections.abc import Callable

from pdfplumber.utils import cluster_objects


def split_cells(words):
    cells = []
    end = None
    for word in sorted(words, key=lambda value: value["x0"]):
        if end is None or word["x0"] - end >= 11:
            cells.append((word["x0"], [word["text"]]))
        else:
            cells[-1][1].append(word["text"])
        end = word["x1"]
    return [(start, " ".join(parts)) for start, parts in cells]


def borderless_tables(page, resolve: Callable[[str], str | None]):
    """Yield header and rows only where real glyph positions support distinct columns."""
    lines = cluster_objects(page.extract_words(), "top", 3)
    if len(lines) > 400:
        return
    for i, words in enumerate(lines[:-1]):
        header = split_cells(words)
        labels = tuple(label for _, label in header)
        fields = {resolve(label) for label in labels}
        if not 3 <= len(labels) <= 30 or len(fields - {None}) < 2:
            continue
        if not fields & {"sku", "product", "quantity"} or any("|" in label for label in labels):
            continue
        starts = [start for start, _ in header]
        rows = []
        last_top = words[0]["top"]
        previous_gap = None
        for j, data_words in enumerate(lines[i + 1:], i + 2):
            gap = data_words[0]["top"] - last_top
            if gap > 55 or (previous_gap is not None and gap > 2 * previous_gap + 4):
                break
            cells = [[] for _ in starts]
            valid = True
            for word in sorted(data_words, key=lambda value: value["x0"]):
                index = bisect_right(starts, word["x0"] + 3) - 1
                if (index < 0 or
                        (not cells[index] and abs(word["x0"] - starts[index]) > 5) or
                        (index < len(starts) - 1 and word["x1"] > starts[index + 1] - 2)):
                    valid = False
                    break
                cells[index].append(word["text"])
            values = tuple(" ".join(cell) for cell in cells)
            if (not valid or sum(bool(value) for value in values) < 2
                    or sum(resolve(value) is not None for value in values) >= 2):
                break
            rows.append((j, values))
            last_top = data_words[0]["top"]
            previous_gap = gap
        if rows:
            yield i + 1, labels, rows
