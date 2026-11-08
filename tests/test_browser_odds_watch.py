"""Offline-only layout and rate safeguards, no network or browser launch."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from browser_odds_watch import (
    StopMonitoring, checked_interval, ensure_not_closed, jst_timestamp,
    monitor, record_if_changed, validate_target, classify_robots_body,
)
from odds_rank_parser import parse_ranked_odds


TEXT_7 = """オッズ
3連単 2車単 3連複 2車複 ワイド
2026/11/08 12:10現在
人気順
1 5-7-1 4.3
2 7-5-1 8.9
高配当順
人気順
1 5-7 2.1
2 7-5 6.2
高配当順
人気順
1 1=5=7 2.3
2 4=5=7 5.8
高配当順
人気順
1 5=7 1.6
2 4=5 3.0
高配当順
人気順
1 5=7 1.3〜1.4
2 1=5 2.0〜2.9
高配当順
"""


def build_9():
    # Demonstrates all seven market signatures without copying a real page.
    blocks = [
        [("1-2-3", 7), ("2-1-3", 11)],      # 3rentan
        [("1-2", 3.7), ("2-1", 7)],        # 2shatan
        [("1=2=3", 2.2), ("2=3=4", 9)],  # 3renpuku
        [("1=2", 1.7), ("2=3", 5)],       # 2shafuku
        [("4-4", 13.5), ("1-2", 8)],     # 2wakutan
        [("4=4", 8), ("1=2", 6)],        # 2wakufuku
        [("1=2", 1.4), ("1=3", 4)],      # wide
    ]
    return "\n".join("人気順\n" + "\n".join(
        f"{index} {combo} {odds}" for index, (combo, odds) in enumerate(b, 1)
    ) + "\n高配当順" for b in blocks)


class OddsParserTests(unittest.TestCase):
    def test_seven_market_layout_reads_partial_market_without_guessing(self):
        result = parse_ranked_odds(TEXT_7, 7)
        self.assertEqual(result["status"], "PARSED_RANKED_MARKETS")
        self.assertEqual(result["markets"]["3rentan"]["count"], 2)
        self.assertFalse(result["markets"]["3rentan"]["complete"])
        self.assertEqual(result["markets"]["3rentan"]["expected_count"], 210)
        self.assertEqual(result["markets"]["2shatan"]["quotes"]["5-7"], 2.1)
        self.assertEqual(result["markets"]["3renpuku"]["quotes"]["1-5-7"], 2.3)
        self.assertEqual(result["markets"]["wide"]["quotes"]["5-7"],
                         {"min": 1.3, "max": 1.4})
        self.assertEqual(result["page_displayed_times_jst"], ["2026-11-08T12:10:00+09:00"])

    def test_kdreams_fullwidth_wave_range_is_not_dropped(self):
        # The live public page displays wide ranges using U+FF5E (～),
        # whereas the original sample used U+301C (〜).
        sample = TEXT_7.replace("1.3〜1.4", "1.3～1.4")
        sample = sample.replace("2.0〜2.9", "2.0～2.9")
        sample = sample.replace("人気順\n", "* 人気順\n")
        sample = sample.replace("1 5=7 1.3～1.4", "1 | 5=7 1.3～1.4")
        result = parse_ranked_odds(sample, 7)
        self.assertEqual(result["status"], "PARSED_RANKED_MARKETS")
        self.assertEqual(result["markets"]["wide"]["quotes"]["5-7"],
                         {"min": 1.3, "max": 1.4})

    def test_html_table_pipe_ranked_rows(self):
        html_text = TEXT_7.replace("1 5-7-1 4.3", "1 | 5-7-1 4.3")
        html_text = html_text.replace("1 5-7 2.1", "1 | 5-7 2.1")
        result = parse_ranked_odds(html_text, 7)
        self.assertEqual(result["status"], "PARSED_RANKED_MARKETS")
        self.assertEqual(result["markets"]["3rentan"]["quotes"]["5-7-1"], 4.3)

    def test_nine_cars_and_same_frame(self):
        result = parse_ranked_odds(build_9(), 9)
        self.assertEqual(result["status"], "PARSED_RANKED_MARKETS")
        self.assertEqual(result["markets"]["2wakutan"]["quotes"]["4-4"], 13.5)
        self.assertEqual(result["markets"]["2wakufuku"]["quotes"]["4-4"], 8)
        self.assertEqual(len(result["markets"]), 7)

    def test_eight_car_frame_count(self):
        result = parse_ranked_odds(build_9(), 8)
        self.assertEqual(result["status"], "PARSED_RANKED_MARKETS")
        self.assertEqual(result["markets"]["2wakutan"]["expected_count"], 32)
        self.assertEqual(result["markets"]["2wakufuku"]["expected_count"], 17)

    def test_missing_or_changed_layout_does_not_label_wrong_market(self):
        result = parse_ranked_odds(TEXT_7.replace("2 7-5 6.2", "2 7=5 6.2"), 7)
        self.assertEqual(result["status"], "UNRECOGNISED_LAYOUT")
        self.assertEqual(result["markets"], {})
        result = parse_ranked_odds("オッズが確認できませんでした", 7)
        self.assertEqual(result["status"], "UNRECOGNISED_LAYOUT")
        self.assertEqual(result["markets"], {})

    def test_rank_not_contiguous_is_rejected(self):
        result = parse_ranked_odds(TEXT_7.replace("2 7-5-1 8.9", "3 7-5-1 8.9"), 7)
        self.assertEqual(result["status"], "UNRECOGNISED_LAYOUT")

    def test_safety_minimum_10_seconds_and_robots_slower(self):
        self.assertEqual(checked_interval(10, 15), 15)
        self.assertEqual(checked_interval(10), 10)
        for value in (0.1, 5, -1, 999):
            with self.subTest(value=value), self.assertRaises(StopMonitoring):
                checked_interval(value)

    def test_html_robots_is_not_fake_allow_rule(self):
        html = (b'<!doctype html><html lang="ja"><head></head>'
                b'<body>Keirin schedule</body></html>')
        result = classify_robots_body(
            html, "text/html; charset=UTF-8",
            "https://keirin.kdreams.jp/robots.txt",
            "https://keirin.kdreams.jp/yokkaichi/racedetail/4820261105040006/",
            "KeirinResearchMonitor/0.2")
        self.assertEqual(result["robots"], "not_robots_html")
        self.assertIsNone(result["crawl_delay"])

    def test_real_disallow_enforced_even_with_html_content_type(self):
        robots = b"User-agent: *\nDisallow: /yokkaichi/\n"
        with self.assertRaisesRegex(StopMonitoring, "disallows"):
            classify_robots_body(
                robots, "text/html",
                "https://keirin.kdreams.jp/robots.txt",
                "https://keirin.kdreams.jp/yokkaichi/racedetail/4820261105040006/",
                "KeirinResearchMonitor/0.2")

    def test_real_crawl_delay_honoured(self):
        robots = b"User-agent: *\nCrawl-delay: 25\nAllow: /\n"
        result = classify_robots_body(
            robots, "text/plain",
            "https://keirin.kdreams.jp/robots.txt",
            "https://keirin.kdreams.jp/yokkaichi/racedetail/4820261105040006/",
            "KeirinResearchMonitor/0.2")
        self.assertEqual(result["robots"], "allowed_by_robots_only")
        self.assertEqual(checked_interval(10, result["crawl_delay"]), 25)

    def test_only_explicit_public_race_url(self):
        good = ("https://keirin.kdreams.jp/kumamoto/racedetail/"
                "8720261106030004/?pageType=odds")
        self.assertEqual(validate_target(good), "keirin.kdreams.jp")
        for url in (
            "http://keirin.kdreams.jp/kumamoto/racedetail/8720261106030004/",
            "https://keirin.kdreams.jp.evil.com/kumamoto/racedetail/8720261106030004/",
            "https://keirin.kdreams.jp/account/",
            "https://keirin.kdreams.jp/kumamoto/racedetail/8720261106030004/?pageType=result",
        ):
            with self.subTest(url=url), self.assertRaises(StopMonitoring):
                validate_target(url)

    def test_acknowledgment_prevents_running_browser_or_network(self):
        deadline = datetime.now(timezone.utc) + timedelta(minutes=15)
        url = "https://keirin.kdreams.jp/kumamoto/racedetail/8720261106030004/"
        with self.assertRaisesRegex(StopMonitoring, "acknowledge"):
            monitor(url, deadline, risk_acknowledged=False,
                    robots_checker=lambda _: (_ for _ in ()).throw(
                        AssertionError("Never called")
                    ))

    def test_expired_deadline_stop(self):
        d = jst_timestamp("2026-11-08T12:20:00+09:00")
        with self.assertRaisesRegex(StopMonitoring, "deadline"):
            ensure_not_closed(d, now=d + timedelta(seconds=1))

    def test_local_output_only_and_digest_dedupe(self):
        with tempfile.TemporaryDirectory() as root:
            import os
            current = Path.cwd()
            try:
                os.chdir(root)
                path = Path("local_data/odds_snapshots/test.jsonl")
                record = {"markets": {"2shatan": {"quotes": {"1-2": 3.5}}},
                          "observed_at_jst": "2026-11-08T12:00:00+09:00"}
                digest, yes = record_if_changed(path, record, None)
                self.assertTrue(yes)
                _, no = record_if_changed(path, record, digest)
                self.assertFalse(no)
                self.assertEqual(len(path.read_text().splitlines()), 1)
                with self.assertRaises(StopMonitoring):
                    record_if_changed(Path("paper_trading/alerts/feed.json"), record, None)
            finally:
                os.chdir(current)


if __name__ == "__main__":
    unittest.main()
