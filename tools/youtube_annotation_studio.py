#!/usr/bin/env python3
"""Local YouTube player + human race-tactics annotation studio.

YouTube video is viewed through the official iframe player. This server never
requests, downloads, caches, records or inspects audiovisual bytes. Only
manually entered rider facts and playback-position markers are saved under
local_data/history/youtube_rider_events.csv, with accurate annotation time.

The video's race ID must first be confirmed in youtube_race_links.csv.
Historical feature builder separately validates the review link and rejects
labels unavailable as of the future race's preclose observation time.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import secrets
import sys
import tempfile
import webbrowser
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from build_multiview_features import HISTORY_FIELDS, dt, local, validate_history
from youtube_race_search import HEADER, DEFAULT_INDEX, VIDEO_ID, RACE_ID

JST = ZoneInfo("Asia/Tokyo")
UI_FILE = Path(__file__).with_name("youtube_annotation_studio.html")
DEFAULT_EVENTS = Path("local_data/history/youtube_rider_events.csv")
ANNOTATION_FIELDS = HISTORY_FIELDS + ("annotation_markers_json",)
ALLOWED_MARKERS = {"attack", "front_start", "front_end", "late_gain"}
MAX_POST = 128 * 1024


def reviewed_link(index, race_id):
    index = local(index)
    if not RACE_ID.fullmatch(race_id):
        raise ValueError("race-id must be numeric (10..24 digits)")
    if not index.is_file() or index.is_symlink() or index.stat().st_size > 1024 * 1024:
        raise ValueError("reviewed YouTube link CSV not found or invalid")
    with index.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != list(HEADER):
            raise ValueError("reviewed links CSV header incompatible")
        matches = [row for row in reader if row.get("race_id") == race_id]
    if len(matches) != 1:
        raise ValueError("exactly one manually reviewed YouTube link required for race")
    link = matches[0]
    if (link["review_status"] != "MANUALLY_CONFIRMED_LINK_ONLY" or
            not VIDEO_ID.fullmatch(link["video_id"])):
        raise ValueError("race video has not been manually verified")
    if not link["race_date"] or not link["venue"]:
        raise ValueError("reviewed link lacks race date or venue")
    dt(link["reviewed_at_jst"])
    return link


def prepare_session(
    race_id, race_at_jst, venue_key, grade, cars,
    *, index=DEFAULT_INDEX, now=None,
):
    link = reviewed_link(index, race_id)
    race_at = dt(race_at_jst)
    clock = dt((now or datetime.now(JST)).isoformat())
    if race_at >= clock:
        raise ValueError("annotation target race has not occurred yet")
    if datetime.fromisoformat(race_at_jst).date().isoformat() != link["race_date"]:
        raise ValueError("race start date disagrees with reviewed YouTube link")
    if dt(link["reviewed_at_jst"]) > clock:
        raise ValueError("reviewed YouTube link timestamp is in the future")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,39}", venue_key or ""):
        raise ValueError("venue-key must be the same lowercase venue slug used in target_context.csv")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,15}", grade or ""):
        raise ValueError("grade must be a short grade identifier, e.g. F2")
    if type(cars) is not int or cars not in (7, 8, 9):
        raise ValueError("cars must be 7, 8 or 9")
    return {
        "race_id": race_id, "race_at_jst": race_at_jst,
        "venue_key": venue_key, "display_venue": link["venue"],
        "race_date": link["race_date"], "grade": grade,
        "video_id": link["video_id"], "cars": cars,
        "index": str(local(index)),
    }


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be integer {minimum}..{maximum}")
    return value


def _ratio(value):
    if type(value) not in (int, float):
        raise ValueError("line_cohesion must be a number between 0 and 1")
    val = float(value)
    if not math.isfinite(val) or not 0 <= val <= 1:
        raise ValueError("line_cohesion must be between 0 and 1")
    return val


def _second(value):
    if type(value) not in (int, float):
        raise ValueError("video playback timestamp must be numeric")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 21600:
        raise ValueError("video playback second outside 0..21600")
    return round(value, 2)


def tally_markers(markers, car, cars):
    if not isinstance(markers, list) or len(markers) > 300:
        raise ValueError("marker list must have <=300 entries")
    parsed = []
    for item in markers:
        if not isinstance(item, dict) or set(item) - {"car_no", "kind", "second", "places"}:
            raise ValueError("unknown or invalid marker fields")
        if _integer(item.get("car_no"), "marker car", 1, cars) != car:
            continue
        kind = item.get("kind")
        if kind not in ALLOWED_MARKERS:
            raise ValueError("marker kind not supported")
        sec = _second(item.get("second"))
        places = item.get("places")
        if kind == "late_gain":
            places = _integer(places, "late-gain places", 1, cars - 1)
        elif places is not None:
            raise ValueError("only late_gain markers may specify places")
        parsed.append({"car_no": car, "kind": kind, "second": sec,
                       **({"places": places} if places is not None else {})})
    parsed.sort(key=lambda x: (x["second"],
                               0 if x["kind"] == "front_end" else 1))
    attacks = sum(x["kind"] == "attack" for x in parsed)
    late = sum(x.get("places", 0) for x in parsed if x["kind"] == "late_gain")
    if attacks > 30 or late > 8:
        raise ValueError("too many attacks or late gains for one rider")
    start = None
    front = 0.0
    for x in parsed:
        if x["kind"] == "front_start":
            if start is not None:
                raise ValueError("overlapping front-start markers")
            start = x["second"]
        elif x["kind"] == "front_end":
            if start is None or x["second"] <= start:
                raise ValueError("front-end marker must follow front-start")
            front += x["second"] - start
            start = None
    if start is not None or front > 3000:
        raise ValueError("unclosed front interval or excessively long duration")
    return attacks, round(front, 2), late, parsed


def build_annotated_rows(payload, session, *, now=None):
    """Validate human-reviewed complete race, not infer labels from YouTube."""
    if not isinstance(payload, dict) or payload.get("review_complete") is not True:
        raise ValueError("explicit whole-race review confirmation required")
    if set(payload) != {"review_complete", "riders", "markers"}:
        raise ValueError("unexpected annotation fields")
    riders, markers = payload["riders"], payload["markers"]
    cars = session["cars"]
    if not isinstance(riders, list) or len(riders) != cars:
        raise ValueError("enter every one of the 7, 8 or 9 riders")
    if not isinstance(markers, list) or len(markers) > 300:
        raise ValueError("too many playback markers")
    # Validate every marker, including those whose car is not yet selected.
    for marker in markers:
        if not isinstance(marker, dict) or not set(marker) <= {"car_no", "kind", "second", "places"}:
            raise ValueError("malformed playback marker")
        _integer(marker.get("car_no"), "marker car", 1, cars)
        if marker.get("kind") not in ALLOWED_MARKERS:
            raise ValueError("invalid marker kind")
        _second(marker.get("second"))
        if marker.get("kind") == "late_gain":
            _integer(marker.get("places"), "late_gain", 1, cars - 1)
        elif "places" in marker:
            raise ValueError("unexpected marker places")
    now = dt((now or datetime.now(JST)).isoformat())
    if now <= dt(session["race_at_jst"]):
        raise ValueError("annotations cannot predate the historical race")
    link = reviewed_link(Path(session["index"]), session["race_id"])
    if link["video_id"] != session["video_id"]:
        raise ValueError("reviewed video association changed")
    if now < dt(link["reviewed_at_jst"]):
        raise ValueError("annotation cannot precede video-link review")
    timestamp = now.astimezone(JST).isoformat()
    car_ids, rider_ids, finish_positions = set(), set(), set()
    rows = []
    for rider in riders:
        if not isinstance(rider, dict) or set(rider) != {
                "car_no", "rider_id", "finish_position", "line_cohesion"}:
            raise ValueError("one or more rider fields missing")
        car = _integer(rider["car_no"], "rider car", 1, cars)
        rider_id = rider["rider_id"]
        if (not isinstance(rider_id, str) or
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,63}", rider_id)):
            raise ValueError("rider_id must be a stable, real provider rider identifier")
        position = _integer(rider["finish_position"], "finish position", 1, cars)
        cohesion = _ratio(rider["line_cohesion"])
        if car in car_ids or rider_id in rider_ids or position in finish_positions:
            raise ValueError("duplicate rider/car/finish label; ties are not supported")
        car_ids.add(car)
        rider_ids.add(rider_id)
        finish_positions.add(position)
        attacks, front, late, own_marks = tally_markers(markers, car, cars)
        row = {
            "race_id": session["race_id"],
            "rider_id": rider_id,
            "race_at_jst": session["race_at_jst"],
            "available_at_jst": timestamp,
            "venue": session["venue_key"], "grade": session["grade"],
            "finish_position": str(position),
            "attack_count": str(attacks), "front_seconds": f"{front:.2f}",
            "late_gain_places": str(late), "line_cohesion": f"{cohesion:.3f}",
            "video_sha256": "", "youtube_video_id": session["video_id"],
            "source_url": "https://www.youtube.com/watch?v=" + session["video_id"],
            "rights_status": "self_recorded",
            "annotation_markers_json": json.dumps(own_marks, ensure_ascii=False,
                                                    separators=(",", ":")),
        }
        validate_history(row)
        rows.append(row)
    if car_ids != set(range(1, cars + 1)) or finish_positions != set(range(1, cars + 1)):
        raise ValueError("rider cars/finished positions must cover exactly 1..N")
    return sorted(rows, key=lambda x: int(next(r["car_no"] for r in riders
                                              if r["rider_id"] == x["rider_id"])))


def save_annotations(payload, session, *, output=DEFAULT_EVENTS, now=None):
    output = local(output)
    if output.suffix != ".csv" or output.is_symlink():
        raise ValueError("annotation output must be a local CSV, not a symlink")
    rows = build_annotated_rows(payload, session, now=now)
    previous = []
    if output.exists():
        if not output.is_file() or output.stat().st_size > 20_000_000:
            raise ValueError("existing annotation archive invalid or too large")
        with output.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != list(ANNOTATION_FIELDS):
                raise ValueError("annotation archive header incompatible; use a separate file")
            previous = list(reader)
            if any(None in row for row in previous):
                raise ValueError("malformed existing annotations")
        if any(r["race_id"] == session["race_id"] for r in previous):
            raise ValueError("this race has already been reviewed; no silent overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", newline="", encoding="utf-8", dir=output.parent,
            prefix="yt_review_", suffix=".tmp", delete=False,
        ) as fh:
            temp = Path(fh.name)
            writer = csv.DictWriter(fh, fieldnames=ANNOTATION_FIELDS)
            writer.writeheader()
            writer.writerows(previous)
            writer.writerows(rows)
        os.replace(temp, output)
    finally:
        if temp and temp.exists():
            temp.unlink()
    return {
        "status": "ANNOTATED_HISTORICAL_RACE_SAVED_NOT_TRAINED",
        "race_id": session["race_id"], "rider_rows_added": len(rows),
        "output": str(output), "video_downloaded": False,
        "model_verified": False, "bet_allowed": False,
    }


class AnnotatorServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, handler, session, *, output=DEFAULT_EVENTS):
        super().__init__(address, handler)
        self.session = session
        self.output = local(output)
        self.secret = secrets.token_urlsafe(32)
        self.expected_origin = f"http://127.0.0.1:{self.server_port}"


class AnnotationHandler(BaseHTTPRequestHandler):
    server: AnnotatorServer

    def log_message(self, format, *args):
        # Suppress URLs or headers that may contain accidental secrets.
        return

    def _reject(self, code, message):
        self._json_response(code, {"status": "REJECTED", "message": message})

    def _json_response(self, code, body):
        raw = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _host_ok(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self._host_ok():
            return self._reject(403, "local host only")
        if self.path != "/":
            return self._reject(404, "not found")
        source = UI_FILE.read_text(encoding="utf-8")
        config = {key: self.server.session[key] for key in (
            "race_id", "race_at_jst", "race_date", "display_venue",
            "venue_key", "grade", "video_id", "cars",
        )}
        config["csrf_token"] = self.server.secret
        safe_json = json.dumps(config, ensure_ascii=False).replace("<", "\\u003c")
        html = source.replace("__RACE_CONFIGURATION_JSON__", safe_json)
        raw = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if not self._host_ok():
            return self._reject(403, "local host only")
        if self.path != "/api/save":
            return self._reject(404, "not found")
        if (self.headers.get("Origin") != self.server.expected_origin or
                self.headers.get("X-Annotation-Token") != self.server.secret):
            return self._reject(403, "local origin and session token required")
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            return self._reject(415, "JSON content type required")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_POST:
                raise ValueError("annotation JSON too large or empty")
            payload = json.loads(self.rfile.read(length))
            result = save_annotations(payload, self.server.session,
                                      output=self.server.output)
        except (ValueError, OSError, TypeError, KeyError, csv.Error,
                json.JSONDecodeError) as exc:
            return self._reject(400, str(exc)[:160])
        return self._json_response(200, result)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--race-id", required=True, help="manually reviewed canonical race ID")
    p.add_argument("--race-at-jst", required=True,
                   help="the actual historical race start time with +09:00 timezone")
    p.add_argument("--venue-key", required=True,
                   help="normalized venue key as in target_context.csv (e.g. matsuyama)")
    p.add_argument("--grade", required=True, help="e.g. F2")
    p.add_argument("--cars", type=int, required=True, choices=[7, 8, 9])
    p.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    p.add_argument("--output", type=Path, default=DEFAULT_EVENTS)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true",
                   help="print local URL instead of opening a browser automatically")
    args = p.parse_args()
    try:
        if not 1024 <= args.port <= 65535:
            raise ValueError("port must be 1024..65535")
        session = prepare_session(
            args.race_id, args.race_at_jst, args.venue_key, args.grade,
            args.cars, index=args.index,
        )
        with AnnotatorServer(("127.0.0.1", args.port), AnnotationHandler,
                             session, output=args.output) as server:
            url = server.expected_origin + "/"
            print("LOCAL_YOUTUBE_ANNOTATION_STUDIO", url, flush=True)
            print("YouTube playback stays in the browser. Only human annotations are saved locally.")
            print("Stop with Ctrl+C.", flush=True)
            if not args.no_browser:
                webbrowser.open(url, new=2)
            try:
                server.serve_forever(poll_interval=0.2)
            except KeyboardInterrupt:
                print("\nStopped.")
            return 0
    except (ValueError, OSError, TypeError, KeyError, csv.Error) as exc:
        print("ANNOTATION_STUDIO_NOT_READY:", str(exc)[:200], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
