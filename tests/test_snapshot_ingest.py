import itertools
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'paper_trading'))
import snapshot_ingest as m

JST=ZoneInfo('Asia/Tokyo')

class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'snapshots').mkdir()
        self.now=datetime(2026,11,8,12,tzinfo=JST)

    def tearDown(self):self.tmp.cleanup()

    def data(self):
        cars=list(range(1,8))
        odds={f'{i}-{j}':12.0 for i,j in itertools.combinations(cars,2)}
        odds['1-2']=4.0
        return {'race_id':'20261108_foo_01','data_origin':'official_manual',
                'source_url':'https://keirin.jp/pc/race', 'cars':cars,
                'bet_deadline_jst':(self.now+timedelta(minutes=40)).isoformat(),
                'start_at_jst':(self.now+timedelta(minutes=45)).isoformat(),
                'features_observed_at_jst':self.now.isoformat(),
                'odds':{'2shafuku':{'source_url':'https://keirin.jp/pc/odds',
                                   'observed_at_jst':self.now.isoformat(), 'values':odds}}}

    def test_favorite(self):
        self.assertEqual(m.proposal(self.data(),self.now)['ticket']['combo'],'1-2')

    def test_missing_market(self):
        s=self.data();s['odds']['2shafuku']['values'].pop('1-3')
        with self.assertRaises(ValueError):m.proposal(s,self.now)

    def test_stale_market(self):
        s=self.data();s['odds']['2shafuku']['observed_at_jst']=(self.now-timedelta(minutes=11)).isoformat()
        with self.assertRaises(ValueError):m.proposal(s,self.now)

    def test_ingest(self):
        (self.root/'snapshots'/'sample.json').write_text(json.dumps(self.data()))
        r=m.ingest(self.root,self.now)
        self.assertEqual(len(r['created']),1)
        self.assertEqual(len(m.ingest(self.root,self.now)['created']),0)

if __name__=='__main__':unittest.main()
