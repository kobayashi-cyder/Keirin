"""Offline regression tests for odds HTML / multiline market ranking layouts."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from odds_html_parser import text_from_html
from odds_rank_parser import parse_ranked_odds


def sample_html():
    blocks = [
        [("1-2-6", "5.8"), ("1-2-3", "7.8")],
        [("1-2", "1.2"), ("2-1", "8.8")],
        [("1=2=6", "2.7"), ("1=2=3", "3.3")],
        [("1=2", "1.0"), ("1=3", "8.2")],
        [("1=2", "1.0～1.1"), ("1=3", "1.2～2.5")],
    ]
    sections = []
    for index, lines in enumerate(blocks):
        rows = []
        for rank, (ticket, odds) in enumerate(lines, 1):
            # A realistic HTML table separates rank, ticket and odds tags.
            # A rank-only first cell followed by a ticket-only second cell
            # produces multiple newlines in the converted page text.
            rows.append(
                f'<tr class="ranking"><td><span>{rank}</span></td>'
                f'<td><a>{ticket}</a></td><td><em>{odds}</em></td></tr>'
            )
        sections.append(
            f'<section><h3>人気順</h3><table>{"".join(rows)}</table>'
            '<h3>高配当順</h3></section>'
        )
    return (
        '<!doctype html><html lang="ja"><head><title>Keirin</title>'
        '<script>const malicious = "人気順";</script></head><body>'
        '<div>オッズ 3連単 2車単 3連複 2車複 ワイド</div>'
        + "".join(sections)
        + '</body></html>'
    )


class HtmlOddsTests(unittest.TestCase):
    def test_structured_html_reads_all_five_7car_markets(self):
        result = parse_ranked_odds(text_from_html(sample_html()), 7)
        self.assertEqual(result["status"], "PARSED_RANKED_MARKETS")
        self.assertEqual(set(result["markets"]), {
            "3rentan", "2shatan", "3renpuku", "2shafuku", "wide"
        })
        self.assertEqual(result["markets"]["3rentan"]["quotes"]["1-2-6"], 5.8)
        self.assertEqual(result["markets"]["wide"]["quotes"]["1-2"],
                         {"min": 1.0, "max": 1.1})
        self.assertFalse(result["markets"]["3rentan"]["complete"])

    def test_multiline_rank_combo_odds(self):
        parts = [
            "人気順", "1", "1-2-6", "5.8", "2", "1-2-3", "7.8", "高配当順",
            "人気順", "1", "1-2", "1.2", "2", "2-1", "8.8", "高配当順",
            "人気順", "1", "1=2=6", "2.7", "2", "1=2=3", "3.3", "高配当順",
            "人気順", "1", "1=2", "1.0", "2", "1=3", "8.2", "高配当順",
            "人気順", "1", "1=2", "1.0～1.1", "2", "1=3", "1.2～2.5",
        ]
        result = parse_ranked_odds("\n".join(parts), 7)
        self.assertEqual(result["status"], "PARSED_RANKED_MARKETS")
        self.assertEqual(result["markets"]["2shatan"]["quotes"]["2-1"], 8.8)

    def test_header_and_script_not_considered_odds(self):
        text = text_from_html(
            '<html><head><title>人気順</title></head>'
            '<body><script>人気順 1 1-2-3 9.0</script><div>レース</div>'
            '</body></html>'
        )
        self.assertNotIn("人気順", text)
        self.assertEqual(parse_ranked_odds(text, 7)["status"], "UNRECOGNISED_LAYOUT")

    def test_doubled_html_markets_fail_closed(self):
        html = sample_html()
        doubled = html.replace("</body>", sample_html().split("<body>")[1].split("</body>")[0] + "</body>")
        result = parse_ranked_odds(text_from_html(doubled), 7)
        self.assertEqual(result["status"], "UNRECOGNISED_LAYOUT")
        self.assertEqual(result["markets"], {})


if __name__ == "__main__":
    unittest.main()
