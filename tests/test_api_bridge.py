import hashlib
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
import api_bridge as mod

JST = ZoneInfo('Asia/Tokyo')


class ApiBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 11, 8, 12, tzinfo=JST)
        (self.root/'verified_models.json').write_text(json.dumps({'models': {}}), encoding='utf-8')
        (self.root/'summary.json').write_text(json.dumps({'groups': {}}), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def entry(self, mode='edge', kind='bet'):
        p = {'status':'PAPER_BET', 'race_id':'20261108_NARA_05', 'mode':mode,
             'forward_verified_clock':True, 'selected_at_jst':self.now.isoformat(),
             'odds_observed_at_jst':self.now.isoformat(),
             'bet_deadline_jst':(self.now+timedelta(minutes=40)).isoformat(),
             'ticket':{'model_id':'certified','bet_type':'2shafuku','combo':'1-2',
                       'estimated_probability':0.5,'odds_at_decision':4.0,
                       'stressed_expected_return':1.5}}
        if kind == 'settlement':
            p={'race_id':'20261108_NARA_05','status':'final'}
        rec = {'seq':1,'prev_hash':mod.GENESIS,'kind':kind,
               'ingested_at_jst':self.now.isoformat(),'input_file':'inbox/x.json', 'payload':p}
        rec['digest'] = hashlib.sha256(mod.canonical(rec).encode('utf-8')).hexdigest()
        (self.root/'journal.jsonl').write_text(mod.canonical(rec)+'\n',encoding='utf-8')
        return rec

    def test_no_proposal_no_alerts(self):
        self.assertEqual(mod.publish(self.root, self.now)['new_alerts'], 0)

    def test_unverified_model_no_alerts(self):
        self.entry()
        self.assertEqual(mod.publish(self.root, self.now)['new_alerts'], 0)

    def test_eligible_edge_and_idempotency(self):
        self.entry()
        (self.root/'verified_models.json').write_text(json.dumps({'models': {'certified': {'bet_type': '2shafuku', 'independently_reviewed': True}}}))
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            self.assertEqual(mod.publish(self.root, self.now)['new_alerts'], 1)
            self.assertEqual(mod.publish(self.root, self.now)['new_alerts'], 0)
        event=json.loads((self.root/'alerts'/'feed.json').read_text())['events'][0]
        self.assertEqual(event['openai_note_status'], 'no_api_key')
        self.assertIn('1.500', event['message'])

    def test_stale_deadline_not_announced(self):
        self.entry()
        (self.root/'verified_models.json').write_text(json.dumps({'models': {'certified': {'bet_type': '2shafuku', 'independently_reviewed': True}}}))
        self.assertEqual(mod.publish(self.root, self.now+timedelta(minutes=35))['new_alerts'], 0)

    def test_settlement_auto_summary(self):
        self.entry(kind='settlement')
        (self.root/'summary.json').write_text(json.dumps({'groups': {'edge/2shafuku': {
            'settled_tickets':1,'hits':1,'roi_ratio':3.3}}}),encoding='utf-8')
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            self.assertEqual(mod.publish(self.root, self.now)['new_alerts'],1)
        event=json.loads((self.root/'alerts'/'feed.json').read_text())['events'][0]
        self.assertIn('3.300',event['message'])

    def test_api_is_commentary_only(self):
        record = self.entry()
        sample = {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'結果が確定するまで実績には含めません。'}]}]}
        with patch.dict(os.environ, {'OPENAI_API_KEY':'test_fake','OPENAI_MODEL':'test-model'}):
            note, flag = mod.api_note(record, http_client=lambda req,key:sample)
        self.assertEqual(flag,'api_ok')
        self.assertTrue(note)

    def test_forbid_api_hype_and_numbers(self):
        record=self.entry()
        sample={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'勝率100%保証します'}]}]}
        with patch.dict(os.environ, {'OPENAI_API_KEY':'test_fake'}):
            note,flag=mod.api_note(record, http_client=lambda req,key:sample)
        self.assertIsNone(note)
        self.assertEqual(flag,'api_rejected')

    def test_default_openai_model_is_gpt6_luna(self):
        record = self.entry()
        captured = []
        sample = {'status': 'completed', 'output': [
            {'type': 'message', 'content': [
                {'type': 'output_text', 'text': '接続状態を確認しました。'}
            ]}
        ]}
        def fake_client(request, key):
            captured.append(request)
            return sample
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake_test_key'}, clear=True):
            note, status = mod.api_note(record, http_client=fake_client)
        self.assertEqual(status, 'api_ok')
        self.assertEqual(captured[0]['model'], 'gpt-6-luna')
        self.assertEqual(captured[0]['reasoning'], {'effort': 'none'})
        self.assertFalse(captured[0]['store'])

    def test_hash_tampering_detected(self):
        self.entry()
        p=self.root/'journal.jsonl'
        p.write_text(p.read_text().replace('1-2','2-3'),encoding='utf-8')
        with self.assertRaises(ValueError):
            mod.publish(self.root,self.now)


if __name__ == '__main__':
    unittest.main()
