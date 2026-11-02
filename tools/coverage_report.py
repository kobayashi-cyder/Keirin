#!/usr/bin/env python3
"""Build an auditable date × venue × race coverage report (no web access).

Run:
  python tools/coverage_report.py

A day without a schedule inventory is UNKNOWN, never "no races".
Multiple winning combinations from dead heats count as one race.
Only odds with contiguous ranks 1..50 or 1..100 qualify as completed.
"""
import csv
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
START = dt.date(2020, 1, 1)


def records(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def parse_id(race_id):
    parts = race_id.split("_")
    if len(parts) != 3 or len(parts[0]) != 8 or not parts[2].isdigit():
        raise ValueError(f"bad race ID: {race_id}")
    date = dt.datetime.strptime(parts[0], "%Y%m%d").date()
    return date, parts[1], int(parts[2])


def load_results():
    winners = defaultdict(dict)
    source_files = set()
    for path in sorted(DATA.glob("*.csv")):
        rows = records(path)
        if not rows or "winning_combination" not in rows[0]:
            continue
        source_files.add(path.name)
        for row in rows:
            rid, combo = row["race_id"], row["winning_combination"]
            parse_id(rid)
            payout = int(row["payout_per_100yen"])
            odd = float(row["winning_final_odds"])
            if payout <= 0 or abs(payout / 100 - odd) > .011:
                raise ValueError(f"invalid payout: {rid} {combo}")
            if combo in winners[rid]:
                if winners[rid][combo] != payout:
                    raise ValueError(f"conflicting repeated result: {rid} {combo}")
            else:
                winners[rid][combo] = payout
    return winners, sorted(source_files)


def load_odds():
    odds = defaultdict(dict)
    files = set()
    for path in sorted(DATA.glob("*.csv")):
        rows = records(path)
        if not rows or not {"race_id", "rank", "combination"} <= rows[0].keys():
            continue
        files.add(path.name)
        for row in rows:
            rid, rank = row["race_id"], int(row["rank"])
            parse_id(rid)
            if rank < 1:
                raise ValueError(f"invalid rank: {rid}")
            combo = row["combination"]
            odd = float(next((row[k] for k in ("final_odds", "displayed_odds", "odds") if row.get(k)), "nan"))
            if not (odd > 0 and odd < float("inf")):
                raise ValueError(f"invalid odds: {rid} #{rank}")
            previous = odds[rid].get(rank)
            value = (combo, odd)
            if previous is not None and previous != value:
                raise ValueError(f"conflicting odds: {rid} #{rank}")
            odds[rid][rank] = value
    return odds, sorted(files)


def ranks_complete(group, upto):
    return all(i in group for i in range(1, upto + 1)) and len({
        group[i][0] for i in range(1, upto + 1)
    }) == upto


def load_cancelled():
    """Cancelled race IDs are terminal outcomes with no winning ticket or odds."""
    path = DATA / "cancelled_races.csv"
    if not path.exists():
        return set()
    races = set()
    for row in records(path):
        rid = row["race_id"]
        parse_id(rid)
        if row["status"] != "cancelled" or not row.get("source_url"):
            raise ValueError(f"Invalid cancelled-race entry: {rid}")
        if rid in races:
            raise ValueError(f"Duplicate cancelled race: {rid}")
        races.add(rid)
    return races


def load_schedule():
    """Only venue-day entries explicitly present in stored inventories."""
    entries = {}
    for path in sorted(DATA.glob("venue_day_coverage*.csv")):
        for row in records(path):
            date = dt.date.fromisoformat(row["date"])
            venue = row["venue_slug"]
            key = (date, venue)
            total = int(row["listed_races"])
            if key in entries and entries[key] != total:
                raise ValueError(f"conflicting schedule inventory: {key}")
            entries[key] = total
    return entries


def report():
    today = dt.datetime.now(ZoneInfo("Asia/Tokyo")).date()
    winners, result_files = load_results()
    odds, odds_files = load_odds()
    schedule = load_schedule()
    cancelled = load_cancelled()
    if cancelled.intersection(winners):
        raise ValueError("Cancelled race has a settled winner")
    if cancelled.intersection(odds):
        raise ValueError("Cancelled race has populated odds")
    for rid in cancelled:
        day, venue, race = parse_id(rid)
        if (day, venue) not in schedule or not 1 <= race <= schedule[(day, venue)]:
            raise ValueError(f"Cancelled race absent from schedule: {rid}")

    # Every recorded race has unique identity, even with multiple payouts.
    per_day = defaultdict(lambda: {"results": set(), "odds": set(), "top50": set(), "top100": set()})
    per_venue_day = defaultdict(lambda: {"results": set(), "odds": set()})
    for rid in winners:
        date, venue, race = parse_id(rid)
        per_day[date]["results"].add(rid)
        per_venue_day[(date, venue)]["results"].add(race)
    for rid, group in odds.items():
        date, venue, race = parse_id(rid)
        per_day[date]["odds"].add(rid)
        per_venue_day[(date, venue)]["odds"].add(race)
        if ranks_complete(group, 50):
            per_day[date]["top50"].add(rid)
        if ranks_complete(group, 100):
            per_day[date]["top100"].add(rid)

    known_listed_races = sum(schedule.values())
    known_results = sum(len(per_venue_day[key]["results"]) for key in schedule)
    known_cancelled = len(cancelled)
    overcounts = [(str(k[0]), k[1]) for k, total in schedule.items()
                  if len(per_venue_day[k]["results"]) > total]
    if overcounts:
        raise ValueError(f"more results than scheduled: {overcounts}")
    # Build a deterministic, race-level work queue. A missing rank is not
    # treated as a zero-odds bet; it remains an explicit open task.
    pending = []
    for (day, venue), expected in sorted(schedule.items()):
        for race in range(1, expected + 1):
            rid = f"{day:%Y%m%d}_{venue}_{race:02d}"
            if rid in cancelled:
                continue
            group = odds.get(rid, {})
            has_result = rid in winners
            fifty = ranks_complete(group, 50)
            hundred = ranks_complete(group, 100)
            if has_result and hundred:
                continue
            pending.append({
                "race_id": rid,
                "date": str(day),
                "venue_slug": venue,
                "race_number": race,
                "result_recorded": int(has_result),
                "odds_rows": len(group),
                "top50_complete": int(fifty),
                "top100_complete": int(hundred),
                "next_action": ("collect_result" if not has_result else
                                "collect_first_odds" if not group else
                                "collect_51_to_100" if fifty else
                                "complete_top50"),
                "source_schedule_url": f"https://keirin.kdreams.jp/kaisai/{day:%Y/%m/%d}/",
            })
    with (DATA / "pending_races.csv").open("w", encoding="utf-8", newline="") as f:
        if pending:
            writer = csv.DictWriter(f, fieldnames=list(pending[0]))
            writer.writeheader()
            writer.writerows(pending)

    date_rows = []
    date = START
    while date <= today:
        venues = [key for key in schedule if key[0] == date]
        p = per_day[date]
        date_rows.append({
            "date": str(date),
            "schedule_status": "partial_or_unverified" if venues else "not_indexed",
            "venue_days_listed": len(venues),
            "listed_races": sum(schedule[k] for k in venues),
            "result_races": len(p["results"]),
            "cancelled_races": sum(parse_id(rid)[0] == date for rid in cancelled),
            "odds_races": len(p["odds"]),
            "top50_races": len(p["top50"]),
            "top100_races": len(p["top100"]),
        })
        date += dt.timedelta(days=1)

    missing_known = known_listed_races - known_results - known_cancelled
    result_total = len(winners)
    odds_race_total = len(odds)
    points = sum(len(x) for x in odds.values())
    top50_total = sum(ranks_complete(g, 50) for g in odds.values())
    top100_total = sum(ranks_complete(g, 100) for g in odds.values())

    stats = {
        "range_start": str(START), "range_end": str(today),
        "calendar_days": len(date_rows),
        "dates_with_schedule_entries": len({d for d, _ in schedule}),
        "schedule_venue_days": len(schedule),
        "known_listed_races": known_listed_races,
        "known_result_races": known_results,
        "known_cancelled_races": known_cancelled,
        "known_resolved_races": known_results + known_cancelled,
        "known_missing_result_races": missing_known,
        "known_missing_result_or_status_races": missing_known,
        "known_pending_top100_races": len(pending),
        "all_result_races": result_total,
        "all_winning_payout_rows": sum(len(x) for x in winners.values()),
        "odds_races": odds_race_total,
        "individual_odds_points": points,
        "top50_races": top50_total,
        "top100_races": top100_total,
        "result_source_files": result_files,
        "odds_source_files": odds_files,
        "note": "Cancelled races have no winning combination, odds, or payout; counts are separate. "
                "Unknown schedule days are NOT counted as days without racing. "
                "Top100 is not inferred from the first 50 ranked odds.",
    }
    (DATA / "date_index.csv").parent.mkdir(parents=True, exist_ok=True)
    with (DATA / "date_index.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(date_rows[0]))
        writer.writeheader()
        writer.writerows(date_rows)
    (DATA / "coverage_summary.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    known_dates = sorted({d for d, _ in schedule})
    lines = [
        "# 競輪データ収集進捗（自動生成）", "",
        "このレポートは既存CSVから再計算します。新しいWeb取得の成功を意味しません。",
        f"対象期間：{START}〜{today}（{len(date_rows):,}暦日）",
        "",
        f"- 確定結果：**{result_total}レース**（払戻行 {stats['all_winning_payout_rows']}件。同着は複数行）",
        f"- 個別3連単オッズ：**{points}点 / {odds_race_total}レース**",
        f"- Top50完了：**{top50_total}レース** / Top100完了：**{top100_total}レース**",
        f"- 開催会場日を列挙済み：**{len(schedule)}会場日・{len(known_dates)}日分**",
        f"- 列挙済み会場日の結果・状態未解決：**{missing_known}レース**（{known_listed_races}中、確定結果{known_results}件・中止{known_cancelled}件）",
        f"- 開催一覧が未索引の日：**{len(date_rows)-len(known_dates)}日**（開催なしとは扱わない）",
        "",
        "## 日付別の既知の残件", "",
        "| 日付 | 登録会場日 | 登録予定レース | 結果収録 | 中止 | 未解決 |", "|---|---:|---:|---:|---:|---:|"
    ]
    for d in known_dates:
        keys = [key for key in schedule if key[0] == d]
        expected = sum(schedule[k] for k in keys)
        collected = sum(len(per_venue_day[k]["results"]) for k in keys)
        cancelled_on_date = sum(parse_id(rid)[0] == d for rid in cancelled)
        lines.append(f"| {d} | {len(keys)} | {expected} | {collected} | {cancelled_on_date} | {expected-collected-cancelled_on_date} |")
    lines += [
        "", "## 検証方針", "",
        "- 収集単位は日付→その日実際に開催された全会場→会場内の全レース。",
        "- 日付の会場一覧を確認するまでは、欠損会場数は算出しない。",
        "- 索引済みレースの結果・状態未解決を先に解消し、残件がある間は新規日付・会場を索引に追加しない。",
        "- 同着の複数的中は一レース・複数払戻として集計。",
        "- 中止レースは `data/cancelled_races.csv` に分離し、的中組番・倍率を捏造せず未解決件数から除外。",
        "- 本報告の収録数はデータ形式と重複の確認であり、全ソースの独立照合を保証しない。",
        "- 第三者サイトの大規模複製・公開は利用条件を確認し、必要な許諾を得てから行う。",
        ""
    ]
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "coverage.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: stats[k] for k in (
        "calendar_days", "schedule_venue_days", "known_missing_result_races",
        "all_result_races", "individual_odds_points", "top50_races", "top100_races"
    )}, ensure_ascii=False))


if __name__ == "__main__":
    report()
