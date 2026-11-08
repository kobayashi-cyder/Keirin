import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'paper_trading'))
import ledger

JST = ZoneInfo('Asia/Tokyo')


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'inbox').mkdir()
        (self.root/'verified_models.json').write_text('{"models": {}}', encoding='utf-8')
        self.now = datetime(2026,11,8,12,0,tzinfo=JST)

    def tearDown(self):
        self.tmp.cleanup()

    def p(self):
        return {'status':'PAPER_BET','race_id':'20261108_nara_01','date':'2026-11-08',
                'mode':'baseline','data_origin':'official_manual',
                'bet_deadline_jst':(self.now+timedelta(minutes=40)).isoformat(),
                'start_at_jst':(self.now+timedelta(minutes=45)).isoformat(),
                'selected_at_jst':self.now.isoformat(), 'odds_observed_at_jst':self.now.isoformat(),
                'source_url':'https://keirin.jp/','odds_source_url':'https://keirin.jp/',
                'forward_verified_clock':True,'stake_yen':100,
                'ticket':{'bet_type':'2shafuku','combo':'1-2','odds_at_decision':3.0,
                          'strategy':'MARKET_FAVORITE_BASELINE_NO_EDGE_CLAIM'}}

    def inbox(self,name,obj):
        (self.root/'inbox'/name).write_text(json.dumps(obj),encoding='utf-8')

    def test_baseline_before_close_and_idempotent(self):
        self.inbox('b.json',{'event_type':'bet','proposal':self.p()})
        first=ledger.sync(self.root,self.now)
        self.assertEqual(len(first['accepted']),1)
        self.assertEqual(first['summary']['paper_bets'],1)
        again=ledger.sync(self.root,self.now)
        self.assertEqual(again['accepted'],[])
        self.assertEqual(again['summary']['paper_bets'],1)
        self.assertEqual(ledger.stats(ledger.load_journal(self.root/'journal.jsonl'))['groups']['baseline/2shafuku']['hit_rate'],None)

    def test_late_record_rejected(self):
        self.inbox('b.json',{'event_type':'bet','proposal':self.p()})
        late=ledger.sync(self.root,self.now+timedelta(minutes=31))
        self.assertFalse(late['accepted'])
        self.assertEqual(late['summary']['paper_bets'],0)

    def test_edge_without_model_rejected(self):
        p=self.p();p['mode']='edge';p['ticket'].update({'strategy':'DECLARED_HOLDOUT_EDGE_CONDITIONAL_3RENTAN','model_id':'unverified',
                'estimated_probability':0.5,'stressed_expected_return':1.35,'odds_at_decision':3.6})
        self.inbox('e.json',{'event_type':'bet','proposal':p})
        r=ledger.sync(self.root,self.now)
        self.assertFalse(r['accepted'])
        self.assertEqual(r['summary']['paper_bets'],0)

    def test_settlement_and_roi(self):
        p=self.p();self.inbox('b.json',{'event_type':'bet','proposal':p});ledger.sync(self.root,self.now)
        result={'race_id':p['race_id'],'status':'final','confirmed_at_jst':(self.now+timedelta(minutes=50)).isoformat(),
                'source_url':'https://keirin.jp/', 'payouts':{'2shafuku':{'1-2':330}}}
        self.inbox('r.json',{'event_type':'settlement','result':result})
        r=ledger.sync(self.root,self.now+timedelta(minutes=51))
        g=r['summary']['groups']['baseline/2shafuku']
        self.assertEqual(g['hit_rate'],1.0)
        self.assertEqual(g['roi_ratio'],3.3)
        self.assertEqual(r['summary']['settled_races'],1)

    def test_integrity_failure(self):
        self.inbox('b.json',{'event_type':'bet','proposal':self.p()});ledger.sync(self.root,self.now)
        f=self.root/'journal.jsonl'
        f.write_text(f.read_text().replace('1-2','2-3'),encoding='utf-8')
        with self.assertRaises(ValueError): ledger.load_journal(f)


if __name__=='__main__':
    unittest.main()
