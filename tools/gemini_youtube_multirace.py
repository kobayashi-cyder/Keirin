#!/usr/bin/env python3
"""Multi-race, multi-pass Gemini YouTube vision pipeline (research-only).

YouTube public URLs go DIRECTLY to the official Google Gemini Interactions
API, never through a downloader. Every race uses independent video-analysis
passes, and only agreeing car-level observations become research candidates.
Supports bounded parallelism, limited optional official YouTube API discovery,
durable per-pass resume, strict point-in-time research CSV export.

Default is DRY RUN: no Google requests, credentials or model charges.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from build_multiview_features import HISTORY_FIELDS, dt, local, validate_history
from youtube_race_search import (
    HEADER, VIDEO_ID, RACE_ID, browser_search_url, event, query_for_event,
    search_api, youtube_api_key,
)
from gpt4o_race_vision import OFFICIAL_FIELDS, official_results

JST = ZoneInfo("Asia/Tokyo")
MODEL_DEFAULT = "gemini-3.6-flash"
QUEUE_FIELDS = (
    "race_id", "race_date", "venue", "venue_key", "race_number",
    "cars", "youtube_url",
)
RUNS_DIR = Path("local_data/gemini_runs")
REPORTS_DIR = Path("local_data/gemini_reports")
EVENTS_FILE = Path("local_data/history/gemini_rider_events.csv")
LINKS_FILE = Path("local_data/history/gemini_research_links.csv")
AUTO_STATUS = "AUTOMATED_RESEARCH_ONLY"
PASS_SCHEMA = "keirin-gemini-yt-pass-v1"
AGREE_SCHEMA = "keirin-gemini-consensus-v1"
TACTIC_KEYS = ("attack_count", "front_seconds", "late_gain_places", "line_cohesion")
NUM_SCHEMA = {"type": ["number", "null"]}
INT_SCHEMA = {"type": ["integer", "null"]}
GEMINI_SCHEMA = {
    "type": "object",
    "properties": {
        "target_race_visible": {"type": "boolean"},
        "full_race_covered": {"type": "boolean"},
        "race_start_seconds": NUM_SCHEMA,
        "race_end_seconds": NUM_SCHEMA,
        "riders": {
            "type": "array", "items": {
                "type": "object", "properties": {
                    "car_no": {"type": "integer"},
                    "attack_count": INT_SCHEMA,
                    "front_seconds": NUM_SCHEMA,
                    "late_gain_places": INT_SCHEMA,
                    "line_cohesion": NUM_SCHEMA,
                    "confidence": {"type": "number"},
                    "evidence_seconds": {"type": "array", "items": {"type": "number"}},
                    "limitations": {"type": "string"},
                },
                "required": [
                    "car_no", *TACTIC_KEYS, "confidence",
                    "evidence_seconds", "limitations",
                ],
            },
        },
        "identity_evidence": {"type": "string"},
    },
    "required": [
        "target_race_visible", "full_race_covered",
        "race_start_seconds", "race_end_seconds",
        "riders", "identity_evidence",
    ],
}
PASS_FOCUSES = (
    "Independently trace each car from the first lap through the finish. "
    "Focus on when a car attacks, position changes and periods riding in front.",
    "Independently review line formations, breaking and rejoining, and "
    "evaluate late gains and the evidence for any front-running duration.",
    "Independently challenge the proposed race identity and detect bib-number "
    "confusions or ambiguous tactics. Refuse to infer motions not in the video.",
)


@dataclass(frozen=True)
class Race:
    race_id: str
    race_date: str
    venue: str
    venue_key: str
    race_number: int
    cars: int
    youtube_url: str = ""


def _localfile(path, suffix):
    path = local(path)
    if path.is_symlink() or (path.exists() and not path.is_file()) or path.suffix != suffix:
        raise ValueError("expected a regular local_data/" + suffix + " file")
    return path


def canonical_video(value):
    if not isinstance(value, str) or len(value) > 350:
        raise ValueError("invalid video URL")
    u = urlsplit(value.strip())
    host = (u.hostname or "").lower()
    if u.scheme != "https" or u.username or u.password or u.port or u.fragment:
        raise ValueError("YouTube video must be a normal public HTTPS watch link")
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com") and u.path == "/watch":
        vid = parse_qs(u.query).get("v", [])
        if len(vid) != 1:
            raise ValueError("one YouTube video ID required")
        ident = vid[0]
    elif host in ("youtu.be", "www.youtu.be") and not u.query:
        ident = u.path.lstrip("/")
    else:
        raise ValueError("unsupported YouTube watch URL")
    if not VIDEO_ID.fullmatch(ident):
        raise ValueError("invalid YouTube video ID")
    return ident, "https://www.youtube.com/watch?v=" + ident


def parse_race(raw):
    if set(raw) != set(QUEUE_FIELDS):
        raise ValueError("incorrect race queue fields")
    rid = raw["race_id"]
    if not isinstance(rid, str) or not RACE_ID.fullmatch(rid):
        raise ValueError("race ID must be 10..24 digits")
    race_date = raw["race_date"]
    venue = raw["venue"]
    try:
        race_num = int(raw["race_number"])
        cars = int(raw["cars"])
    except (ValueError, TypeError):
        raise ValueError("race number / cars missing or invalid") from None
    if str(race_num) != str(raw["race_number"]) or str(cars) != str(raw["cars"]):
        raise ValueError("race number and cars must be canonical integers")
    event(race_date, venue, race_num)
    if cars not in (7,8,9):
        raise ValueError("cars must be 7, 8 or 9 (do not guess)")
    key = raw["venue_key"]
    if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,39}", key):
        raise ValueError("venue_key must match target_context venue slug")
    url = raw["youtube_url"].strip()
    if url:
        _, url = canonical_video(url)
    return Race(rid, race_date, venue, key, race_num, cars, url)


def load_jobs(path):
    path = _localfile(path, ".csv")
    if not path.exists() or path.stat().st_size > 100_000:
        raise ValueError("race queue missing or too large")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != list(QUEUE_FIELDS):
            raise ValueError("queue CSV headers differ from documented order")
        raw = list(reader)
    if not 1 <= len(raw) <= 20:
        raise ValueError("race queue must contain 1..20 real races")
    jobs = [parse_race(r) for r in raw]
    if len({j.race_id for j in jobs}) != len(jobs):
        raise ValueError("duplicated race IDs in queue")
    return jobs


def create_template(path):
    path = _localfile(path, ".csv")
    if path.exists():
        raise ValueError("template already exists; no overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=QUEUE_FIELDS).writeheader()
    return path


def _num(x, low, high, *, integer=False, nullable=False):
    if nullable and x is None:
        return None
    if type(x) not in ((int,) if integer else (int, float)):
        raise ValueError("model returned invalid/missing numeric observation")
    if not math.isfinite(x) or not low <= x <= high:
        raise ValueError("model returned out of range/nonfinite number")
    return x


def validate_response(resp, job):
    if not isinstance(resp, dict) or set(resp) != set(GEMINI_SCHEMA["properties"]):
        raise ValueError("Gemini output missing fields or has unexpected properties")
    if type(resp["target_race_visible"]) is not bool or type(resp["full_race_covered"]) is not bool:
        raise ValueError("race identity booleans invalid")
    if not isinstance(resp["identity_evidence"], str) or len(resp["identity_evidence"]) > 500:
        raise ValueError("race identity evidence invalid")
    start = _num(resp["race_start_seconds"], 0, 21600, nullable=True)
    end = _num(resp["race_end_seconds"], 0, 21600, nullable=True)
    if (start is None) != (end is None) or (start is not None and end <= start):
        raise ValueError("race start/end not in a valid interval")
    riders = resp["riders"]
    if not isinstance(riders, list) or len(riders) > job.cars:
        raise ValueError("too many rider observations")
    cars = set()
    for rider in riders:
        if not isinstance(rider, dict) or set(rider) != set(GEMINI_SCHEMA["properties"]["riders"]["items"]["properties"]):
            raise ValueError("rider observation has incorrect fields")
        c = _num(rider["car_no"], 1, job.cars, integer=True)
        if c in cars:
            raise ValueError("duplicate car in model response")
        cars.add(c)
        _num(rider["attack_count"], 0, 30, integer=True, nullable=True)
        _num(rider["front_seconds"], 0, 3000, nullable=True)
        _num(rider["late_gain_places"], 0, job.cars - 1, integer=True, nullable=True)
        _num(rider["line_cohesion"], 0, 1, nullable=True)
        _num(rider["confidence"], 0, 1)
        if not isinstance(rider["limitations"], str) or len(rider["limitations"]) > 500:
            raise ValueError("missing observation limitations")
        ts = rider["evidence_seconds"]
        if not isinstance(ts, list) or len(ts) > 30:
            raise ValueError("evidence timestamps malformed")
        for sec in ts:
            _num(sec, 0, 21600)
            if start is not None and not (start - 5 <= sec <= end + 5):
                raise ValueError("rider evidence timestamp outside identified race window")
    if resp["target_race_visible"] or resp["full_race_covered"]:
        if start is None or end - start < 20 or not resp["identity_evidence"].strip():
            raise ValueError("claimed race verification lacks timeline or visible identity evidence")
    return resp


def prompt(job, pass_index):
    if pass_index < 1 or pass_index > len(PASS_FOCUSES):
        raise ValueError("unsupported independent pass number")
    return (
        "あなたは競輪の映像を実際に視聴して戦術を抽出する検証者です。提供した"
        "YouTube動画だけを映像・音声資料として使い、タイトルやリクエストの情報を"
        "映像で確認した事実と混同しないでください。動画には複数レースが"
        "含まれる可能性があります。\n"
        f"検証したい対象: {job.race_date} {job.venue} "
        f"{job.race_number}R, 車数 {job.cars}, 外部レースID {job.race_id}。"
        "レースID自体は画面に存在しなくてもよいが、開催日・競輪場・R番号を"
        "画面の表示や実況から同定できない場合target_race_visible=false。"
        "複数レースを混合しないでください。\n"
        "レース映像開始・終了のYouTube動画全体での再生秒を出し、"
        "全レースが見えないときfull_race_covered=false。"
        "車番はゼッケン・実況で確定できる場合だけ扱います。"
        "仕掛け回数attack_count、先頭走行秒数front_seconds、終盤で順位を"
        "上げた数late_gain_places、ライン連携スコアline_cohesionを"
        "推測ではなく実際に観察できた場合だけ数値で返してください。"
        "1fps等で動きが見落とされる場合、先頭秒数の正確な測定が不可能な場合、"
        "未確認がある場合はnull（決して0で埋めない）。"
        "evidence_secondsには映像から実際に確認した動画全体の再生秒を記録。"
        "選手本人ID、確定着順を映像だけで確定しないこと。"
        "前の解析結果を見ずに独立に判断すること。"
        "動画そのものの画像・音声が読めない場合は"
        "target_race_visible=false、riders=[]、時間=nullとして返す。"
        "JSONスキーマどおりのJSONだけ返してください。\n"
        "今回の独立した観点: " + PASS_FOCUSES[pass_index - 1]
    )


def gemini_key(env=Path(".env")):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    env = Path(env)
    if env.exists():
        if env.is_symlink() or not env.is_file() or env.stat().st_size > 16_384:
            raise ValueError("invalid local .env")
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("#"):
                continue
            name, sep, value = line.partition("=")
            if sep and name.strip() == "GEMINI_API_KEY":
                key = value.strip().strip("'\"").strip()
                break
    if not key or len(key) > 200 or any(ch.isspace() for ch in key):
        raise ValueError("GEMINI_API_KEY missing; configure it privately in .env")
    return key


def call_gemini(job, video_url, pass_index, model, key):
    """The only Gemini API call. google-genai is imported lazily."""
    try:
        from google import genai
    except ImportError:
        raise ValueError("google-genai SDK not installed: py -3 -m pip install -U google-genai") from None
    client = genai.Client(api_key=key)
    try:
        response = client.interactions.create(
            model=model,
            input=[
                {"type": "text", "text": prompt(job, pass_index)},
                {"type": "video", "uri": video_url},
            ],
            response_format=[
                {"type": "text", "mime_type": "application/json", "schema": GEMINI_SCHEMA}
            ],
            store=False,
        )
        content = response.output_text
        if not isinstance(content, str):
            raise ValueError("Gemini returned no readable JSON output")
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            raise ValueError("Gemini did not return valid JSON") from None
        return validate_response(data, job)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _atomic_new(path, obj):
    """Persist only if missing, refusing overwrite for resumable paid calls."""
    path = _localfile(path, ".json")
    if path.exists():
        raise ValueError("cannot overwrite an existing analysis result")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix="gemini_", suffix=".tmp", delete=False,
        ) as f:
            tmp = Path(f.name)
            json.dump(obj, f, ensure_ascii=False, allow_nan=False, indent=2)
            f.write("\n")
        # os.link fails if the name already exists, unlike os.replace.
        os.link(tmp, path)
    finally:
        if tmp and tmp.exists():
            tmp.unlink()


def _read_json(path):
    path = _localfile(path, ".json")
    if not path.is_file() or path.stat().st_size > 200_000:
        raise ValueError("saved Gemini result missing or too large")
    return json.loads(path.read_text(encoding="utf-8"))


def file_key(job, video_url, pass_index=None):
    ident, _ = canonical_video(video_url)
    base = job.race_id + "_" + ident
    return base + (f"_pass{pass_index}" if pass_index else "")


def completed_run_path(job, url, pass_index):
    return RUNS_DIR / (file_key(job, url, pass_index) + ".json")


def report_path(job, url):
    return REPORTS_DIR / (file_key(job, url) + ".json")


def analyze_pass(job, url, pass_index, model, key, *, caller=call_gemini):
    path = completed_run_path(job, url, pass_index)
    if path.exists():
        obj = _read_json(path)
        if (obj.get("schema") != PASS_SCHEMA or obj.get("race") != asdict(job)
                or obj.get("youtube_url") != url or obj.get("pass_index") != pass_index
                or obj.get("model") != model):
            raise ValueError("existing pass result conflicts with this analysis job")
        validate_response(obj["prediction"], job)
        return obj, "REUSED_SAVED_PASS"
    pred = caller(job, url, pass_index, model, key)
    validate_response(pred, job)
    result = {
        "schema": PASS_SCHEMA, "race": asdict(job), "youtube_url": url,
        "pass_index": pass_index, "model": model,
        "observed_at_jst": datetime.now(JST).isoformat(),
        "prediction": pred,
    }
    _atomic_new(path, result)
    return result, "NEW_API_PASS"


def agree(job, url, passes):
    """Calculate per-metric agreement, never treat correlated AI votes as truth."""
    if len(passes) not in (2,3):
        raise ValueError("independent analysis requires 2 or 3 passes")
    pred = [validate_response(x["prediction"], job) for x in passes]
    time_pairs = [
        (r["race_start_seconds"], r["race_end_seconds"]) for r in pred
    ]
    valid_identity = all(
        x["target_race_visible"] is True and x["full_race_covered"] is True
        and bool(x["identity_evidence"].strip())
        for x in pred
    ) and all(start is not None and end is not None for start, end in time_pairs)
    if valid_identity:
        start_times = [pair[0] for pair in time_pairs]
        end_times = [pair[1] for pair in time_pairs]
        valid_identity = (max(start_times) - min(start_times) <= 5
                          and max(end_times) - min(end_times) <= 5)
    maps = [{r["car_no"]: r for r in x["riders"]} for x in pred]
    if not all(set(m) == set(range(1, job.cars + 1)) for m in maps):
        valid_identity = False
    output = []
    for car in range(1, job.cars+1):
        samples = [m.get(car) for m in maps]
        candidate = {"car_no": car}
        confident = all(
            x is not None and x["confidence"] >= .90
            and len(x["evidence_seconds"]) >= 2
            for x in samples
        )
        for field in TACTIC_KEYS:
            values = [s[field] if s else None for s in samples]
            if not confident or any(v is None for v in values):
                candidate[field] = None
            else:
                spread = max(values) - min(values)
                tol = 2.0 if field == "front_seconds" else (
                    .05 if field == "line_cohesion" else 0
                )
                candidate[field] = (
                    round(sum(values) / len(values), 3) if spread <= tol else None
                )
        candidate["all_passes_confident"] = confident
        candidate["evidence_seconds_by_pass"] = [
            s["evidence_seconds"] if s is not None else [] for s in samples
        ]
        output.append(candidate)
    ready = valid_identity and all(
        all(row[f] is not None for f in TACTIC_KEYS) for row in output
    )
    return {
        "schema": AGREE_SCHEMA, "race": asdict(job), "youtube_url": url,
        "passes": len(passes), "model": passes[0]["model"],
        "identity_consensus": valid_identity,
        "all_metrics_consensus": ready,
        "status": "AUTOMATED_RESEARCH_CANDIDATE" if ready
                  else "INCOMPLETE_OR_DISAGREED_NOT_TRAINING_READY",
        "training_validated": False,
        "bet_allowed": False, "alert_allowed": False,
        "generated_at_jst": datetime.now(JST).isoformat(),
        "identified_window_seconds": (
            [round(sum(t[0] for t in time_pairs) / len(passes), 2),
             round(sum(t[1] for t in time_pairs) / len(passes), 2)]
            if valid_identity else None
        ),
        "riders": output,
        "source_pass_paths": [
            str(completed_run_path(job, url, p["pass_index"])) for p in passes
        ],
        "limitations": (
            "Both/three passes share one AI family and can share the SAME "
            "misidentification. Agreement is not independent ground truth. "
            "Exact front-running seconds and tactics may remain unknown. "
            "Use only research, after external results verification."
        ),
    }


def discover_candidates(jobs, *, max_candidates, max_searches, discoverer=None):
    """Official YouTube API search only, never download or trust candidate."""
    if sum(not j.youtube_url for j in jobs) > max_searches:
        raise ValueError("requested more YouTube search calls than max_searches")
    key = youtube_api_key() if discoverer is None and any(
        not j.youtube_url for j in jobs
    ) else None
    resolved = []
    for job in jobs:
        if job.youtube_url:
            resolved.append((job, [job.youtube_url]))
            continue
        if discoverer is None:
            hits = search_api(
                query_for_event(job.race_date, job.venue, job.race_number),
                key, max_results=max_candidates,
            )
        else:
            hits = discoverer(job)
        urls = []
        for hit in hits[:max_candidates]:
            if not isinstance(hit, dict) or "url" not in hit:
                continue
            try:
                _, url = canonical_video(hit["url"])
            except (ValueError, TypeError):
                continue
            if url not in urls:
                urls.append(url)
        resolved.append((job, urls))
    return resolved


def analyze_batch(jobs, *, model, passes=2, workers=2, max_api_calls=8,
                  auto_discover=False, candidates_per_race=1, max_searches=8,
                  key=None, caller=call_gemini, discoverer=None):
    if not 1 <= workers <= 3 or not 2 <= passes <= 3:
        raise ValueError("workers must be 1..3; passes 2..3")
    if not 1 <= max_api_calls <= 48 or not 1 <= candidates_per_race <= 3:
        raise ValueError("API call budget or candidate count outside bounds")
    if not 0 <= max_searches <= 20:
        raise ValueError("invalid YouTube search-request budget")
    if any(not job.youtube_url for job in jobs) and not auto_discover:
        raise ValueError("some races have no video URL; use --auto-discover")
    max_pairs = sum(1 if j.youtube_url else candidates_per_race for j in jobs)
    if max_pairs * passes > max_api_calls:
        raise ValueError("maximum potential AI calls exceed --max-api-calls budget")
    resolved = discover_candidates(
        jobs, max_candidates=candidates_per_race,
        max_searches=max_searches, discoverer=discoverer,
    ) if auto_discover else [(j, [j.youtube_url]) for j in jobs]
    tasks = [
        (j, url, n) for j, urls in resolved for url in urls
        for n in range(1, passes + 1)
    ]
    if len(tasks) > max_api_calls:
        raise ValueError("Gemini API call budget exceeded")
    # Explicitly require a real GEMINI_API_KEY in the CLI; injectable key
    # supports zero-network offline tests.
    if tasks and key is None:
        key = gemini_key()
    results = {}
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(analyze_pass, j, url, n, model, key, caller=caller):
            (j, url, n) for j, url, n in tasks
        }
        for fut in concurrent.futures.as_completed(futures):
            job, url, n = futures[fut]
            try:
                pass_doc, mode = fut.result()
                results[(job.race_id, url, n)] = pass_doc
            except Exception as exc:
                # Deliberately sanitize external SDK errors (may include URL/API key).
                errors.append({
                    "race_id": job.race_id, "pass": n,
                    "error_class": type(exc).__name__,
                    "status": "PASS_FAILED_NO_TRAINING",
                })
    report_docs = []
    for job, urls in resolved:
        for url in urls:
            selected = [
                results.get((job.race_id, url, i)) for i in range(1, passes+1)
            ]
            if None in selected:
                continue
            report = agree(job, url, selected)
            path = report_path(job, url)
            if path.exists():
                old = _read_json(path)
                if old["race"] != report["race"] or old["model"] != model:
                    raise ValueError("existing consensus report conflicts with job")
                # Reuse an existing report for idempotency.
                report = old
            else:
                _atomic_new(path, report)
            report_docs.append(report)
    return {
        "requested_races": len(jobs), "api_calls_budget": max_api_calls,
        "tasks_planned": len(tasks),
        "new_api_calls_max": len(tasks),  # saved passes are reused
        "completed_passes": len(results),
        "errors": errors, "reports": report_docs,
        "no_video_candidates": [
            j.race_id for j, urls in resolved if not urls
        ],
    }


def _read_csv(path, fields):
    path = _localfile(path, ".csv")
    if not path.is_file() or path.stat().st_size > 20_000_000:
        raise ValueError("required historical CSV missing or oversized")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != list(fields):
            raise ValueError("required CSV header differs")
        rows = list(reader)
    if any(None in row for row in rows):
        raise ValueError("malformed historical CSV")
    return rows


def _rewrite_csv(path, fields, rows):
    path = _localfile(path, ".csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent,
            prefix="gemini_append_", suffix=".tmp", delete=False,
        ) as f:
            tmp = Path(f.name)
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, path)
    finally:
        if tmp and tmp.exists():
            tmp.unlink()


def export_research(reports, official_csv, *, event_csv=EVENTS_FILE,
                    link_csv=LINKS_FILE, now=None):
    """Only exact one candidate/race, true independent result identities and
    complete AI agreement may enter explicitly labeled research-only CSV."""
    official_csv = _localfile(official_csv, ".csv")
    event_csv = _localfile(event_csv, ".csv")
    link_csv = _localfile(link_csv, ".csv")
    if not reports:
        return {"exported_races": 0, "blocked": [], "rows_added": 0}
    now = now or datetime.now(JST)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("export clock needs a timezone")
    timestamp = now.astimezone(JST).isoformat()
    prior_events = _read_csv(event_csv, HISTORY_FIELDS) if event_csv.exists() else []
    prior_links = _read_csv(link_csv, HEADER) if link_csv.exists() else []
    from collections import defaultdict
    grouped = defaultdict(list)
    for r in reports:
        if r.get("schema") != AGREE_SCHEMA:
            raise ValueError("invalid report schema")
        grouped[r["race"]["race_id"]].append(r)
    all_events, all_links = [], []
    blocked = []
    for rid, candidates in sorted(grouped.items()):
        accepted = [
            r for r in candidates if r["status"] == "AUTOMATED_RESEARCH_CANDIDATE"
            and r["all_metrics_consensus"] is True
            and r["identity_consensus"] is True
            and r["training_validated"] is False
        ]
        if len(accepted) != 1:
            blocked.append({"race_id": rid, "reason": "ambiguous_or_incomplete_candidate"})
            continue
        report = accepted[0]
        job = parse_race({**report["race"]})
        if job.youtube_url and job.youtube_url != report["youtube_url"]:
            # An explicitly supplied URL must remain unchanged. When the
            # queue URL was blank, official discovery is allowed but the
            # Gemini identity consensus and independent results still gate it.
            blocked.append({"race_id": rid, "reason": "mismatched_canonical_video_url"})
            continue
        if any(r["race_id"] == rid for r in prior_events + all_events) or any(
                r["race_id"] == rid for r in prior_links + all_links):
            blocked.append({"race_id": rid, "reason": "already_exported_no_overwrite"})
            continue
        try:
            official = official_results(official_csv, rid, job.cars)
            if any(
                r["venue"] != job.venue_key or
                dt(r["race_at_jst"]).astimezone(JST).date().isoformat() != job.race_date
                for r in official
            ):
                raise ValueError("official race venue/date does not match queue")
            if dt(report["generated_at_jst"]) > dt(timestamp):
                raise ValueError("report created after export")
            labels = {int(r["car_no"]): r for r in official}
            computed = {}
            for row in report["riders"]:
                car = row["car_no"]
                if car in computed or car not in labels or any(
                    row[k] is None for k in TACTIC_KEYS
                ):
                    raise ValueError("incomplete/duplicate model-car labels")
                computed[car] = row
            if set(computed) != set(labels):
                raise ValueError("model/official car numbers differ")
            video_id, _ = canonical_video(report["youtube_url"])
            batch_events = []
            for car in range(1, job.cars+1):
                off, val = labels[car], computed[car]
                row = {
                    "race_id": rid, "rider_id": off["rider_id"],
                    "race_at_jst": off["race_at_jst"],
                    "available_at_jst": timestamp,
                    "venue": off["venue"], "grade": off["grade"],
                    "finish_position": off["finish_position"],
                    "attack_count": str(val["attack_count"]),
                    "front_seconds": str(val["front_seconds"]),
                    "late_gain_places": str(val["late_gain_places"]),
                    "line_cohesion": str(val["line_cohesion"]),
                    "video_sha256": "", "youtube_video_id": video_id,
                    "source_url": report["youtube_url"],
                    "rights_status": "self_recorded",
                }
                validate_history(row)
                batch_events.append(row)
            all_events.extend(batch_events)
            all_links.append({
                "race_id": rid, "race_date": job.race_date,
                "venue": job.venue, "race_number": str(job.race_number),
                "video_id": video_id, "reviewed_at_jst": timestamp,
                "review_status": AUTO_STATUS,
                "download_permission": "NOT_ESTABLISHED",
            })
        except (ValueError, TypeError, KeyError) as e:
            blocked.append({"race_id": rid, "reason": type(e).__name__ + "_validation_failed"})
    if all_events:
        # Avoid partial overwrite of user records, but this is not an atomic
        # two-file transaction; keep both files in local_data only.
        _rewrite_csv(link_csv, HEADER, prior_links + all_links)
        _rewrite_csv(event_csv, HISTORY_FIELDS, prior_events + all_events)
    return {
        "status": "AUTOMATED_UNVERIFIED_RESEARCH_EXPORT",
        "exported_races": len(all_links), "rows_added": len(all_events),
        "blocked": blocked,
        "events_file": str(event_csv), "links_file": str(link_csv),
        "model_verified": False, "bet_allowed": False,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    modes = ap.add_mutually_exclusive_group(required=True)
    modes.add_argument("--jobs", type=Path, help="batch CSV inside local_data")
    modes.add_argument("--init-template", type=Path, help="create empty batch CSV")
    modes.add_argument("--race-id", help="one race, canonical numeric ID")
    ap.add_argument("--race-date")
    ap.add_argument("--venue")
    ap.add_argument("--venue-key")
    ap.add_argument("--race-number", type=int)
    ap.add_argument("--cars", type=int)
    ap.add_argument("--video-url", default="", help="public HTTPS YouTube watch URL")
    ap.add_argument("--analyze", action="store_true", help="actually run Gemini API")
    ap.add_argument("--confirm-api-cost", action="store_true",
                    help="allow paid/rate-limited Gemini API usage")
    ap.add_argument("--model", default=MODEL_DEFAULT)
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--max-api-calls", type=int, default=8)
    ap.add_argument("--auto-discover", action="store_true",
                    help="official YouTube API search for missing video URLs")
    ap.add_argument("--candidates-per-race", type=int, default=1)
    ap.add_argument("--max-searches", type=int, default=8)
    ap.add_argument("--export-research", action="store_true",
                    help="append complete consensus + independently sourced results to RESEARCH only")
    ap.add_argument("--official-results", type=Path,
                    help="independently obtained per-car official CSV under local_data/")
    args = ap.parse_args()
    try:
        if args.init_template:
            if args.analyze or args.export_research:
                raise ValueError("template creation cannot also perform analysis")
            dest = create_template(args.init_template)
            print(json.dumps({"status": "EMPTY_TEMPLATE_CREATED",
                              "file": str(dest), "api_calls": 0}, ensure_ascii=False))
            return 0
        if args.jobs:
            jobs = load_jobs(args.jobs)
        else:
            if None in (args.race_date, args.venue, args.venue_key,
                        args.race_number, args.cars):
                raise ValueError("single race requires date, venue, venue-key, number and cars")
            jobs = [parse_race({
                "race_id": args.race_id, "race_date": args.race_date,
                "venue": args.venue, "venue_key": args.venue_key,
                "race_number": str(args.race_number), "cars": str(args.cars),
                "youtube_url": args.video_url,
            })]
        if not re.fullmatch(r"gemini-[A-Za-z0-9.-]{2,60}", args.model):
            raise ValueError("invalid Gemini model ID")
        if not args.analyze:
            if args.confirm_api_cost or args.export_research:
                raise ValueError("API/export flags require --analyze")
            print(json.dumps({
                "status": "DRY_RUN_NO_API_CALL", "races": len(jobs),
                "model": args.model, "passes": args.passes, "workers": args.workers,
                "max_api_calls": args.max_api_calls,
                "race_jobs": [
                    {"race_id": j.race_id, "cars": j.cars,
                     "video_url": j.youtube_url or None,
                     "search_url": browser_search_url(
                         query_for_event(j.race_date, j.venue, j.race_number)
                     ) if not j.youtube_url else None}
                    for j in jobs
                ],
                "notes": "No search performed; no media fetched or sent; no training.",
            }, ensure_ascii=False, indent=2))
            return 0
        if not args.confirm_api_cost:
            raise ValueError("--analyze requires --confirm-api-cost")
        if args.export_research and not args.official_results:
            raise ValueError("--export-research requires --official-results")
        results = analyze_batch(
            jobs, model=args.model, passes=args.passes,
            workers=args.workers, max_api_calls=args.max_api_calls,
            auto_discover=args.auto_discover,
            candidates_per_race=args.candidates_per_race,
            max_searches=args.max_searches,
        )
        reports = results.pop("reports")
        if args.export_research:
            results["research_export"] = export_research(reports, args.official_results)
        print(json.dumps({
            "status": "GEMINI_BATCH_COMPLETED_WITH_GATES",
            **results,
            "reports": [{
                "race_id": r["race"]["race_id"],
                "url": r["youtube_url"],
                "report_file": str(report_path(parse_race(r["race"]), r["youtube_url"])),
                "status": r["status"],
                "identity_consensus": r["identity_consensus"],
                "all_metrics_consensus": r["all_metrics_consensus"],
                "model_verified": False,
            } for r in reports],
        }, ensure_ascii=False, indent=2))
        return 0 if not results["errors"] and not results["no_video_candidates"] else 2
    except (ValueError, OSError, TypeError, KeyError, csv.Error,
            json.JSONDecodeError) as exc:
        print("GEMINI_BATCH_NOT_READY:", type(exc).__name__ + ": " + str(exc)[:150],
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
