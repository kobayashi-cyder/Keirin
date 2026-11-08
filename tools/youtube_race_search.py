#!/usr/bin/env python3
"""Find YouTube keirin race-video LINKS using the official YouTube Data API.

Default mode only prints a YouTube browser-search URL, no key or network.
--api-search performs ONE official search.list request; no pagination,
downloading, playback, copying, frame extraction or AI video training.
Search results are unverified and not stored. Users may manually register
one reviewed video ID (link only) for the corresponding canonical race ID.
No API metadata gets copied into the research training set.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SEARCH_ENDPOINT = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_SEARCH = "https://www.youtube.com/results"
JST = ZoneInfo("Asia/Tokyo")
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
RACE_ID = re.compile(r"[0-9]{10,24}\Z")
HEADER = (
    "race_id", "race_date", "venue", "race_number", "video_id",
    "reviewed_at_jst", "review_status", "download_permission",
)
DEFAULT_INDEX = Path("local_data/history/youtube_race_links.csv")


def event(date_str, venue, race_number):
    try:
        parsed = date.fromisoformat(date_str)
    except (ValueError, TypeError) as exc:
        raise ValueError("race-date must be YYYY-MM-DD") from exc
    if parsed.isoformat() != date_str:
        raise ValueError("race-date must be canonical YYYY-MM-DD")
    if (not isinstance(venue, str) or not 1 <= len(venue) <= 25 or
            not venue.strip() or any(ch in venue for ch in "\r\n\t<>\"'\\/")):
        raise ValueError("venue should be a short racecourse name, e.g. 松山")
    if type(race_number) is not int or not 1 <= race_number <= 12:
        raise ValueError("race-number must be 1..12")
    return parsed, venue.strip(), race_number


def query_for_event(race_date, venue, race_number):
    day, venue, number = event(race_date, venue, race_number)
    return f"競輪 {day.year}年{day.month}月{day.day}日 {venue} {number}R レース映像"


def browser_search_url(query):
    return YOUTUBE_SEARCH + "?" + urlencode({"search_query": query})


def youtube_api_key(env_file=Path(".env")):
    # The existing .env is intentionally untracked, as with OpenAI/Discord.
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key and Path(env_file).is_file():
        if Path(env_file).stat().st_size > 65536:
            raise ValueError(".env is unexpectedly large")
        for line in Path(env_file).read_text(encoding="utf-8-sig").splitlines():
            key_name, separator, value = line.partition("=")
            if separator and key_name.strip() == "YOUTUBE_API_KEY":
                key = value.strip().strip("\"'").strip()
                break
    if not key:
        raise ValueError("YOUTUBE_API_KEY not configured; use the browser search link instead")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,160}", key):
        raise ValueError("YOUTUBE_API_KEY format invalid; key never printed")
    return key


def search_api(query, key, *, max_results=10, opener=urlopen):
    """Single public YouTube Data API search. Caller does not cache API data."""
    if type(max_results) is not int or not 1 <= max_results <= 15:
        raise ValueError("max-results must be 1..15")
    if not isinstance(query, str) or not 1 <= len(query) <= 150:
        raise ValueError("invalid search query")
    if not isinstance(key, str) or not key:
        raise ValueError("missing API key")
    params = {
        "part": "snippet", "type": "video", "q": query,
        "maxResults": str(max_results),
        "relevanceLanguage": "ja", "regionCode": "JP",
        "order": "relevance", "videoEmbeddable": "true",
        "key": key,
    }
    req = Request(
        SEARCH_ENDPOINT + "?" + urlencode(params),
        headers={"Accept": "application/json",
                 "User-Agent": "KeirinResearchYouTubeLinkFinder/0.1"},
        method="GET",
    )
    try:
        with opener(req, timeout=15) as response:
            if getattr(response, "status", 200) != 200:
                raise ValueError("YouTube API did not return HTTP 200")
            raw = response.read(512001)
        if len(raw) > 512000:
            raise ValueError("YouTube API response too large")
    except HTTPError as exc:
        # Never print the requested URL: query parameters contain the key.
        raise ValueError(f"YouTube Data API HTTP {exc.code}; check API key/quota") from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("YouTube Data API connection failed (credentials hidden)") from None
    try:
        document = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError("YouTube Data API did not return JSON") from None
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise ValueError("unexpected YouTube search result structure")
    result, seen = [], set()
    for item in document["items"][:max_results]:
        if not isinstance(item, dict):
            continue
        info = item.get("id")
        snippet = item.get("snippet")
        if not isinstance(info, dict) or not isinstance(snippet, dict):
            continue
        if info.get("kind") != "youtube#video":
            continue
        vid = info.get("videoId")
        if not isinstance(vid, str) or not VIDEO_ID.fullmatch(vid) or vid in seen:
            continue
        seen.add(vid)
        def brief(field, limit):
            value = snippet.get(field, "")
            return html.unescape(value).strip()[:limit] if isinstance(value, str) else ""
        result.append({
            "video_id": vid,
            "url": "https://www.youtube.com/watch?v=" + vid,
            "title": brief("title", 180),
            "channel": brief("channelTitle", 100),
            "published_at": brief("publishedAt", 40),
            "status": "UNVERIFIED_SEARCH_CANDIDATE",
            "video_content_downloaded": False,
            "training_ready": False,
        })
    return result


def local_index(path):
    path = Path(path)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "local_data":
        raise ValueError("index must stay within local_data/")
    if path.suffix.lower() != ".csv":
        raise ValueError("index must be a .csv file")
    return path


def register_reviewed_link(
    path, *, race_id, race_date, venue, race_number,
    video_id, confirmed=False, now=None,
):
    """Only a manually checked link, not video-derived data or usage rights."""
    path = local_index(path)
    event(race_date, venue, race_number)
    if not RACE_ID.fullmatch(race_id):
        raise ValueError("race-id must be numeric canonical Kdreams race ID")
    if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
        raise ValueError("invalid YouTube video ID")
    if confirmed is not True:
        raise ValueError("confirm that the video actually matches this race")
    if path.is_symlink():
        raise ValueError("index symlink rejected")
    records = []
    if path.exists():
        if not path.is_file() or path.stat().st_size > 1000000:
            raise ValueError("index invalid or too large")
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != list(HEADER):
                raise ValueError("index header incompatible")
            records = list(reader)
        if any(row.get("race_id") == race_id for row in records):
            raise ValueError("race already linked; no silent overwrite")
        if len(records) >= 5000:
            raise ValueError("link index at capacity")
    now = now or datetime.now(JST)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("review time needs a timezone")
    records.append({
        "race_id": race_id, "race_date": race_date, "venue": venue,
        "race_number": str(race_number), "video_id": video_id,
        "reviewed_at_jst": now.astimezone(JST).isoformat(),
        "review_status": "MANUALLY_CONFIRMED_LINK_ONLY",
        "download_permission": "NOT_ESTABLISHED",
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent,
            prefix="youtube_links_", suffix=".tmp", delete=False
        ) as output:
            tmp = Path(output.name)
            writer = csv.DictWriter(output, fieldnames=HEADER)
            writer.writeheader()
            writer.writerows(records)
        os.replace(tmp, path)
    finally:
        if tmp and tmp.exists():
            tmp.unlink()
    return {
        "status": "LINK_ONLY_REVIEWED_NOT_TRAINED",
        "race_id": race_id,
        "video_url": "https://www.youtube.com/watch?v=" + video_id,
        "index": str(path),
        "video_downloaded": False,
        "video_features_extracted": False,
        "training_allowed_by_link": False,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--race-date", required=True, help="race date YYYY-MM-DD")
    ap.add_argument("--venue", required=True, help="racecourse name, e.g. 松山")
    ap.add_argument("--race-number", type=int, required=True, help="1..12")
    ap.add_argument("--api-search", action="store_true",
                    help="use YouTube Data API (one quota-charged search request)")
    ap.add_argument("--max-results", type=int, default=10)
    ap.add_argument("--register-video-id",
                    help="explicitly manually reviewed YouTube 11-char video ID")
    ap.add_argument("--race-id", help="canonical numeric race ID for registration")
    ap.add_argument("--confirm-reviewed", action="store_true",
                    help="you checked video content and matched race identity")
    ap.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    args = ap.parse_args()
    try:
        query = query_for_event(args.race_date, args.venue, args.race_number)
        if args.register_video_id:
            if args.api_search or not args.race_id:
                raise ValueError("--register-video-id requires --race-id and cannot combine --api-search")
            report = register_reviewed_link(
                args.index, race_id=args.race_id,
                race_date=args.race_date, venue=args.venue,
                race_number=args.race_number,
                video_id=args.register_video_id, confirmed=args.confirm_reviewed
            )
        else:
            if args.race_id or args.confirm_reviewed:
                raise ValueError("race-id/confirm-reviewed only apply to link registration")
            report = {
                "status": "SEARCH_LINK_ONLY_NO_API_REQUEST",
                "query": query,
                "browser_search_url": browser_search_url(query),
                "videos_downloaded": 0,
                "model_trained": False,
            }
            if args.api_search:
                key = youtube_api_key()
                candidates = search_api(query, key, max_results=args.max_results)
                report["status"] = "UNVERIFIED_YOUTUBE_VIDEO_CANDIDATES"
                report["candidate_count"] = len(candidates)
                report["candidates"] = candidates
                report["note"] = "No automatic race matching. Review on YouTube; links are NOT video training.",
    except (ValueError, TypeError, OSError, csv.Error) as exc:
        # Do not expose API request, URL containing key, media or credentials.
        print("YOUTUBE_LOOKUP_REJECTED:", type(exc).__name__ + ": " + str(exc)[:180],
              file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
