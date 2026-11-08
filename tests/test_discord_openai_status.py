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
        self.assertEqual(json.loads(req.data)["model"], "gpt-4o")
        self.assertNotIn("reasoning", json.loads(req.data))
        self.assertEqual(json.loads(req.data)["input"],
                         '{"kind": "research_data_status", "prediction_model_verified": false}')
        self.assertNotIn("7520261108010001", req.data.decode())

    def test_luna_opt_in_keeps_supported_reasoning_option(self):
        sample = {"status": "completed", "output": [
            {"type": "message", "content": [
                {"type": "output_text", "text": "接続状態を確認しました。"}
            ]}
        ]}
        op = FakeOpener(FakeResponse(status=200, payload=sample))
        note, flag = mod.openai_note(
            "connection_test", model="gpt-6-luna", key="fake", opener=op
        )
        self.assertEqual(flag, "OK")
        data = json.loads(op.requests[0].data)
        self.assertEqual(data["model"], "gpt-6-luna")
        self.assertEqual(data["reasoning"], {"effort": "none"})

    def test_list_available_models_does_not_post_discord_or_call_responses(self):
        op = FakeOpener(FakeResponse(status=200, payload={
            "object": "list",
            "data": [{"id": "gpt-4o"}, {"id": "whisper-1"}],
        }))
        models, status = mod.available_models(key="fake", opener=op)
        self.assertEqual(status, "OK")
        self.assertIn("gpt-4o", models)
        self.assertNotIn("gpt-6-luna", models)
        self.assertEqual(op.requests[0].get_method(), "GET")
        self.assertEqual(op.requests[0].full_url,
                         "https://api.openai.com/v1/models")
        buf = io.StringIO()
        with (patch.object(sys, "argv", ["tool", "--list-models"]),
              patch("discord_openai_status.available_models",
                    return_value=(models, "OK")),
              patch("discord_openai_status.send_discord") as post,
              patch("discord_openai_status.openai_note") as response,
              patch("sys.stdout", buf)):
            self.assertEqual(mod.main(), 0)
        post.assert_not_called()
        response.assert_not_called()
        self.assertIn("gpt-4o: LISTED", buf.getvalue())
        self.assertIn("gpt-6-luna: NOT_LISTED", buf.getvalue())

    def test_list_models_requires_no_discord_and_cannot_be_combined_with_send(self):
        buf = io.StringIO()
        with (patch.object(sys, "argv", ["tool", "--list-models", "--send"]),
              patch("discord_openai_status.available_models") as models,
              patch("sys.stderr", buf)):
            self.assertEqual(mod.main(), 2)
        models.assert_not_called()
        self.assertIn("--list-models cannot be combined", buf.getvalue())

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

    def test_env_file_loads_only_supported_keys_and_overrides_stale_windows_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# Local secrets only\n"
                "OPENAI_API_KEY='sk-local-test-only'\n"
                "DISCORD_WEBHOOK_URL=\"https://discord.com/api/webhooks/123456/test_token\"\n"
                "OPENAI_MODEL=gpt-4o\n"
                "UNRELATED_KEY=ignored\n",
                encoding="utf-8-sig",
            )
            with patch.dict(os.environ, {"OPENAI_API_KEY": "old-invalid-key",
                                         "DISCORD_WEBHOOK_URL": "old-url"},
                            clear=True):
                self.assertEqual(mod.load_local_env(path), "LOADED")
                self.assertEqual(os.getenv("OPENAI_API_KEY"), "sk-local-test-only")
                self.assertEqual(os.getenv("DISCORD_WEBHOOK_URL"),
                                 "https://discord.com/api/webhooks/123456/test_token")
                self.assertEqual(os.getenv("OPENAI_MODEL"), "gpt-4o")
                self.assertNotIn("UNRELATED_KEY", os.environ)

    def test_env_file_does_not_dump_credentials_to_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            secret = "supersecret-test-token-123"
            path.write_text("OPENAI_API_KEY=" + secret + "\n",
                            encoding="utf-8")
            buf = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), patch("sys.stdout", buf):
                result = mod.load_local_env(path)
                print("LOCAL_ENV:", result)
            self.assertEqual(result, "LOADED")
            self.assertNotIn(secret, buf.getvalue())

    def test_invalid_env_does_not_partially_override_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "OPENAI_API_KEY=new-private-value\n"
                "DISCORD_WEBHOOK_URL=\n", encoding="utf-8"
            )
            with patch.dict(os.environ, {"OPENAI_API_KEY": "existing-key"},
                            clear=True):
                with self.assertRaisesRegex(ValueError, "DISCORD_WEBHOOK_URL is empty"):
                    mod.load_local_env(path)
                self.assertEqual(os.getenv("OPENAI_API_KEY"), "existing-key")

    def test_env_file_does_not_require_pip_or_store_on_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / ".env"
            self.assertEqual(mod.load_local_env(missing), "NOT_FOUND")
            missing.write_text("OPENAI_API_KEY=key\nOPENAI_API_KEY=second\n",
                               encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate OPENAI_API_KEY"):
                mod.load_local_env(missing)

    def test_429_quota_and_rate_code_are_safely_distinguished(self):
        from urllib.error import HTTPError
        for error_code in ("credit_balance_exhausted",
                           "project_spend_limit_exceeded",
                           "rate_limit_exceeded"):
            body = json.dumps({"error": {
                "message": "private information from error body",
                "code": error_code,
                "type": "insufficient_quota"
            }}).encode("utf-8")
            err = HTTPError("https://api.openai.com/v1/responses",
                            429, "Too Many Requests", {}, io.BytesIO(body))
            status = mod.openai_error_status(err)
            self.assertEqual(status, "HTTP_429_" + error_code)
            self.assertNotIn("private information", status)

    def test_429_malicious_error_body_never_leaks_to_output(self):
        from urllib.error import HTTPError
        body = json.dumps({"error": {
            "code": "private_api_key_sk_DO_NOT_LEAK",
            "type": "private_api_key_sk_DO_NOT_LEAK",
            "message": "private_api_key_sk_DO_NOT_LEAK",
        }}).encode("utf-8")
        err = HTTPError("https://api.openai.com/v1/responses",
                        429, "Too Many Requests", {}, io.BytesIO(body))
        self.assertEqual(mod.openai_error_status(err), "HTTP_429")

    def test_openai_failure_does_not_post_discord(self):
        stdout = io.StringIO()
        with (patch.object(sys, "argv",
                           ["tool", "--test", "--use-openai", "--send"]),
              patch("discord_openai_status.load_local_env", return_value="NOT_FOUND"),
              patch("discord_openai_status.openai_note",
                    return_value=(None, "HTTP_429_credit_balance_exhausted")),
              patch("discord_openai_status.send_discord") as discord,
              patch("sys.stdout", stdout)):
            result = mod.main()
        discord.assert_not_called()
        self.assertEqual(result, 2)
        self.assertIn("OPENAI_RESULT: HTTP_429_credit_balance_exhausted", stdout.getvalue())
        self.assertIn("DISCORD_RESULT: SKIPPED_OPENAI_FAILED", stdout.getvalue())

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
