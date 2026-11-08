"""Offline local video manifest and optional FFmpeg sampling tests."""
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import video_history as m


class VideoTests(unittest.TestCase):
    def video(self):
        location = Path("local_data/videos/20261101.mp4")
        location.parent.mkdir(parents=True, exist_ok=True)
        location.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 4096)
        return location

    def test_registration_local_no_network_and_no_video_model_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = os.getcwd()
            os.chdir(tmp)
            try:
                file = self.video()
                entry = m.register(
                    file, "7520261108010001", "https://example.org/allowed-video",
                    "permitted", now=datetime.fromisoformat("2026-11-08T10:00:00+09:00")
                )
                self.assertEqual(entry["status"], "LOCAL_VIDEO_REGISTERED_NOT_TRAINED")
                self.assertFalse(entry["model_verified"])
                self.assertFalse(entry["video_uploaded_to_openai"])
                manifest = Path("local_data/history/video_manifest.jsonl")
                content = json.loads(manifest.read_text(encoding="utf-8").splitlines()[0])
                self.assertEqual(content["annotations_status"], "UNANNOTATED")
                self.assertEqual(content["size_bytes"], file.stat().st_size)
                self.assertEqual(len(content["sha256"]), 64)
                with self.assertRaisesRegex(ValueError, "already registered"):
                    m.register(file, "7520261108010001",
                               "https://example.org/allowed-video", "permitted")
            finally:
                os.chdir(original)

    def test_mp4_signature_rejects_malformed_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = os.getcwd()
            os.chdir(tmp)
            try:
                p = self.video()
                p.write_bytes(b"\x00"*4108)
                with self.assertRaisesRegex(ValueError, "signature"):
                    m.inspect_video(p)
            finally:
                os.chdir(original)

    def test_frame_sampling_uses_only_local_ffmpeg_no_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = os.getcwd()
            os.chdir(tmp)
            try:
                file = self.video()
                calls = []
                class FakeResult:
                    returncode = 0
                def runner(args, **kwargs):
                    calls.append((args, kwargs))
                    return FakeResult()
                folder = m.extract_frames(file, "7520261108010001", "a"*64,
                                          seconds=30, max_frames=9, runner=runner)
                self.assertTrue(folder.is_dir())
                self.assertEqual(calls[0][0][0], "ffmpeg")
                self.assertFalse(calls[0][1]["shell"])
                self.assertIn("fps=1/30,scale=640:-2", calls[0][0])
                with self.assertRaisesRegex(ValueError, "already exists"):
                    m.extract_frames(file, "7520261108010001", "a"*64,
                                     seconds=30, max_frames=9, runner=runner)
            finally:
                os.chdir(original)

    def test_template_creates_headers_without_fake_annotations(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = os.getcwd()
            os.chdir(tmp)
            try:
                location = Path("local_data/history/events_template.csv")
                result = m.template(location)
                self.assertEqual(result["status"], "BLANK_VIDEO_EVENT_TEMPLATE_CREATED")
                self.assertEqual(len(location.read_text(encoding="utf-8").splitlines()), 1)
                with self.assertRaisesRegex(ValueError, "exists"):
                    m.template(location)
            finally:
                os.chdir(original)

    def test_paths_rights_and_source_must_be_real(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = os.getcwd()
            os.chdir(tmp)
            try:
                file = self.video()
                for kwargs in (
                    {"race_id": "notid", "source_url": "https://example.org/x",
                     "rights_status": "permitted"},
                    {"race_id": "7520261108010001", "source_url": "file://local",
                     "rights_status": "permitted"},
                    {"race_id": "7520261108010001", "source_url": "https://example.org/x",
                     "rights_status": "unknown"},
                ):
                    with self.assertRaises(ValueError):
                        m.register(file, **kwargs)
            finally:
                os.chdir(original)


if __name__ == "__main__":
    unittest.main()
