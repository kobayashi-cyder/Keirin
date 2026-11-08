#!/usr/bin/env python3
"""Accept manually documented, complete preclose official odds for BASELINE only.

No official feed or validated predictive model is bundled.
No scraping, no retrospective entry, no actual wagering.
"""
import argparse
import itertools
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

JST = ZoneInfo('Asia/Tokyo')


def when(value):
    t = datetime.fromisoformat(value)
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError('timestamp must have offset')
    return t.astimezone(JST)


def official(url):
    try:
        p = urlparse(url)
        return p.scheme == 'https' and p.hostname in ('keirin.jp', 'www.keirin.jp')
    except (TypeError, ValueError):
        return False


def proposal(s, now):
    if s.get('data_origin') != 'official_manual' or not official(s.get('source_url')):
        raise ValueError('provenance not acceptable')
    rid = s.get('race_id')
    if not isinstance(rid, str) or not re.fullmatch('[A-Za-z0-9_-]{4,100}', rid):
        raise ValueError('invalid race ID')
    cars = s.get('cars')
    if not isinstance(cars, list) or len(cars) not in (7,9) or any(type(x) is not int for x in cars) or sorted(set(cars)) != cars:
        raise ValueError('invalid race cars')
    deadline, start = when(s['bet_deadline_jst']), when(s['start_at_jst'])
    featured = when(s['features_observed_at_jst'])
    if not (now.date() == start.date() and
            deadline <= start and timedelta(minutes=10) <= deadline - now <= timedelta(minutes=180) and
            featured <= now and featured < deadline):
        raise ValueError('not an eligible future race')
    q = (s.get('odds') or {}).get('2shafuku')
    if not isinstance(q,dict) or not official(q.get('source_url')):
        raise ValueError('missing primary official market')
    observed = when(q['observed_at_jst'])
    if not (observed <= now and observed < deadline and now - observed <= timedelta(minutes=10)):
        raise ValueError('stale or non-prospective odds')
    odds=q.get('values')
    expected={f'{i}-{j}' for i,j in itertools.combinations(cars,2)}
    if not isinstance(odds,dict) or set(odds) != expected:
        raise ValueError('requires all official quinella combinations')
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<=1 or v>=9999.9 for v in odds.values()):
        raise ValueError('invalid or no-bid odds')
    # Basic sanity check against mistaken units/market/format.
    implied=sum(1/v for v in odds.values())
    if not 1.01 <= implied <= 3:
        raise ValueError('unlikely complete market')
    best=min(odds, key=lambda c:(odds[c],c))
    return {'status':'PAPER_BET','race_id':rid,'date':start.date().isoformat(),
            'bet_deadline_jst':s['bet_deadline_jst'],'start_at_jst':s['start_at_jst'],
            'source_url':s['source_url'],'data_origin':'official_manual',
            'odds_source_url':q['source_url'],'odds_observed_at_jst':q['observed_at_jst'],
            'selected_at_jst':now.isoformat(),'mode':'baseline',
            'forward_verified_clock':True,'stake_yen':100,
            'ticket':{'bet_type':'2shafuku','combo':best,'odds_at_decision':odds[best],
                      'estimated_probability':None,'stressed_expected_return':None,
                      'strategy':'MARKET_FAVORITE_BASELINE_NO_EDGE_CLAIM'}}


def ingest(root, now=None):
    root=Path(root)
    now=(now or datetime.now(timezone.utc)).astimezone(JST)
    snapshots=root/'snapshots'
    inbox=root/'inbox'
    inbox.mkdir(parents=True,exist_ok=True)
    accepted=[]; rejected=[]
    for path in sorted(snapshots.glob('*.json')):
        try:
            s=json.loads(path.read_text(encoding='utf-8'))
            rid=s.get('race_id')
            target=inbox/(rid+'_baseline.json') if isinstance(rid,str) and re.fullmatch('[A-Za-z0-9_-]{4,100}',rid) else None
            if target and target.exists():
                continue
            p=proposal(s,now)
            target=inbox/(p['race_id']+'_baseline.json')
            if target.exists():
                continue
            target.write_text(json.dumps({'event_type':'bet','proposal':p},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            accepted.append(target.name)
        except (ValueError,TypeError,KeyError,AttributeError) as e:
            rejected.append({'file':path.name,'reason':str(e)[:90]})
    return {'created':accepted,'skipped':rejected}


if __name__ == '__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--root',default='paper_trading');args=ap.parse_args()
    print(json.dumps(ingest(args.root),ensure_ascii=False,indent=2))
