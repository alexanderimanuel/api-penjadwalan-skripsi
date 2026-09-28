import copy
import json
import tempfile
import unittest
from pathlib import Path
from validate_august_dataset import ROOT, audit, freeze

class DatasetAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=json.loads((ROOT/'data/reconciliation/2026-08-03/candidate-dataset.json').read_text(encoding='utf-8'))
        cls.blocks=json.loads((ROOT/'data/blocks/2026-08-03/blocks.json').read_text(encoding='utf-8'))
        cls.calendar=json.loads((ROOT/'data/calendar/2026-08-03/calendar.json').read_text(encoding='utf-8'))

    def setUp(self): self.data=copy.deepcopy(self.base)
    def run_audit(self): return audit(self.data,self.blocks,self.calendar)

    def test_source_valid_integrity_but_not_feasible(self):
        result=self.run_audit()
        self.assertTrue(result['integrity_passed'])
        self.assertFalse(result['reference_feasible_under_recorded_assignments'])
        self.assertEqual(len(result['reference_hard_audit']['HC1']),1)

    def test_missing_event(self):
        self.data['events'].pop()
        self.assertTrue(self.run_audit()['reference_hard_audit']['HC3'])

    def test_duplicate_event(self):
        self.data['events'].append(copy.deepcopy(self.data['events'][0]))
        self.assertFalse(self.run_audit()['integrity_passed'])

    def test_changed_teacher(self):
        self.data['events'][0]['teacher_codes']=['1']
        self.assertTrue(self.run_audit()['reference_hard_audit']['HC6'])

    def test_changed_minutes(self):
        self.data['events'][0]['time']['kbm_minutes']+=5
        self.assertTrue(self.run_audit()['reference_hard_audit']['HC4'])

    def test_fixed_slot_used(self):
        e=self.data['events'][0]
        e['reference']={'day':'senin','start_period':1,'end_period':2}
        self.assertTrue(self.run_audit()['reference_hard_audit']['HC5'])

    def test_evidence_tampering(self):
        self.data['events'][0]['source_evidence']['class_block']['raw_text']='changed'
        self.assertFalse(self.run_audit()['integrity_passed'])

    def test_changed_fixed_records(self):
        self.data['fixed_activity_source_records'].pop()
        self.assertFalse(self.run_audit()['integrity_passed'])

    def test_snapshot_rejects_overwrite_before_any_write(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)
            freeze({'one':b'original'},p)
            freeze({'one':b'original'},p)
            with self.assertRaises(ValueError): freeze({'new':b'new','one':b'changed'},p)
            self.assertEqual((p/'one').read_bytes(),b'original')
            self.assertFalse((p/'new').exists())

if __name__=='__main__': unittest.main()
