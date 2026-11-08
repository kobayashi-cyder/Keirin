"""Strict, offline tests for official YouTube race-video discovery and reviewed links."""
import csv
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import youtube_race_search as yt

RACE = "7520261108010001"
ID1 = "AbCd1234_-X"
ID2 = "ZyxW9876_-Q"


class FakeResponse:
    status = 200
    def __init__(self, data):
        self.data = json.dumps(data).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def read(self, count):
        return self.data[:count]


class YouTubeSearchTests(unittest.TestCase):
    def test_query_uses_exact_event_without_guessing_results(self):
        q = yt.query_for_event("2026-11-08", "松山", 1)
        self.assertIn("2026年11月8日", q)
        self.assertIn("松山", q)
        self.assertIn("1R", q)
        url = yt.browser_search_url(q)
        parts = urlsplit(url)
        self.assertEqual(parts.hostname, "www.youtube.com")
        self.assertEqual(parse_qs(parts.query)["search_query"], [q])

    def test_bad_date_course_and_race_rejected(self):
        for date in ("2026-13-08", "2026-1-8", "2026-02-30"):
            with self.assertRaises(ValueError):
                yt.query_for_event(date, "松山", 1)
        for venue in ("", "../secret", "<script>", "a"*26):
            with self.assertRaises(ValueError):
                yt.query_for_event("2026-11-08", venue, 1)
        for number in (0, 13, -1, 1.0):
            with self.assertRaises(ValueError):
                yt.query_for_event("2026-11-08", "松山", number)

    def test_official_api_one_request_never_video_download(self):
        calls = []
        obj = {
            "items": [
                {
                    "id": {"kind": "youtube#video", "videoId": ID1},
                    "snippet": {
                        "title": "松山競輪 &amp; 1R", "channelTitle": "Official source",
                        "publishedAt": "2026-11-08T11:30:00Z"
                    },
                },
                {
                    "id": {"kind": "youtube#video", "videoId": ID1},
                    "snippet": {"title": "Duplicate"}
                },
                {"id": {"kind": "youtube#playlist", "playlistId": "playlist"},
                 "snippet": {"title": "Ignore playlist"}},
                {"id": {"kind": "youtube#video", "videoId": ID2},
                 "snippet": {"title": "別のレース"}},
            ]
        }
        def opener(request, timeout):
            calls.append((request, timeout))
            return FakeResponse(obj)
        hits = yt.search_api("競輪 2026年11月8日 松山 1R", "AIzaFakeTEST_SECRET_123456789",
                             opener=opener, max_results=10)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 15)
        req = calls[0][0]
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(urlsplit(req.full_url).hostname, "www.googleapis.com")
        opts = parse_qs(urlsplit(req.full_url).query)
        self.assertEqual(opts["part"], ["snippet"])
        self.assertEqual(opts["type"], ["video"])
        self.assertEqual(opts["videoEmbeddable"], ["true"])
        self.assertEqual(opts["maxResults"], ["10"])
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]["title"], "松山競輪 & 1R")
        self.assertEqual(hits[0]["video_id"], ID1)
        self.assertTrue(hits[0]["url"].endswith(ID1))
        self.assertFalse(hits[0]["training_ready"])
        self.assertFalse(hits[0]["video_content_downloaded"])
        self.assertEqual(hits[0]["status"], "UNVERIFIED_SEARCH_CANDIDATE")

    def test_api_error_sanitizes_key_and_request_url(self):
        private_key = "AIzaFAKEPRIVATEKEY12345678901234567890"
        def boom(req, timeout):
            raise HTTPError(req.full_url, 403, "Permission", {}, None)
        with self.assertRaises(ValueError) as ctx:
            yt.search_api("競輪 松山 1R", private_key, opener=boom)
        self.assertEqual(str(ctx.exception), "YouTube Data API HTTP 403; check API key/quota")
        self.assertNotIn(private_key, str(ctx.exception))
        self.assertNotIn("?", str(ctx.exception))

    def test_empty_api_candidates_are_not_claimed_as_training(self):
        items = yt.search_api("keirin", "fake", opener=lambda *a, **k: FakeResponse({"items": []}))
        self.assertEqual(items, [])
        with self.assertRaises(ValueError):
            yt.search_api("q", "fake", max_results=100,
                          opener=lambda *a, **k: self.fail("must not call network"))

    def test_api_key_read_from_local_env_without_echo(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".env"
            p.write_text('YOUTUBE_API_KEY="AIzaLocalFakeKey_1234567890"\n', encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(yt.youtube_api_key(p), "AIzaLocalFakeKey_1234567890")
            with patch.dict(os.environ, {"YOUTUBE_API_KEY": "AIzaEnvFakeKey_1234567890"}):
                self.assertEqual(yt.youtube_api_key(p), "AIzaEnvFakeKey_1234567890")
            with patch.dict(os.environ, {}, clear=True):
                p.write_text("OPENAI_API_KEY=sk-do-not-log\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "YOUTUBE_API_KEY not configured"):
                    yt.youtube_api_key(p)

    def test_manual_review_is_required_and_link_only_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = os.getcwd()
            os.chdir(tmp)
            try:
                target = yt.DEFAULT_INDEX
                details = dict(
                    race_id=RACE, race_date="2026-11-08", venue="松山",
                    race_number=1, video_id=ID1,
                    now=datetime.fromisoformat("2026-11-08T21:00:00+09:00"),
                )
                with self.assertRaisesRegex(ValueError, "confirm"):
                    yt.register_reviewed_link(target, **details)
                self.assertFalse(target.exists())
                result = yt.register_reviewed_link(target, confirmed=True, **details)
                self.assertEqual(result["status"], "LINK_ONLY_REVIEWED_NOT_TRAINED")
                self.assertFalse(result["video_downloaded"])
                self.assertFalse(result["video_features_extracted"])
                self.assertFalse(result["training_allowed_by_link"])
                with target.open(encoding="utf-8", newline="") as f:
                    rows = list(csv.DictReader(f))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["video_id"], ID1)
                self.assertEqual(rows[0]["download_permission"], "NOT_ESTABLISHED")
                self.assertNotIn("title", rows[0])
                with self.assertRaisesRegex(ValueError, "already linked"):
                    yt.register_reviewed_link(target, confirmed=True, **details)
            finally:
                os.chdir(before)

    def test_bad_video_id_race_id_and_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = os.getcwd()
            os.chdir(tmp)
            try:
                default = dict(
                    race_id=RACE, race_date="2026-11-08", venue="松山",
                    race_number=1, video_id=ID1, confirmed=True,
                )
                for value in ("no", "user?x", "../secret"):
                    with self.assertRaises(ValueError):
                        yt.register_reviewed_link(
                            Path("local_data/history/links.csv"), **{**default, "video_id": value}
                        )
                with self.assertRaises(ValueError):
                    yt.register_reviewed_link(
                        Path("local_data/history/links.csv"), **{**default, "race_id": "abc"}
                    )
                for unsafe in (".env", "../.env", "/tmp/x.csv",
                               "local_data/../.env", "local_data/readme.txt"):
                    with self.assertRaises(ValueError):
                        yt.local_index(unsafe)
            finally:
                os.chdir(before)

    def test_cli_without_api_key_never_network_calls(self):
        argv = ["youtube_race_search.py", "--race-date", "2026-11-08",
                "--venue", "松山", "--race-number", "1"]
        with patch.object(sys, "argv", argv):
            with patch.object(yt, "search_api", side_effect=AssertionError("must not access API")):
                with patch("sys.stdout", new_callable=io.StringIO) as stream:
                    self.assertEqual(yt.main(), 0)
        output = json.loads(stream.getvalue())
        self.assertEqual(output["status"], "SEARCH_LINK_ONLY_NO_API_REQUEST")
        self.assertIn("youtube.com/results", output["browser_search_url"])
        self.assertFalse(output["model_trained"])
        self.assertEqual(output["videos_downloaded"], 0)

    def test_manual_registered_video_link_not_equal_video_training(self):
        self.assertNotIn("video_sha256", yt.HEADER)
        self.assertNotIn("attack_count", yt.HEADER)
        self.assertNotIn("front_seconds", yt.HEADER)


if __name__ == "__main__":
    unittest.main()
