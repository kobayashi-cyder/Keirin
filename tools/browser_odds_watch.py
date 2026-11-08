#!/usr/bin/env python3
"""Single-page, 10-second *maximum frequency* public browser odds monitor.

Local research opt-in is required; acknowledgment does not establish legal
permission. The operator's terms still apply. Honors robots and throttling.
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
from odds_html_parser import text_from_html
from odds_3rentan_matrix import recover_three_rentan_matrix, diagnose_three_rentan_structure

JST = ZoneInfo("Asia/Tokyo")
USER_AGENT = "KeirinResearchMonitor/0.2 (user-acknowledged; single-page)"
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
            content_type = response.headers.get("Content-Type", "")
            data = response.read(256_001)
            if len(data) > 256_000:
                raise StopMonitoring("robots file unexpectedly large")
    except HTTPError as exc:
        if exc.code in (401, 403, 429) or exc.code >= 500:
            raise StopMonitoring(f"robots policy unavailable: HTTP {exc.code}") from exc
        if exc.code == 404:
            # Not having a robots file does NOT grant license to copy.
            return {"robots": "not_published_http_404", "crawl_delay": None}
        raise StopMonitoring(f"robots check HTTP {exc.code}") from exc
    except (OSError, URLError) as exc:
        raise StopMonitoring(f"robots check failed: {type(exc).__name__}") from exc
    return classify_robots_body(data, content_type, robots_url, url, user_agent)


def classify_robots_body(data, content_type, robots_url, target_url, user_agent):
    """An HTML page at /robots.txt is not an allow or a disallow directive.

    Never pretend this is a valid robots policy. A *real* Disallow remains
    authoritative and blocks monitoring. HTTP 403/429 and challenges are
    handled separately before this function is reached.
    """
    content = data.decode("utf-8", errors="replace").lstrip("\ufeff \t\r\n")
    if content[:256].lower().startswith(("<!doctype html", "<html", "<?xml")):
        # There is no interpretable robots policy. This does not confer
        # authorization; the caller must separately acknowledge the terms.
        return {"robots": "not_robots_html", "crawl_delay": None}
    if not content:
        raise StopMonitoring("Empty robots document: monitoring paused")
    rp = RobotFileParser(robots_url)
    rp.parse(content.splitlines())
    if not rp.can_fetch(user_agent, target_url):
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
            risk_acknowledged=False, robots_checker=read_robots):
    """Poll a single browser page. Rate is global, never '10 seconds per market'.

    The user must acknowledge the published terms and assume responsibility.
    This does not claim operator permission. A robots allow rule is also not
    permission; the operator may restrict automated collection. Fail closed
    on site access restrictions and parser errors.
    """
    host = validate_target(url)
    if not risk_acknowledged:
        raise StopMonitoring("You must acknowledge site terms/risks before local monitoring.")
    if not 1 <= cycles <= 720:
        raise StopMonitoring("cycles must be between 1 and 720")
    if cars not in (7, 8, 9):
        raise StopMonitoring("Current rank-parser handles 7, 8 or 9 cars only")
    if output is None:
        output = Path("local_data/odds_snapshots") / (urlsplit(url).path.rstrip("/").split("/")[-1] + ".jsonl")
    output = _safe_output_path(output)
    ensure_not_closed(deadline)
    policy = robots_checker(url)
    if policy["robots"] == "not_robots_html":
        print("WARNING: /robots.txt returned a regular HTML page, not robots rules. "
              "This is not permission. You accepted responsibility for the "
              "site's published restrictions. Valid robots Disallow rules "
              "are still enforced when available.", flush=True)
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
                    ensure_not_closed(deadline)
                    if "確定オッズ" in visible:
                        raise StopMonitoring("Final odds already shown; no more pre-close monitoring")
                    if any(flag in visible for flag in ("CAPTCHA", "captcha", "ロボット認証")):
                        raise StopMonitoring("Access challenge; stop without attempting bypass")
                    snapshot = parse_ranked_odds(visible, cars)
                    parsed_from = "rendered_text"
                    if snapshot["status"] != "PARSED_RANKED_MARKETS":
                        # The browser already fetched an HTML page. Parse it
                        # in memory rather than issuing a second HTTP request.
                        html_text = text_from_html(page.content())
                        alternative = parse_ranked_odds(html_text, cars)
                        if alternative["status"] == "PARSED_RANKED_MARKETS":
                            snapshot = alternative
                            parsed_from = "html_structure"
                    if snapshot["status"] != "PARSED_RANKED_MARKETS":
                        # Diagnose DOM visibility without writing HTML, cookies,
                        # credentials, or odds to disk.
                        full_dom_text = page.locator("body").text_content(timeout=8000) or ""
                        metrics = {
                            "recognized_ranked_blocks": snapshot.get("ranked_blocks"),
                            "expected_market_blocks": snapshot.get("expected_blocks"),
                            "visible_popularity_mentions": visible.count("人気順"),
                            "dom_popularity_mentions": full_dom_text.count("人気順"),
                            "visible_body_chars": len(visible),
                            "dom_text_chars": len(full_dom_text),
                            "detail": snapshot.get("detail", "layout change"),
                        }
                        print("LAYOUT_DIAGNOSTICS: " + json.dumps(
                            metrics, ensure_ascii=False, sort_keys=True
                        ), flush=True)
                        print(f"[{index+1}/{cycles}] NOT_PARSED: "
                              f"{snapshot.get('detail','layout change')}", flush=True)
                        # Page layout changed or required markets missing.
                        raise StopMonitoring("Layout unknown; stop instead of inventing prices")
                    # The ranked 3rentan list often contains only 50/210 tickets.
                    # The page may also embed full numeric odds matrices grouped
                    # by first-place car. Recover ONLY all tickets, with every
                    # ranked quote crosschecked; else keep the original top 50.
                    matrix_status = "not_applicable"
                    ranked_market = snapshot["markets"].get("3rentan")
                    if ranked_market is not None and not ranked_market["complete"]:
                        full_odds, matrix_status = recover_three_rentan_matrix(
                            page.content(), cars, ranked_market["quotes"]
                        )
                        if full_odds is not None:
                            ranked_market["quotes"] = full_odds
                            ranked_market["count"] = len(full_odds)
                            ranked_market["complete"] = (
                                len(full_odds) == ranked_market["expected_count"]
                            )
                            parsed_from += "+crosschecked_3rentan_matrix"
                        else:
                            print("3RENTAN_MATRIX_NOT_VERIFIED: " + matrix_status,
                                  flush=True)
                            fingerprint = diagnose_three_rentan_structure(
                                page.content(), cars
                            )
                            print("3RENTAN_DOM_FINGERPRINT: " + json.dumps(
                                fingerprint, ensure_ascii=False, sort_keys=True
                            ), flush=True)
                    # Never log an odds observation after the declared closing time.
                    ensure_not_closed(deadline)
                    timestamp = datetime.now(JST).isoformat()
                    record = {
                        "source_url": url, "observed_at_jst": timestamp,
                        "deadline_at_jst": deadline.isoformat(),
                        "cars": cars, "markets": snapshot["markets"],
                        "parser_method": parsed_from,
                        "three_rentan_matrix_verification": matrix_status,
                        "page_displayed_times_jst": snapshot["page_displayed_times_jst"],
                        "consent_basis": "user_acknowledged_terms_and_risks_not_operator_approval",
                        "quality": "unverified_page_parser",
                    }
                    previous_digest, changed = record_if_changed(
                        output, record, previous_digest
                    )
                    count += 1
                    print(f"[{index+1}/{cycles}] {timestamp} parsed={len(record['markets'])} "
                          f"source={parsed_from} changed={changed} saved={output}", flush=True)
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
    ap.add_argument("--acknowledge-terms-and-risks", action="store_true",
                    help="acknowledge Kdreams use restrictions and responsibility; NOT operator permission")
    args = ap.parse_args()
    try:
        result = monitor(args.url, jst_timestamp(args.deadline_jst),
                         interval=args.interval, cars=args.cars,
                         cycles=args.cycles, output=args.output,
                         risk_acknowledged=args.acknowledge_terms_and_risks)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except StopMonitoring as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
