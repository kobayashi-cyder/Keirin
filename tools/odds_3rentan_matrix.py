#!/usr/bin/env python3
"""Extract *fully verified* ordered 3-car odds from already-loaded HTML tables.

Research only. No browser requests, API calls, model inference or wagers.
Never infer missing prices. A full 210/336/504 ticket matrix is returned only
when all leaders' numeric row/column grids are unambiguous and every ranked
quote supplied as a cross-check matches the full matrix.
"""
from __future__ import annotations

import itertools
import math
import re
from collections import defaultdict
from html.parser import HTMLParser


class _TableCells(HTMLParser):
    """Collect HTML <table>/<tr>/<td>/<th> text (including nested formatting)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.table_stack = []
        self.row = None
        self.cell = None
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "template"):
            self.skipping += 1
        if self.skipping:
            return
        if tag == "table":
            self.table_stack.append([])
        elif tag == "tr" and self.table_stack:
            self.row = []
        elif tag in ("td", "th") and self.table_stack and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if not self.skipping and self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "template") and self.skipping:
            self.skipping -= 1
            return
        if self.skipping:
            return
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table_stack:
            self.table_stack[-1].append(self.row)
            self.row = None
        elif tag == "table" and self.table_stack:
            table = self.table_stack.pop()
            if table:
                self.tables.append(table)


def _single_number(value):
    """Return a standalone car number 1..9, not a rider name or price."""
    value = value.strip()
    return int(value) if re.fullmatch(r"[1-9]", value) else None


def _odds(value):
    value = value.replace(",", "").strip()
    if not re.fullmatch(r"\d{1,5}(?:\.\d{1,2})?", value):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 1.0 else None


def _table_for_leader(table, cars):
    """Identify a 3rentan grid strictly from a complete row/column index.

    The matrix has n-1 column labels for 2nd place, n-1 row labels for 3rd
    place, and a blank diagonal (2nd and 3rd may not be the same rider).
    Some Kdreams pages repeat the row number in a trailing cell.
    """
    n = len(cars)
    if len(table) < n:
        return None
    for header_idx, header in enumerate(table):
        # Header can have a corner label and blank cells; no extra numeric
        # rider labels or duplicate columns may be present.
        labels = [_single_number(c) for c in header]
        labels = [v for v in labels if v is not None]
        if len(labels) != n - 1 or len(set(labels)) != n - 1:
            continue
        if not set(labels) <= set(cars):
            continue
        leader_candidates = set(cars) - set(labels)
        if len(leader_candidates) != 1:
            continue
        leader = next(iter(leader_candidates))
        rows = table[header_idx + 1:header_idx + n]
        if len(rows) != n - 1:
            continue
        data, seen = {}, set()
        valid = True
        for cells in rows:
            # Row starts with 3rd-place car; may duplicate it at end.
            cells = list(cells)
            if not cells:
                valid = False
                break
            row_car = _single_number(cells[0])
            if row_car is None or row_car not in labels or row_car in seen:
                valid = False
                break
            seen.add(row_car)
            if len(cells) == n + 1 and _single_number(cells[-1]) == row_car:
                cells.pop()
            if len(cells) != n:
                valid = False
                break
            for col_car, cell in zip(labels, cells[1:]):
                if col_car == row_car:
                    if cell.strip() not in ("", "-", "－", "―", "×"):
                        valid = False
                        break
                    continue
                price = _odds(cell)
                if price is None:
                    valid = False
                    break
                data[f"{leader}-{col_car}-{row_car}"] = price
            if not valid:
                break
        if valid and seen == set(labels) and len(data) == (n-1)*(n-2):
            return leader, data
    return None


def recover_three_rentan_matrix(html, cars, ranked_quotes):
    """Return (full_quotes, reason). No speculative partial augmentation.

    An independent ranked top-N quote for the same page is REQUIRED, and
    every quoted combination must match within its 0.1 displayed precision.
    """
    if not isinstance(html, str) or not isinstance(ranked_quotes, dict):
        return None, "invalid_input"
    if cars not in (7, 8, 9) or not ranked_quotes:
        return None, "unsupported_or_missing_ranked_crosscheck"
    all_cars = tuple(range(1, cars + 1))
    parser = _TableCells()
    parser.feed(html)
    parser.close()
    candidates = defaultdict(list)
    for table in parser.tables:
        entry = _table_for_leader(table, all_cars)
        if entry is not None:
            leader, data = entry
            candidates[leader].append(data)
    # Multiple competing grids for the same leader are ambiguous; do not
    # silently select one. Accept duplicated *identical* grids only.
    full = {}
    for leader in all_cars:
        groups = candidates.get(leader, [])
        if not groups or any(group != groups[0] for group in groups):
            return None, f"missing_or_conflicting_matrix_for_leader_{leader}"
        full.update(groups[0])
    expected = {
        "-".join(map(str, order))
        for order in itertools.permutations(all_cars, 3)
    }
    if set(full) != expected:
        return None, "matrix_ticket_set_incomplete"
    for combo, quoted in ranked_quotes.items():
        if combo not in full or not isinstance(quoted, (float, int)):
            return None, "ranked_quote_unverifiable"
        if not math.isclose(full[combo], quoted, rel_tol=0, abs_tol=0.051):
            return None, f"ranked_quote_mismatch_for_{combo}"
    return dict(sorted(full.items())), "crosschecked_full_matrix"
