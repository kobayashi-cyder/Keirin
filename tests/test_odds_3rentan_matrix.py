"""Offline full-table regression: no site access, no model, no betting."""
import itertools
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from odds_3rentan_matrix import recover_three_rentan_matrix


def fixture(cars=7, *, missing_leader=None, sabotage=None):
    """Construct tables with the site's 2nd-place columns / 3rd-place rows."""
    combos = {}
    tables = []
    for a in range(1, cars+1):
        if a == missing_leader:
            continue
        other = [c for c in range(1, cars+1) if c != a]
        header = "<tr><th></th>" + "".join(f"<th>{b}</th>" for b in other) + "</tr>"
        rows = []
        for c in other:
            cells = [f"<th>{c}</th>"]
            for b in other:
                if b == c:
                    cells.append("<td></td>")
                else:
                    odds = round(1 + a * 100 + b * 10 + c * .1, 1)
                    combos[f"{a}-{b}-{c}"] = odds
                    cells.append(f"<td><span>{odds}</span></td>")
            cells.append(f"<th>{c}</th>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        table = "<table class='3rentan_matrix'>" + header + "".join(rows) + "</table>"
        tables.append(table)
    html = "<!doctype html><html><body><div>1着の選択：</div>" + "".join(tables) + "</body></html>"
    if sabotage:
        html = html.replace(*sabotage)
    return html, combos


class MatrixTests(unittest.TestCase):
    def test_complete_seven_cars_with_ranked_crosscheck(self):
        html, prices = fixture(7)
        sample = dict(list(prices.items())[:50])
        actual, status = recover_three_rentan_matrix(html, 7, sample)
        self.assertEqual(status, "crosschecked_full_matrix")
        self.assertEqual(actual, prices)
        self.assertEqual(len(actual), 210)
        self.assertEqual(actual["1-2-6"], prices["1-2-6"])

    def test_complete_nine_cars(self):
        html, prices = fixture(9)
        sample = dict(list(prices.items())[:50])
        actual, status = recover_three_rentan_matrix(html, 9, sample)
        self.assertEqual(status, "crosschecked_full_matrix")
        self.assertEqual(len(actual), 504)

    def test_missing_leader_fail_closed(self):
        html, prices = fixture(7, missing_leader=7)
        actual, status = recover_three_rentan_matrix(html, 7, dict(list(prices.items())[:50]))
        self.assertIsNone(actual)
        self.assertEqual(status, "missing_or_conflicting_matrix_for_leader_7")

    def test_price_conflict_with_ranked_top50_fail_closed(self):
        html, prices = fixture(7)
        top = dict(list(prices.items())[:50])
        top["1-2-3"] += 0.3
        actual, status = recover_three_rentan_matrix(html, 7, top)
        self.assertIsNone(actual)
        self.assertEqual(status, "ranked_quote_mismatch_for_1-2-3")

    def test_missing_diagonal_must_be_blank(self):
        html, prices = fixture(7)
        html = html.replace("<td></td>", "<td>9.9</td>", 1)
        actual, status = recover_three_rentan_matrix(html, 7, dict(list(prices.items())[:50]))
        self.assertIsNone(actual)

    def test_competing_tables_fail_closed(self):
        html, prices = fixture(7)
        html = html.replace("</body>", "<table><tr><th>2</th><th>3</th></tr></table></body>")
        actual, status = recover_three_rentan_matrix(html, 7, dict(list(prices.items())[:50]))
        self.assertEqual(status, "crosschecked_full_matrix")
        self.assertEqual(len(actual), 210)
        # a second *conflicting* valid matrix for leader 1 must be rejected
        html2, _ = fixture(7, sabotage=("102.3", "112.3"))
        # Add only the damaged leader 1 table, not full duplicate set
        leader1 = html2.split("<table class='3rentan_matrix'>")[1].split("</table>")[0]
        html_conflict = html.replace("</body>", "<table class='3rentan_matrix'>" + leader1 + "</table></body>")
        result, reason = recover_three_rentan_matrix(
            html_conflict, 7, dict(list(prices.items())[:50])
        )
        self.assertIsNone(result)
        self.assertEqual(reason, "missing_or_conflicting_matrix_for_leader_1")

    def test_no_crosscheck_means_not_verified(self):
        html, _ = fixture(7)
        actual, status = recover_three_rentan_matrix(html, 7, {})
        self.assertIsNone(actual)
        self.assertEqual(status, "unsupported_or_missing_ranked_crosscheck")


if __name__ == "__main__":
    unittest.main()
