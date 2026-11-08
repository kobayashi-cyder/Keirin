#!/usr/bin/env python3
"""Conservative parser for PUBLIC, ranked keirin odds as rendered page text.

Only parse rank/combination/price evidence. No scraping or HTTP calls here.
An incomplete market MUST remain incomplete; no fabricated odds or prices.
This parser is intentionally strict and will fail closed on layout changes.
"""
from __future__ import annotations

import itertools
import re

VALID_MARKETS = (
    "3rentan", "2shatan", "3renpuku", "2shafuku",
    "2wakutan", "2wakufuku", "wide",
)
ORDER_7_CARS = ("3rentan", "2shatan", "3renpuku", "2shafuku", "wide")
ORDER_9_CARS = VALID_MARKETS
ORDERED = {"3rentan", "2shatan", "2wakutan"}
UNORDERED = set(VALID_MARKETS) - ORDERED
SIZE_BY_MARKET = {"3rentan": 3, "2shatan": 2, "3renpuku": 3,
                  "2shafuku": 2, "2wakutan": 2, "2wakufuku": 2, "wide": 2}
FRAME_MARKETS = {"2wakutan", "2wakufuku"}
# Accept ranked visible text such as 1 5-7-1 4.3 or 1 5=7=1 4.3.
# Whitespace can be multiple lines in browser-generated innerText.
RANK_ROW = re.compile(
    r"(?<!\S)(\d{1,3})\s+([1-9](?:[-=][1-9]){1,2})\s+"
    r"(\d{1,5}(?:\.\d{1,2})?)(?:\s*[-〜~]\s*(\d{1,5}(?:\.\d{1,2})?))?(?!\S)"
)
POPULARITY = re.compile(r"(?:^|\n)\s*人気順\s*(?:\n|$)")
TERMINATORS = ("高配当順", "人気順", "払戻金", "レース結果")
ODDS_TIME = re.compile(r"(?<!\d)(20\d\d)/(\d\d)/(\d\d)\s+(\d\d):(\d\d)現在")


def _canonical_combo(code, combination, cars):
    parts = re.split(r"[-=]", combination)
    if len(parts) != SIZE_BY_MARKET[code]:
        raise ValueError("unexpected ticket length")
    if len(set(parts)) != len(parts):
        # Same-frame 4-4, 5-5, 6-6 valid for frame tickets only.
        if code not in FRAME_MARKETS:
            raise ValueError("repeated rider")
        if len(parts) != 2 or parts[0] != parts[1]:
            raise ValueError("invalid repeated frame")
    values = tuple(map(int, parts))
    limit = 6 if code in FRAME_MARKETS else cars
    if any(v < 1 or v > limit for v in values):
        raise ValueError("car or frame outside race")
    if code in UNORDERED:
        values = tuple(sorted(values))
    return "-".join(map(str, values))


def _expected_count(code, cars):
    if code == "3rentan":
        return cars * (cars - 1) * (cars - 2)
    if code == "3renpuku":
        return cars * (cars - 1) * (cars - 2) // 6
    if code == "2shatan":
        return cars * (cars - 1)
    if code in ("2shafuku", "wide"):
        return cars * (cars - 1) // 2
    if cars not in (8, 9):
        return None
    # 8 riders: frames 1,2,3,4,5(2 cars),6(2 cars) => 32/17.
    # 9 riders: 1,2,3,4(2),5(2),6(2) => 33/18.
    same_frame = 2 if cars == 8 else 3
    return 30 + same_frame if code == "2wakutan" else 15 + same_frame


def _ranked_blocks(text):
    """Find page '人気順' sections, ignoring unstructured tables."""
    occurrences = list(POPULARITY.finditer(text))
    blocks = []
    for item in occurrences:
        stop = len(text)
        for marker in TERMINATORS:
            i = text.find(marker, item.end())
            if i >= 0:
                stop = min(stop, i)
        region = text[item.end():stop]
        ranked = list(RANK_ROW.finditer(region))
        if not ranked or int(ranked[0].group(1)) != 1:
            continue
        rows = []
        for match in ranked:
            rank, combo, lo, hi = match.groups()
            rows.append((int(rank), combo, float(lo),
                         float(hi) if hi is not None else None))
        # Missed rows, extra page text, duplicated rank etc. => no section.
        if [rank for rank, *_ in rows] != list(range(1, len(rows) + 1)):
            continue
        blocks.append(rows)
    return blocks


def parse_ranked_odds(rendered_text, cars, market_order=None):
    """Return only reliably assigned markets, never infer missing prices.

    The operator-specific rank-list order is intentionally explicit.
    A source layout change results in NO records, not mislabeled tickets.
    """
    if cars not in (7, 8, 9):
        raise ValueError("layout requires 7, 8 or 9 cars")
    if not isinstance(rendered_text, str):
        raise ValueError("rendered page text required")
    order = tuple(market_order or (ORDER_7_CARS if cars == 7 else ORDER_9_CARS))
    if not order or any(x not in VALID_MARKETS for x in order) or len(set(order)) != len(order):
        raise ValueError("invalid ordered market declaration")
    if cars == 7 and any(x in FRAME_MARKETS for x in order):
        raise ValueError("frame tickets unavailable for 7-car parser")
    blocks = _ranked_blocks(rendered_text)
    if len(blocks) != len(order):
        return {"status": "UNRECOGNISED_LAYOUT", "ranked_blocks": len(blocks),
                "expected_blocks": len(order), "markets": {},
                "detail": "Market order cannot be verified; nothing assigned"}
    output = {}
    for code, rows in zip(order, blocks):
        expected = _expected_count(code, cars)
        if expected is None or len(rows) > expected:
            return {"status": "UNRECOGNISED_LAYOUT", "markets": {},
                    "detail": "Unexpected number of ticket rows"}
        table = {}
        try:
            for _, raw, lower, upper in rows:
                combo = _canonical_combo(code, raw, cars)
                # Two- and three-car unordered markets must be input sorted or
                # expressed with '=', not a mixed separator.
                expected_separator = "-" if code in ORDERED else "="
                if expected_separator not in raw:
                    raise ValueError("wrong market separator")
                if not lower >= 1.0 or (upper is not None and upper < lower):
                    raise ValueError("invalid odds")
                if upper is not None and code != "wide":
                    raise ValueError("unexpected ranged odds")
                if combo in table:
                    raise ValueError("duplicate ticket")
                table[combo] = {"min": lower, "max": upper} if upper is not None else lower
        except ValueError:
            return {"status": "UNRECOGNISED_LAYOUT", "markets": {},
                    "detail": "Ticket pattern not consistent with market"}
        output[code] = {"quotes": table, "count": len(table),
                        "expected_count": expected,
                        "complete": len(table) == expected}
    timestamps = sorted(set("-".join(m.group(i) for i in (1, 2, 3)) + "T" +
                            ":".join(m.group(i) for i in (4, 5)) + ":00+09:00"
                            for m in ODDS_TIME.finditer(rendered_text)))
    return {"status": "PARSED_RANKED_MARKETS", "markets": output,
            "page_displayed_times_jst": timestamps,
            "note": "Only ranked odds seen in page. Incomplete is not complete."}
