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
    if not candidates:
        # Real Kdreams HTML embeds extra rider-name cells and optional
        # nested tables, so a clean six-car-number header may not exist.
        # Try the more conservative numeric-grid fallback, requiring ALL
        # ranked ticket quotes to agree. Never fill a partially matched grid.
        return _recover_fragmented_tables(parser.tables, all_cars, ranked_quotes)
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


def diagnose_three_rentan_structure(html, cars):
    """Return a limited, price-free fingerprint of likely odds-table shapes.

    Never emit page markup, participant names, odds numbers, or credentials.
    Useful when actual site markup differs from the synthetic 6x6 fixture.
    """
    if not isinstance(html, str) or cars not in (7, 8, 9):
        return {"status": "invalid_input"}
    parser = _TableCells()
    parser.feed(html)
    parser.close()
    candidates = []
    for index, table in enumerate(parser.tables):
        if len(table) < cars - 1:
            continue
        data_rows = sum(
            1 for row in table
            if sum(_odds(cell) is not None for cell in row) >= cars - 3
        )
        if data_rows < cars - 3:
            continue
        header_rows = []
        for row_i, row in enumerate(table):
            numeric_labels = [_single_number(cell) for cell in row]
            numeric_labels = [x for x in numeric_labels if x is not None]
            if len(numeric_labels) == cars - 1 and len(set(numeric_labels)) == cars - 1:
                header_rows.append(row_i)
        widths = [len(row) for row in table]
        def shape(cell):
            if not cell:
                return "_"
            car = _single_number(cell)
            if car is not None:
                return "car"
            if _odds(cell) is not None:
                return "price"
            return "text"
        sample = []
        for row in table[:min(cars + 1, len(table))]:
            sample.append([shape(cell) for cell in row[:cars + 3]])
        candidates.append({
            "table_index": index, "rows": len(table),
            "row_widths": widths[:cars + 3],
            "data_like_rows": data_rows,
            "header_candidates": header_rows[:3],
            "cell_kinds_first_rows": sample,
        })
    return {
        "total_html_tables": len(parser.tables),
        "possible_odds_tables": len(candidates),
        "examples": candidates[:4],
    }



def _decimal_price(value):
    """An odds-cell candidate must have a decimal point, unlike car numbers."""
    cleaned = value.replace(",", "").strip()
    if not re.fullmatch(r"\\d{1,5}\\.\\d{1,2}", cleaned):
        return None
    return _odds(cleaned)


def _fragmented_price_grid(table, n):
    """Recover only contiguous groups of n-1 rows with n-2 decimal prices.

    Kdreams mixes rider names, descriptions and odds cells in each table
    row. The resulting row can be wider than a normal 6x6 matrix. Discard
    all non-price cells, but never infer absent price cells.
    """
    runs = []
    for index, row in enumerate(table):
        prices = [_decimal_price(v) for v in row]
        prices = tuple(p for p in prices if p is not None)
        if len(prices) == n - 2:
            runs.append((index, prices))
    if len(runs) != n - 1:
        return None
    if any(b[0] != a[0] + 1 for a, b in zip(runs, runs[1:])):
        return None
    return tuple(prices for _, prices in runs)


def _fragmented_group_to_quotes(group, cars):
    """Rows index third place; odds columns index second place in car order.

    No diagonal quote exists because 2nd and 3rd cannot be the same rider.
    """
    n = len(cars)
    table = {}
    for first, grid in zip(cars, group):
        other = [car for car in cars if car != first]
        if len(grid) != n - 1:
            return None
        for third, row in zip(other, grid):
            possible_second = [second for second in other if second != third]
            if len(row) != len(possible_second):
                return None
            for second, odd in zip(possible_second, row):
                table[f"{first}-{second}-{third}"] = odd
    return table


def _ranked_quotes_match(quotes, ranked_quotes):
    """Cross-check all popularity-ranked quotes, not just one example."""
    for combo, listed in ranked_quotes.items():
        if (combo not in quotes or type(listed) not in (float, int)
                or not math.isclose(quotes[combo], listed, rel_tol=0, abs_tol=0.051)):
            return False
    return True


def _recover_fragmented_tables(tables, cars, ranked_quotes):
    """Try each n-table HTML grouping; accept only a UNIQUE full solution.

    This fallback deliberately depends on unambiguous DOM order (the leader
    selector shows leaders 1..n in ascending order) and a 50-ticket ranked
    cross-check. If extra layouts yield another interpretation, refuse.
    """
    n = len(cars)
    grids = []
    for table in tables:
        grid = _fragmented_price_grid(table, n)
        if grid is not None:
            # Nested HTML can yield two copies of the same price matrix.
            # Adjacent duplicate copies do not supply new evidence.
            if not grids or grids[-1] != grid:
                grids.append(grid)
    if len(grids) < n:
        return None, f"fragmented_odds_tables_found_{len(grids)}_need_{n}"
    expected = {
        "-".join(map(str, order))
        for order in itertools.permutations(cars, 3)
    }
    accepted = {}
    for start in range(len(grids) - n + 1):
        group = grids[start:start+n]
        odds = _fragmented_group_to_quotes(group, cars)
        if odds is None or set(odds) != expected:
            continue
        if _ranked_quotes_match(odds, ranked_quotes):
            fingerprint = tuple(sorted(odds.items()))
            accepted[fingerprint] = odds
    if len(accepted) != 1:
        return None, f"fragmented_matrix_unique_solutions_{len(accepted)}_from_{len(grids)}_grids"
    return dict(sorted(next(iter(accepted.values())).items())), "crosschecked_full_matrix_from_fragmented_tables"
