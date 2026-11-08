# Web scraping test monitor: one race every >=10 seconds (Windows)

## What this code actually does

- Uses a fresh Chromium browser via Playwright to read a single PUBLIC race-detail odds page, after **the user acknowledges the published terms and accepts responsibility for their own use**. This does NOT confirm operator permission.
- Before opening the page, checks the site's `robots.txt`. A disallow, block, unclear robots response or site challenge stops the program. robots.txt allowance is **not** a license.
- One race page **globally** every at least 10 seconds, or a longer robots `Crawl-delay` if specified. Not 10 seconds per race; do not run multiple instances against the same site.
- Stops on 401/403/429/5xx, redirect off site, unknown page format, CAPTCHA or the declared race deadline. Does not bypass authentication or rotate IPs.
- Parses ONLY explicitly visible ranked odds for the configured 7-, 8- or 9-car market order; saves **a small JSONL record of numerical facts** locally if any quote changed. Partial markets remain `complete=false`. Does not save HTML, cookies or the entire page.
- Does **not** calculate winning probabilities or issue 130% investment recommendations. No OpenAI API, Discord API or real bet interface is contacted.

**Important:** Kdreams restricts unauthorized copying and reuse in its terms: https://keirin.kdreams.jp/info/sitepolicy/ . No operator license has been verified for this project. User assumption of responsibility is not a grant of permission and does not guarantee the activity is contractually or legally permitted. The launcher now asks for `I_AGREE` as a **user risk acknowledgment only**. There is no false `permission confirmed` claim. The program retains robots.txt restrictions, a minimum 10-second interval per site, single-race scope, and stops on blocks or challenges. If robots or site policy disallow access, do not circumvent them.

The live Kdreams page parser still needs verification. If it stops with `UNRECOGNISED_LAYOUT`, the program has not proved that it can extract all seven markets correctly. This change does not enable predictions or Discord notifications.

## Windows setup

Download the current `main` branch of the GitHub repository, then open a command prompt in its root. Install Playwright:

```cmd
py -3 -m pip install playwright
py -3 -m playwright install chromium
```

Or run `setup_browser_monitor.bat`.

No OpenAI/Discord keys are required.

## Tests without accessing the website

```cmd
py -3 -m unittest discover -s tests -p "test_browser_odds_watch.py" -v
```

These tests use synthetic rendered text and never open a browser. They check both 7-car and 9-car market list parsing and 10-second minimum interval.

## Example of user-acknowledged local research monitoring

Choose an **upcoming** race and read its actual voting deadline, e.g. 20:20 JST. Do not use the historical 熊本 URL from 2026-11-08 once that race has closed.

```cmd
py -3 tools\browser_odds_watch.py --url "https://keirin.kdreams.jp/PLACE/racedetail/REAL_ID/?pageType=odds" --deadline-jst "2026-11-08T20:20:00+09:00" --cars 7 --interval 10 --cycles 60 --acknowledge-terms-and-risks
```

The example URL and deadline are placeholders; use an actual upcoming race and real deadline. Operator consent remains unverified.

The command reads **no more than 60 pages**, spaced at least 10 seconds apart, then stops. It does not run as a background service. The actual time between page loads may be longer, including site response delays or robots `Crawl-delay`.

`run_10s_monitor.bat` guides you through the same inputs; the operation still requires site permission.

Parsed snapshots are stored at `local_data/odds_snapshots/<race_id>.jsonl`. Git ignores `local_data/` to avoid publishing third-party data.

## Real-world parser limitations

This parser expects lists of "人気順" tickets whose pattern is approximately `1 5-7-1 4.3`. Some sites use rows split across tabs or scripts, and the live Kdreams DOM has **not yet been successfully verified** with the program.

If the layout or market order changes, `UNRECOGNISED_LAYOUT` stops the monitor: it must not misattribute a 2車単 quote to another bet type. Incomplete lists do not become invented full markets.

The old snapshot URL, with the betting closed, is not a meaningful 10-second monitoring target. 8-car pages are also supported by the parser's count checks, but the site-specific live DOM still needs verification.

The next engineering step is to inspect a permitted, real pre-close page's structured market rows and harden the parser with deterministic tests. No hypothetical "130% edge" should be sent to Discord until a real calibrated probability model and up-to-date odds pass the research validation gate.

## References

- Kdreams site policy: https://keirin.kdreams.jp/info/sitepolicy/
- Kdreams page example: https://keirin.kdreams.jp/kumamoto/racedetail/8720261106030004/?pageType=odds
- Current data requirements: docs/DATA_PROVIDER_PLAN_20261108.md
