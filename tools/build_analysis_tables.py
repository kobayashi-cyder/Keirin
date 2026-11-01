#!/usr/bin/env python3
"""Normalize already-committed race results/odds into analysis-ready CSVs.

No network access or third-party scraping; nothing is guessed. This is a
materialized view, not a new external data collection or rights clearance.
"""
import csv
import hashlib
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
OUT = ROOT / 'exports'

RESULT_COLUMNS = ['race_id','date','venue_slug','race_number','bet_type',
                  'winning_combination','payout_per_100yen','winning_final_odds',
                  'popularity_rank','result_type','source_urls','source_files']
ODDS_COLUMNS = ['race_id','date','venue_slug','race_number','bet_type','rank',
                'combination','odds_multiplier','hit','odds_status',
                'source_timestamp_jst','source_urls','source_files']
MANIFEST_COLUMNS = ['path','kind','rows','unique_races','sha256','rows_with_source_url']


def race_fields(rid):
    parts = rid.split('_')
    if len(parts) != 3 or len(parts[0]) != 8 or not parts[0].isdigit() or not parts[2].isdigit():
        raise ValueError(f'invalid race_id: {rid}')
    from datetime import datetime
    date = datetime.strptime(parts[0], '%Y%m%d').date().isoformat()
    return {'race_id':rid,'date':date,'venue_slug':parts[1],
            'race_number':str(int(parts[2])),'bet_type':'3rentan'}


def merge_text(record, new, keys, rid):
    for key in keys:
        value = new.get(key,'')
        if value and record.get(key,'') and record[key] != value:
            raise ValueError(f'conflicting {key}: {rid}: {record[key]} != {value}')
        if value and not record.get(key):
            record[key] = value


def rows_to_csv(path, fields, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8') as f:
        writer = csv.DictWriter(f,fieldnames=fields,lineterminator='\n')
        writer.writeheader()
        for r in rows:
            writer.writerow({k:r.get(k,'') for k in fields})


def build(data_dir=DATA, out_dir=OUT):
    results, odds, manifest = {}, {}, []
    for path in sorted(data_dir.glob('*.csv')):
        with path.open(encoding='utf-8-sig',newline='') as f:
            reader = csv.DictReader(f)
            fields = set(reader.fieldnames or [])
            if {'race_id','winning_combination','payout_per_100yen'} <= fields:
                kind = 'results'
            elif {'race_id','rank','combination'} <= fields:
                kind = 'odds'
            else:
                continue
            count, races, links = 0, set(), 0
            for raw in reader:
                count += 1
                rid = raw['race_id']
                races.add(rid)
                links += bool(raw.get('source_url'))
                base = race_fields(rid)
                if kind == 'results':
                    payout = int(raw['payout_per_100yen'])
                    odd = Decimal(raw['winning_final_odds'])
                    if payout <= 0 or odd != Decimal(payout) / 100:
                        raise ValueError(f'payout mismatch: {rid}')
                    combo = raw['winning_combination']
                    if len(set(combo.split('-'))) != 3 or len(combo.split('-')) != 3:
                        raise ValueError(f'invalid winning combination: {rid}')
                    key = (rid,combo)
                    new = dict(base,winning_combination=combo,payout_per_100yen=str(payout),
                               winning_final_odds=str(odd),
                               popularity_rank=raw.get('popularity_rank',''),
                               result_type=raw.get('result_type',''))
                    target = results
                    immutable = ['payout_per_100yen','winning_final_odds']
                    optional = ['popularity_rank','result_type']
                else:
                    rank = int(raw['rank'])
                    if rank < 1: raise ValueError(f'invalid rank: {rid}')
                    combo = raw['combination']
                    keys = combo.split('-')
                    if len(keys) != 3 or len(set(keys)) != 3:
                        raise ValueError(f'invalid odds combination: {rid}')
                    value = next((raw.get(k) for k in ('final_odds','displayed_odds','odds') if raw.get(k)),None)
                    if value is None: raise ValueError(f'missing odds: {rid} #{rank}')
                    try:
                        decimal = Decimal(value)
                    except InvalidOperation:
                        raise ValueError(f'invalid odds: {rid} #{rank}')
                    if not decimal.is_finite() or decimal <= 0: raise ValueError(f'invalid odds: {rid} #{rank}')
                    key = (rid,rank)
                    new = dict(base,rank=str(rank),combination=combo,odds_multiplier=str(decimal),
                               hit=raw.get('hit',''),odds_status=raw.get('odds_status','unknown') or 'unknown',
                               source_timestamp_jst=raw.get('odds_site_timestamp_jst',''))
                    target = odds
                    immutable = ['combination','odds_multiplier']
                    optional = ['hit','source_timestamp_jst']
                if key not in target:
                    new['_files'] = set()
                    new['_urls'] = set()
                    target[key] = new
                record = target[key]
                merge_text(record,new,immutable+optional,key)
                if kind == 'odds':
                    # A documented status supersedes unknown; conflicting statuses fail.
                    a,b=record['odds_status'],new['odds_status']
                    if a == 'unknown': record['odds_status'] = b
                    elif b != 'unknown' and a != b: raise ValueError(f'conflicting odds status: {key}')
                record['_files'].add(path.name)
                if raw.get('source_url'): record['_urls'].add(raw['source_url'])
            manifest.append({'path':f'data/{path.name}','kind':kind,'rows':count,
                'unique_races':len(races),
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'rows_with_source_url':links})

    def finalize(row):
        row['source_files']='|'.join(sorted(row.pop('_files')))
        row['source_urls']='|'.join(sorted(row.pop('_urls')))
        return row
    result_rows=[finalize(results[k]) for k in sorted(results)]
    odds_rows=[finalize(odds[k]) for k in sorted(odds)]
    rows_to_csv(out_dir/'results_3rentan.csv',RESULT_COLUMNS,result_rows)
    rows_to_csv(out_dir/'odds_3rentan.csv',ODDS_COLUMNS,odds_rows)
    rows_to_csv(out_dir/'source_manifest.csv',MANIFEST_COLUMNS,manifest)
    print(f'exported {len(result_rows)} payout rows, {len(odds_rows)} odds points, {len(manifest)} source files')
    return len(result_rows),len(odds_rows),len(manifest)


if __name__ == '__main__':
    build()
