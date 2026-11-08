#!/usr/bin/env python3
"""Forward-only public GitHub paper ticket ledger. Never handles real funds.

Inputs are v0.6 paper_bet.py proposals, not historical selections.
All accepted records use the actual time of ingestion, not an input timestamp.
"""
import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

JST = ZoneInfo('Asia/Tokyo')
GENESIS = '0' * 64
HOSTS = {'keirin.jp', 'www.keirin.jp'}


def canonical(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def stamp(value):
    t = datetime.fromisoformat(value)
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError('naive timestamp')
    return t.astimezone(JST)


def official(url):
    try:
        u = urlparse(url)
        return u.scheme == 'https' and u.hostname in HOSTS
    except (TypeError, ValueError):
        return False


def load_journal(path):
    p = Path(path)
    if not p.exists():
        return []
    prev = GENESIS
    records = []
    for index, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
        record = json.loads(line)
        digest = record.pop('digest')
        if (record.get('seq') != index or record.get('prev_hash') != prev or
                hashlib.sha256(canonical(record).encode()).hexdigest() != digest):
            raise ValueError(f'journal hash chain broken at line {index}')
        record['digest'] = digest
        records.append(record)
        prev = digest
    return records


def append(records, kind, payload, input_file, now):
    item = {'seq': len(records) + 1, 'prev_hash': records[-1]['digest'] if records else GENESIS,
            'kind': kind, 'ingested_at_jst': now.isoformat(), 'input_file': input_file,
            'payload': payload}
    item['digest'] = hashlib.sha256(canonical(item).encode()).hexdigest()
    records.append(item)


def validated_bet(p, now, accepted, model_allowlist):
    if not isinstance(p, dict) or p.get('status') != 'PAPER_BET':
        raise ValueError('not a paper-bet proposal')
    rid, mode, ticket = p.get('race_id'), p.get('mode'), p.get('ticket')
    if not isinstance(rid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{4,100}', rid):
        raise ValueError('invalid race ID')
    if mode not in ('baseline', 'edge') or not isinstance(ticket, dict):
        raise ValueError('invalid strategy')
    kind, combo = ticket.get('bet_type'), ticket.get('combo')
    if kind not in ('2shafuku', '3rentan') or not isinstance(combo, str):
        raise ValueError('invalid market/combination')
    nums = combo.split('-')
    if len(nums) != (2 if kind == '2shafuku' else 3) or not all(x.isdigit() and 1 <= int(x) <= 9 for x in nums) or len(set(nums)) != len(nums):
        raise ValueError('invalid combination')
    if kind == '2shafuku' and list(map(int, nums)) != sorted(map(int, nums)):
        raise ValueError('unordered market must use sorted numbers')
    if mode == 'baseline' and (kind != '2shafuku' or ticket.get('strategy') != 'MARKET_FAVORITE_BASELINE_NO_EDGE_CLAIM'):
        raise ValueError('baseline must be 2shafuku market favorite')
    if p.get('forward_verified_clock') is not True or p.get('data_origin') != 'official_manual':
        raise ValueError('unverified or synthetic proposal')
    if p.get('stake_yen') != 100 or not official(p.get('source_url')) or not official(p.get('odds_source_url')):
        raise ValueError('stake or source invalid')
    deadline, start, selected, observed = (stamp(p[k]) for k in
        ('bet_deadline_jst', 'start_at_jst', 'selected_at_jst', 'odds_observed_at_jst'))
    if (not (now.date() == start.date() and deadline <= start and
             timedelta(minutes=10) <= deadline - now <= timedelta(minutes=180)) or
        selected > now or observed > selected or observed >= deadline or
        now - observed > timedelta(minutes=10)):
        raise ValueError('not a fresh, prospective, pre-close record')
    if (rid, mode) in accepted:
        raise ValueError('duplicate race and strategy')
    odds = ticket.get('odds_at_decision')
    if type(odds) not in (float, int) or not 1 < odds < 1000000:
        raise ValueError('invalid odds')
    if mode == 'edge':
        mid = ticket.get('model_id')
        if (not isinstance(mid, str) or mid not in model_allowlist.get('models', {}) or
            model_allowlist['models'][mid].get('bet_type') != kind or
            model_allowlist['models'][mid].get('independently_reviewed') is not True):
            raise ValueError('no independently reviewed model in local allowlist')
        if ticket.get('strategy') != 'DECLARED_HOLDOUT_EDGE_CONDITIONAL_3RENTAN':
            raise ValueError('invalid edge strategy')
        prob, stressed = ticket.get('estimated_probability'), ticket.get('stressed_expected_return')
        if (type(prob) not in (int, float) or not 0 < prob <= 1 or
            type(stressed) not in (int, float) or stressed < 1.3 or
            abs(prob * odds * 0.75 - stressed) > 0.001):
            raise ValueError('edge expected return mismatch')
        if kind == '3rentan' and prob < 0.12:
            raise ValueError('three-order confidence below 12%')
    return rid, mode


def validated_result(r, now, bets, settled):
    rid = r.get('race_id')
    if rid not in bets or rid in settled:
        raise ValueError('result has no ticket or already settled')
    if r.get('status') not in ('final', 'cancelled') or not official(r.get('source_url')):
        raise ValueError('unverified final result/source')
    confirmed = stamp(r.get('confirmed_at_jst'))
    if confirmed > now or confirmed < max(stamp(b['start_at_jst']) for b in bets[rid]):
        raise ValueError('result not yet finalized')
    if r['status'] == 'final':
        payouts = r.get('payouts')
        if not isinstance(payouts, dict):
            raise ValueError('missing payouts')
        for bet in bets[rid]:
            kind = bet['ticket']['bet_type']
            table = payouts.get(kind)
            if not isinstance(table, dict) or not table:
                raise ValueError(f'missing {kind} payout')
            if any(not isinstance(k, str) or type(v) is not int or v < 100 for k, v in table.items()):
                raise ValueError('invalid payout table')
    return rid


def stats(records):
    bets = defaultdict(list)
    settlements = {}
    for e in records:
        p = e['payload']
        if e['kind'] == 'bet':
            bets[p['race_id']].append(p)
        elif e['kind'] == 'settlement':
            settlements[p['race_id']] = p
    groups = defaultdict(lambda: {'settled_tickets': 0, 'hits': 0, 'stake_yen': 0,
                                  'returned_yen': 0, 'pending': 0, 'refunded': 0})
    for rid, items in bets.items():
        result = settlements.get(rid)
        for bet in items:
            k = bet['mode'] + '/' + bet['ticket']['bet_type']
            g = groups[k]
            if result is None:
                g['pending'] += 1
            elif result['status'] == 'cancelled':
                g['refunded'] += 1
            else:
                combo = bet['ticket']['combo']
                amount = result['payouts'][bet['ticket']['bet_type']].get(combo, 0)
                g['settled_tickets'] += 1
                g['stake_yen'] += bet['stake_yen']
                g['returned_yen'] += amount
                g['hits'] += int(amount > 0)
    for g in groups.values():
        n = g['settled_tickets']
        g['hit_rate'] = round(g['hits'] / n, 6) if n else None
        g['roi_ratio'] = round(g['returned_yen'] / g['stake_yen'], 6) if g['stake_yen'] else None
    return {'schema': 'keirin-paper-summary-v1', 'journal_events': len(records),
            'paper_bets': sum(map(len, bets.values())), 'settled_races': len(settlements),
            'groups': dict(sorted(groups.items())),
            'caveat': 'No official verification of model performance; no actual purchase; empty rates mean not measured'}


def sync(root, now=None):
    root = Path(root)
    now = (now or datetime.now(timezone.utc)).astimezone(JST)
    journal = root / 'journal.jsonl'
    records = load_journal(journal)
    processed = {r['input_file'] for r in records}
    allowlist = json.loads((root / 'verified_models.json').read_text(encoding='utf-8'))
    valid_bets = {(r['payload']['race_id'], r['payload']['mode']) for r in records if r['kind'] == 'bet'}
    bet_by_race = defaultdict(list)
    settled = set()
    for e in records:
        if e['kind'] == 'bet':
            bet_by_race[e['payload']['race_id']].append(e['payload'])
        elif e['kind'] == 'settlement':
            settled.add(e['payload']['race_id'])
    accepted, rejected = [], []
    for name in sorted((root / 'inbox').glob('*.json')):
        rel = name.relative_to(root).as_posix()
        if rel in processed:
            continue
        try:
            obj = json.loads(name.read_text(encoding='utf-8'))
            if obj.get('event_type') == 'bet':
                bet = obj['proposal']
                rid, mode = validated_bet(bet, now, valid_bets, allowlist)
                append(records, 'bet', bet, rel, now)
                valid_bets.add((rid, mode))
                bet_by_race[rid].append(bet)
            elif obj.get('event_type') == 'settlement':
                result = obj['result']
                rid = validated_result(result, now, bet_by_race, settled)
                append(records, 'settlement', result, rel, now)
                settled.add(rid)
            else:
                raise ValueError('invalid event type')
            accepted.append(rel)
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append({'file': rel, 'reason': str(exc)[:150]})
    if accepted:
        journal.write_text(''.join(canonical(r) + '\n' for r in records), encoding='utf-8')
    (root / 'summary.json').write_text(json.dumps(stats(records), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'accepted': accepted, 'rejected': rejected, 'summary': stats(records)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['sync', 'stats'])
    parser.add_argument('--root', default='paper_trading')
    args = parser.parse_args()
    root = Path(args.root)
    if args.command == 'sync':
        output = sync(root)
    else:
        output = stats(load_journal(root / 'journal.jsonl'))
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
