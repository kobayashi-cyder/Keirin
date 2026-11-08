"""No-network regression tests for explicit OpenAI -> Discord connection."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import discord_openai_status as mod


def sample_report():
    return {
        "status": "NOT_READY_NO_PROBABILITIES",
        "race_id": "7520261108010001",
        "model_verified": False,
        "alert_allowed": False,
        "bet_allowed": False,
        "market_coverage": {
            "3rentan": {"observed": 210, "possible": 210,
                        "structural_coverage_complete": True},
            "2shafuku": {"observed": 21, "possible": 21,
                         "structural_coverage_complete": True},
        },
        "candidates": [{"market": "3rentan", "combo": "1-2-3",
                        "stressed_return": 9999.0}],
        "secret_marker": "NEVER_POST_ME",
    }


class FakeResponse:
    def __init__(self, *, status=204, payload=None):
        self.status = status
        self.payload = payload

    def read(self, *args):
        return json.dumps(self.payload or {}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        self.timeout = timeout
        return self.response


class DiscordOpenAITests(unittest.TestCase):
    def test_test_mode_is_not_prediction(self):
        m = mod.status_message()
        self.assertIn("接続テスト", m)
        self.assertNotIn("1-2-3", m)

    def test_report_only_safe_counts_no_odds_or_picks(self):
        m = mod.status_message(sample_report())
        self.assertIn("3連単：210/210", m)
        self.assertIn("モデル：実データによる検証未完了", m)
        self.assertNotIn("1-2-3", m)
        self.assertNotIn("9999", m)
        self.assertNotIn("NEVER_POST_ME", m)
        self.assertNotIn("期待回収率130%", m)

    def test_reject_unverified_alert_permission(self):
        d = sample_report()
        d["alert_allowed"] = True
        with self.assertRaises(ValueError):
            mod.status_message(d)

    def test_reject_arbitrary_status_and_unsafe_market(self):
        d = sample_report()
        d["status"] = "SHADOW_READY_REAL_BETS"
        with self.assertRaises(ValueError):
            mod.status_message(d)
        d = sample_report()
        d["market_coverage"]["@everyone"] = {"observed": 1, "possible": 1,
                                             "structural_coverage_complete": True}
        with self.assertRaises(ValueError):
            mod.status_message(d)

    def test_webhook_url_must_be_discord_https_no_redirect(self):
        url = "https://discord.com/api/webhooks/123456/TEST_abc-xyz"
        self.assertEqual(mod.validate_webhook(url), url)
        for bad in (
            "http://discord.com/api/webhooks/1/token",
            "https://discord.com.evil.test/api/webhooks/1/token",
            "https://evil.test/api/webhooks/123456/TEST_abc-xyz",
            "https://discord.com/api/webhooks/123456/token?wait=1",
            "https://discord.com@evil.test/api/webhooks/123456/token",
        ):
            with self.assertRaises(ValueError):
                mod.validate_webhook(bad)

    def test_openai_uses_responses_store_false_limited_input(self):
        result = {"status": "completed", "output": [
            {"type": "message", "content": [
                {"type": "output_text", "text": "接続状況を確認しました。"}
            ]}
        ]}
        opener = FakeOpener(FakeResponse(status=200, payload=result))
        note, status = mod.openai_note("research_data_status", key="fake-key", opener=opener)
        self.assertEqual(status, "OK")
        self.assertEqual(note, "接続状況を確認しました。")
        req = opener.requests[0]
        self.assertEqual(req.full_url, mod.OPENAI_ENDPOINT)
        self.assertEqual(json.loads(req.data)["store"], False)
        self.assertEqual(json.loads(req.data)["input"],
                         '{"kind": "research_data_status", "prediction_model_verified": false}')
        self.assertNotIn("7520261108010001", req.data.decode())

    def test_openai_rejects_wager_claim_or_prices(self):
        for phrase in ("予想は必ず当たる", "今すぐ購入", "買い目は三連単です。", "回収率が高いです", "1着です"):
            result = {"status": "completed", "output": [
                {"type": "message", "content": [{"type": "output_text", "text": phrase}]}
            ]}
            note, flag = mod.openai_note(
                "connection_test", key="test",
                opener=FakeOpener(FakeResponse(status=200, payload=result))
            )
            if phrase == "買い目は三連単です。":
                # No ticket or numeric prediction is present in this phrase
                # but policy still rejects the word '買い目' outright.
                self.assertEqual(flag, "REJECTED_OUTPUT")
            else:
                self.assertEqual(flag, "REJECTED_OUTPUT")
            self.assertIsNone(note)

    def test_discord_post_only_sends_fixed_text_and_mentions_disabled(self):
        opener = FakeOpener(FakeResponse())
        status = mod.send_discord(
            mod.status_message(sample_report()),
            webhook="https://discord.com/api/webhooks/123456/TEST_abc-xyz",
            opener=opener,
        )
        self.assertEqual(status, "SENT")
        data = json.loads(opener.requests[0].data)
        self.assertEqual(data["allowed_mentions"], {"parse": []})
        self.assertNotIn("9999", data["content"])

    def test_rate_limit_no_retry_and_no_token_in_error(self):
        from urllib.error import HTTPError
        class RateLimited(FakeOpener):
            def open(self, request, timeout):
                self.requests.append(request)
                raise HTTPError(request.full_url, 429, "Too Many", {}, None)
        opener = RateLimited(FakeResponse())
        result = mod.send_discord(
            "test", webhook="https://discord.com/api/webhooks/123456/secret_token",
            opener=opener,
        )
        self.assertEqual(result, "RATE_LIMITED")
        self.assertEqual(len(opener.requests), 1)
        self.assertNotIn("secret_token", result)

    def test_default_cli_dry_run_no_external_calls(self):
        buf = io.StringIO()
        with (patch.object(sys, "argv", ["tool", "--test"]),
              patch("discord_openai_status.openai_note") as api,
              patch("discord_openai_status.send_discord") as discord,
              patch("sys.stdout", buf)):
            self.assertEqual(mod.main(), 0)
        api.assert_not_called()
        discord.assert_not_called()
        self.assertIn("DRY_RUN", buf.getvalue())

    def test_report_path_confined_to_local_data(self):
        with self.assertRaises(ValueError):
            mod.load_report(Path("secrets.json"))
        with self.assertRaises(ValueError):
            mod.load_report(Path("local_data/../secrets.json"))


if __name__ == "__main__":
    unittest.main()
