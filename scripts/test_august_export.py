import copy
import tempfile
import unittest
from pathlib import Path
from export_august import ROOT, validate_run, materialize, audit_export, read_json, render_schedule

class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.problem,cls.dataset,cls.solution,cls.report=validate_run(ROOT/'results/august_sa_stage09/verified-seed0')
        cls.export=materialize(cls.problem,cls.dataset,cls.solution,cls.report)
    def test_resource_views_count_multi_teacher_event_correctly(self):
        r=audit_export(self.export,self.problem)
        self.assertEqual((r['unique_events'],r['teacher_lesson_rows'],r['class_fixed_records']),(513,522,54))
        self.assertEqual(len([r for r in self.export['events'] if len(r['teacher_codes'])>1]),5)
    def test_destination_clock_differs_from_original_when_moved(self):
        for r in self.export['events']:
            if r['event_id']=='E-kelas-27-12':
                self.assertEqual((r['day'],r['start_period'],r['start'],r['end']),('selasa',2,'07:30','09:45'))
                self.assertEqual(r['source_placement']['day'],'rabu')
                break
        else:self.fail('Missing moved event')
    def test_empty_teacher_included(self):
        t=next(t for t in self.export['teachers'] if t['code']=='1')
        self.assertEqual(t['lessons'],[])
        self.assertEqual(t['totals'],{'meetings':0,'jp':0,'kbm_minutes':0})
        self.assertIn('Tidak ada penugasan',render_schedule(self.export,'teachers'))
    def test_teacher_row_loss_detected(self):
        d=copy.deepcopy(self.export)
        next(t for t in d['teachers'] if t['lessons'])['lessons'].pop()
        with self.assertRaises(ValueError):audit_export(d,self.problem)
    def test_clock_tampering_detected(self):
        d=copy.deepcopy(self.export);d['events'][0]['start']='00:00'
        with self.assertRaises(ValueError):audit_export(d,self.problem)
    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'bad.json';p.write_text('{"event":1,"event":2}')
            with self.assertRaises(ValueError):read_json(p)
    def test_best_and_final_are_not_confused(self):
        _,_,_,r=validate_run(ROOT/'results/august_sa_stage09/verified-seed1','final')
        self.assertEqual(r['score']['S'],281)
        _,_,_,r=validate_run(ROOT/'results/august_sa_stage09/verified-seed1','best')
        self.assertEqual(r['score']['S'],239)

if __name__=='__main__':unittest.main()
