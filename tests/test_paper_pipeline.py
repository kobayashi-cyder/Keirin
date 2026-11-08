"""Full synthetic/offline integration test. Not real racing performance."""
import itertools
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'paper_trading'))
import api_bridge
import snapshot_ingest
import ledger

JST = ZoneInfo('Asia/Tokyo')


class TestOfflinePipeline(unittest.TestCase):
    def test_before_close_then_post_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'inbox').mkdir()
            (root / 'snapshots').mkdir()
            (root / 'verified_models.json').write_text('{"models":{}}')
            t = datetime(2026, 11, 8, 12, tzinfo=JST)
            cars = list(range(1, 8))
            odds = {f'{a}-{b}':12.0 for a,b in itertools.combinations(cars, 2)}
            odds['1-2'] = 4.0
            snap = {'race_id':'20261108_NARA_05','data_origin':'official_manual',
                    'cars':cars, 'source_url':'https://keirin.jp/pc/race',
                    'start_at_jst':(t+timedelta(minutes=45)).isoformat(),
                    'bet_deadline_jst':(t+timedelta(minutes=40)).isoformat(),
                    'features_observed_at_jst':t.isoformat(),
                    'odds':{'2shafuku':{'source_url':'https://keirin.jp/pc/odds',
                        'observed_at_jst':t.isoformat(), 'values':odds}}}
            (root/'snapshots'/'nara05.json').write_text(json.dumps(snap))
            self.assertEqual(len(snapshot_ingest.ingest(root,t)['created']), 1)
            initial=ledger.sync(root,t+timedelta(seconds=1))
            self.assertEqual(initial['summary']['paper_bets'],1)
            self.assertEqual(initial['summary']['groups']['baseline/2shafuku']['pending'],1)
            with patch.dict(os.environ, {'OPENAI_API_KEY':''}):
                self.assertEqual(api_bridge.publish(root,t+timedelta(seconds=2))['new_alerts'],0)
            final={'race_id':snap['race_id'],'status':'final',
                   'confirmed_at_jst':(t+timedelta(minutes=50)).isoformat(),
                   'source_url':'https://keirin.jp/pc/result',
                   'payouts':{'2shafuku':{'1-2':330}}}
            (root/'inbox'/'nara05_result.json').write_text(json.dumps({'event_type':'settlement','result':final}))
            settled=ledger.sync(root,t+timedelta(minutes=51))
            group=settled['summary']['groups']['baseline/2shafuku']
            self.assertEqual(group['settled_tickets'],1)
            self.assertEqual(group['hits'],1)
            self.assertEqual(group['roi_ratio'],3.3)
            with patch.dict(os.environ, {'OPENAI_API_KEY':''}):
                self.assertEqual(api_bridge.publish(root,t+timedelta(minutes=51,seconds=1))['new_alerts'],1)
            feed=json.loads((root/'alerts'/'feed.json').read_text())
            self.assertEqual(feed['events'][0]['kind'],'settlement')
            self.assertIn('3.300',feed['events'][0]['message'])
            self.assertEqual(ledger.sync(root,t+timedelta(minutes=52))['accepted'],[])


if __name__ == '__main__':
    unittest.main()
