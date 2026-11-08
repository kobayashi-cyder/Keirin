#!/usr/bin/env python3
"""Single-page, 10-second *maximum frequency* public browser odds monitor.

Explicit authorization is required because a web page being viewable does not
grant reuse or automated access permission. Honors robots and throttling.
The watcher never posts picks, submits wagers, or invokes model/API/Discord.
No captcha/authentication bypass, proxy rotation, or hidden endpoint calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

from odds_rank_parser import parse_ranked_odds

JST = ZoneInfo("Asia/Tokyo")
USER_AGENT = "KeirinResearchMonitor/0.1 (permission-required; single-page)"
SAFE_HOSTS = {"keirin.kdreams.jp"}
MIN_INTERVAL_SECONDS = 10.0


class StopMonitoring(Exception):
    """Permanent policy, response, validation or expiry failure."""


def validate_target(url):
    p = urlsplit(url)
    if (p.scheme != "https" or p.hostname not in SAFE_HOSTS or
            p.username is not None or p.password is not None or
            not re.fullmatch(r"/[a-z0-9-]+/racedetail/\d+/", p.path) or
            (p.query and p.query != "pageType=odds") or p.fragment):
        raise StopMonitoring("Only an HTTPS public Kdreams race-detail odds page is accepted")
    return p.hostname


def read_robots(url, user_agent=USER_AGENT):
    host = validate_target(url)
    robots_url = f"https://{host}/robots.txt"
    request = Request(robots_url, headers={"User-Agent": user_agent, "Accept": "text/plain"})
    try:
        with urlopen(request, timeout=10) as response:
            if response.status != 200 or urlsplit(response.geturl()).hostname != host:
                raise StopMonitoring("robots policy could not be verified")
            data = response.read(256_001)
            if len(data) > 256_000:
                raise StopMonitoring("robots file unexpectedly large")
    except HTTPError as exc:
        if exc.code in (401, 403, 429) or exc.code >= 500:
            raise StopMonitoring(f"robots policy unavailable: HTTP {exc.code}") from exc
        if exc.code == 404:
            # Not having a robots file does NOT grant license to copy.
            return {"robots": "not_published", "crawl_delay": None}
        raise StopMonitoring(f"robots check HTTP {exc.code}") from exc
    except (OSError, URLError) as exc:
        raise StopMonitoring(f"robots check failed: {type(exc).__name__}") from exc
    rp = RobotFileParser(robots_url)
    rp.parse(data.decode("utf-8", errors="replace").splitlines())
    if not rp.can_fetch(user_agent, url):
        raise StopMonitoring("robots.txt disallows this page for the monitor")
    return {"robots": "allowed_by_robots_only", "crawl_delay": rp.crawl_delay(user_agent)}


def jst_timestamp(value):
    t = datetime.fromisoformat(value)
    if t.tzinfo is None or t.utcoffset() is None:
        raise StopMonitoring("deadline requires an explicit +09:00 UTC offset")
    return t.astimezone(JST)


def checked_interval(interval, crawl_delay=None):
    try:
        interval = float(interval)
    except (TypeError, ValueError) as exc:
        raise StopMonitoring("interval must be numeric") from exc
    if not MIN_INTERVAL_SECONDS <= interval <= 300.0:
        raise StopMonitoring("interval must be 10 to 300 seconds")
    if crawl_delay is None:
        return interval
    return max(interval, float(crawl_delay))


def ensure_not_closed(deadline, *, now=None):
    now = now or datetime.now(JST)
    if now.tzinfo is None:
        raise StopMonitoring("timezone-aware clock required")
    if now >= deadline:
        raise StopMonitoring("Race deadline reached; no further requests")


def _safe_output_path(path):
    path = Path(path)
    # By default, our output is only under the gitignored local_data folder.
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "local_data":
        raise StopMonitoring("Output must stay under local_data/ within the repository")
    return path


def record_if_changed(path, record, previous_digest):
    """Local JSONL of *parsed facts only*, not raw HTML, page text or cookies."""
    record = dict(record)
    key = json.dumps({"markets": record["markets"]}, sort_keys=True, ensure_ascii=False,
                     allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(key).hexdigest()
    if digest == previous_digest:
        return previous_digest, False
    record["content_sha256"] = digest
    path = _safe_output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
    return digest, True


def monitor(url, deadline, *, interval=10, cars=7, cycles=60, output=None,
            confirmed_permission=False, robots_checker=read_robots):
    """Poll a single browser page. Rate is global, never '10 seconds per market'.

    Caller must have obtained authorization for this specific use and rate.
    The site may forbid it despite a robots allow rule; this code does not
    decide licensing. Fail closed on policy or access errors.
    """
    host = validate_target(url)
    if not confirmed_permission:
        raise StopMonitoring("Kdreams content-use permission is not confirmed. Monitoring disabled.")
    if not 1 <= cycles <= 720:
        raise StopMonitoring("cycles must be between 1 and 720")
    if cars not in (7, 8, 9):
        raise StopMonitoring("Current rank-parser handles 7, 8 or 9 cars only")
    if output is None:
        output = Path("local_data/odds_snapshots") / (urlsplit(url).path.rstrip("/").split("/")[-1] + ".jsonl")
    output = _safe_output_path(output)
    ensure_not_closed(deadline)
    policy = robots_checker(url)
    minimum_interval = checked_interval(interval, policy["crawl_delay"])
    from playwright.sync_api import sync_playwright

    count, previous_digest = 0, None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=USER_AGENT, service_workers="block")
            page = context.new_page()
            # Limit incidental third-party and media requests. Do not block
            # first-party scripts which may be needed for odds rendering.
            def resource_policy(route):
                request = route.request
                name = urlsplit(request.url).hostname or ""
                if (name != host or request.resource_type in ("image", "media", "font")):
                    route.abort()
                else:
                    route.continue_()
            page.route("**/*", resource_policy)
            next_allowed = time.monotonic()
            for index in range(cycles):
                ensure_not_closed(deadline)
                pause = next_allowed - time.monotonic()
                if pause > 0:
                    time.sleep(pause)
                ensure_not_closed(deadline)
                start = time.monotonic()
                next_allowed = start + minimum_interval
                try:
                    response = page.goto(url, wait_until="domcontentloaded",
                                         timeout=12_000)
                    if response is None:
                        raise StopMonitoring("No HTTP response")
                    status = response.status
                    if status in (401, 403, 429):
                        raise StopMonitoring(f"HTTP {status}: stop, do not retry or evade")
                    if status >= 500:
                        raise StopMonitoring(f"HTTP {status}: stop (server error)")
                    if status != 200 or urlsplit(page.url).hostname != host:
                        raise StopMonitoring(f"HTTP {status} or redirect: stop")
                    visible = page.locator("body").inner_text(timeout=8000)
                    if any(flag in visible for flag in ("CAPTCHA", "captcha", "ロボット認証")):
                        raise StopMonitoring("Access challenge; stop without attempting bypass")
                    snapshot = parse_ranked_odds(visible, cars)
                    if snapshot["status"] != "PARSED_RANKED_MARKETS":
                        print(f"[{index+1}/{cycles}] NOT_PARSED: {snapshot.get('detail','layout change')}", flush=True)
                        # Page layout changed or required markets missing.
                        raise StopMonitoring("Layout unknown; stop instead of inventing prices")
                    timestamp = datetime.now(JST).isoformat()
                    record = {
                        "source_url": url, "observed_at_jst": timestamp,
                        "deadline_at_jst": deadline.isoformat(),
                        "cars": cars, "markets": snapshot["markets"],
                        "page_displayed_times_jst": snapshot["page_displayed_times_jst"],
                        "authorization": "operator_confirmed_use_and_polling",
                        "quality": "unverified_page_parser",
                    }
                    previous_digest, changed = record_if_changed(
                        output, record, previous_digest
                    )
                    count += 1
                    print(f"[{index+1}/{cycles}] {timestamp} parsed={len(record['markets'])} "
                          f"changed={changed} saved={output}", flush=True)
                except StopMonitoring:
                    raise
                except Exception as exc:
                    # Non-200 or transient errors are not retried automatically.
                    raise StopMonitoring(f"Browser/parser failed: {type(exc).__name__}: {str(exc)[:150]}") from exc
        finally:
            browser.close()
    return {"status": "FINISHED", "successful_checks": count, "output": str(output)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True, help="single public race odds page")
    ap.add_argument("--deadline-jst", required=True, help="e.g. 2026-11-08T20:20:00+09:00")
    ap.add_argument("--cars", type=int, choices=[7, 8, 9], default=7)
    ap.add_argument("--interval", type=float, default=10)
    ap.add_argument("--cycles", type=int, default=60, help="max browser checks")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--permission-confirmed", action="store_true",
                    help="confirm site operator permits your intended monitoring frequency and local use")
    args = ap.parse_args()
    try:
        result = monitor(args.url, jst_timestamp(args.deadline_jst),
                         interval=args.interval, cars=args.cars,
                         cycles=args.cycles, output=args.output,
                         confirmed_permission=args.permission_confirmed)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except StopMonitoring as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
