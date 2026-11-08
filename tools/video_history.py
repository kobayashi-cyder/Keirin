#!/usr/bin/env python3
"""Register local licensed race video and optionally extract local JPEG frames.

No downloading, scraping, automatic AI labeling or media upload. Original
video bytes never leave local_data; file SHA-256 is used for provenance.
The raw video is NOT a trained model. Attach verified manual race-action
annotations to tools/build_multiview_features.py to learn from tactics.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from build_multiview_features import HISTORY_FIELDS, local

JST = ZoneInfo("Asia/Tokyo")
VALID_EXTENSIONS = {".mp4", ".mkv", ".webm"}


def inspect_video(path):
    path = local(path)
    if path.suffix.lower() not in VALID_EXTENSIONS or not path.is_file() or path.is_symlink():
        raise ValueError("only local regular mp4/mkv/webm video files accepted")
    size = path.stat().st_size
    if not 1024 <= size <= 4 * 1024 ** 3:
        raise ValueError("video size must be 1KB..4GB")
    with path.open("rb") as stream:
        header = stream.read(16)
    if path.suffix.lower() == ".mp4":
        if header[4:8] != b"ftyp":
            raise ValueError("MP4 file signature does not match extension")
    elif header[:4] != bytes.fromhex("1a45dfa3"):
        raise ValueError("Matroska/WebM file signature does not match")
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024*1024)
            if not chunk:
                break
            sha.update(chunk)
    return size, sha.hexdigest()


def extract_frames(video, race_id, digest, *, seconds=20, max_frames=12,
                   runner=subprocess.run):
    """Optional local FFmpeg extraction, never upload content."""
    if type(seconds) is not int or not 5 <= seconds <= 300:
        raise ValueError("frame interval must be 5..300 seconds")
    if type(max_frames) is not int or not 1 <= max_frames <= 30:
        raise ValueError("frame count must be 1..30")
    directory = Path("local_data/video_frames") / (race_id + "_" + digest[:12])
    directory = local(directory)
    if directory.exists():
        raise ValueError("frames directory already exists; do not overwrite")
    directory.mkdir(parents=True)
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-n", "-i", str(video),
        "-vf", f"fps=1/{seconds},scale=640:-2",
        "-frames:v", str(max_frames), "-q:v", "6",
        str(directory / "frame_%03d.jpg"),
    ]
    try:
        outcome = runner(command, shell=False, capture_output=True,
                         timeout=180, check=False)
    except FileNotFoundError as e:
        raise ValueError("FFmpeg not installed; video remains available for later") from e
    except subprocess.TimeoutExpired as e:
        raise ValueError("local frame extraction timed out") from e
    if outcome.returncode:
        raise ValueError("FFmpeg failed to decode this local video")
    return directory


def register(video, race_id, source_url, rights_status, *,
             manifest=Path("local_data/history/video_manifest.jsonl"),
             frames=False, now=None, runner=subprocess.run):
    video, manifest = local(video), local(manifest)
    if not re.fullmatch(r"\d{10,24}", race_id):
        raise ValueError("race ID must be a canonical numeric ID")
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("real HTTPS source URL required")
    if rights_status not in ("permitted", "self_recorded"):
        raise ValueError("video use/store/training rights not confirmed")
    if video == manifest or manifest.suffix != ".jsonl":
        raise ValueError("manifest must be a separate local JSONL file")
    size, digest = inspect_video(video)
    previous = set()
    if manifest.exists():
        if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 1024*1024:
            raise ValueError("manifest invalid or unexpectedly large")
        with manifest.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    previous.add(json.loads(line)["sha256"])
    if digest in previous:
        raise ValueError("video already registered; no duplicates")
    folder = extract_frames(video, race_id, digest, runner=runner) if frames else None
    now = now or datetime.now(JST)
    record = {
        "schema": "keirin-local-video-manifest-v1",
        "race_id": race_id,
        "sha256": digest,
        "size_bytes": size,
        "video_file": str(video),
        "source_url": source_url,
        "rights_status": rights_status,
        "registered_at_jst": now.astimezone(JST).isoformat(),
        "frames_path": str(folder) if folder else None,
        "annotations_status": "UNANNOTATED",
        "model_verified": False,
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "status": "LOCAL_VIDEO_REGISTERED_NOT_TRAINED",
        "race_id": race_id, "sha256_prefix": digest[:12],
        "frames_path": record["frames_path"],
        "annotation_csv_template": "local_data/history/video_events_template.csv",
        "video_uploaded_to_openai": False,
        "model_verified": False,
    }


def template(path):
    path = local(path)
    if path.exists():
        raise ValueError("annotation template exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        csv.DictWriter(fh, fieldnames=HISTORY_FIELDS).writeheader()
    return {"status": "BLANK_VIDEO_EVENT_TEMPLATE_CREATED", "file": str(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--template", type=Path, help="create blank rider-event labels CSV")
    group.add_argument("--video", type=Path, help="local licensed mp4/mkv/webm")
    parser.add_argument("--race-id")
    parser.add_argument("--source-url")
    parser.add_argument("--rights-status", choices=["permitted", "self_recorded"])
    parser.add_argument("--frames", action="store_true", help="extract sampled local JPEG frames with FFmpeg")
    args = parser.parse_args()
    try:
        if args.template:
            response = template(args.template)
        else:
            if not all((args.race_id, args.source_url, args.rights_status)):
                raise ValueError("--race-id, --source-url and --rights-status required")
            response = register(args.video, args.race_id, args.source_url,
                                args.rights_status, frames=args.frames)
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, json.JSONDecodeError, KeyError) as e:
        # Never output media bytes, tokens, or private third-party responses.
        print("VIDEO_IMPORT_REJECTED:", type(e).__name__ + ": " + str(e)[:120],
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
