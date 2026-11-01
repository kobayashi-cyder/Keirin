import csv
import tempfile
import unittest
from pathlib import Path

from tools.build_analysis_tables import build


def write(path, fields, rows):
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


class AnalysisExportTests(unittest.TestCase):
    def test_dead_heat_and_duplicate_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / 'data'
            data.mkdir()
            rows = [
                {'race_id':'20261031_kumamoto_01','winning_combination':'1-7-5',
                 'payout_per_100yen':'340','winning_final_odds':'3.4'},
                {'race_id':'20261031_kumamoto_01','winning_combination':'7-1-5',
                 'payout_per_100yen':'330','winning_final_odds':'3.3'},
            ]
            fields = list(rows[0])
            write(data/'a.csv', fields, rows)
            write(data/'b.csv', fields, rows)
            write(data/'odds.csv', ['race_id','rank','combination','final_odds','hit'], [
                {'race_id':'20261031_kumamoto_01','rank':'1','combination':'1-7-5',
                 'final_odds':'3.4','hit':'1'}])
            self.assertEqual(build(data,root/'exports'), (2,1,3))
            with (root/'exports'/'results_3rentan.csv').open() as f:
                output = list(csv.DictReader(f))
            self.assertEqual(len(output),2)
            self.assertEqual(output[0]['source_files'],'a.csv|b.csv')
            self.assertEqual(output[0]['date'],'2026-10-31')
            self.assertEqual(output[0]['venue_slug'],'kumamoto')
            with (root/'exports'/'source_manifest.csv').open() as f:
                manifest = list(csv.DictReader(f))
            self.assertEqual(len(manifest),3)

    def test_conflict_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root/'data'
            data.mkdir()
            fields = ['race_id','winning_combination','payout_per_100yen','winning_final_odds']
            write(data/'a.csv', fields, [dict(zip(fields,['20261031_aomori_01','1-2-3','1500','15']))])
            write(data/'b.csv', fields, [dict(zip(fields,['20261031_aomori_01','1-2-3','1600','16']))])
            with self.assertRaisesRegex(ValueError,'conflicting'):
                build(data,root/'exports')


if __name__ == '__main__':
    unittest.main()
