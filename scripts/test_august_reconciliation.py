"""Kasus kegagalan rekonsiliasi serta batas interval; tidak bergantung PDF."""
import copy
import unittest
from reconcile_august import reconcile, overlap_findings

def block(source, identity, teacher):
    return {'source':source,'id':identity,'teacher_codes':teacher,'class_ids':['X-1'],
            'subject_id':'AGM','day':'senin','start_period':2,'end_period':3,
            'time':{'jp':2,'kbm_minutes':90,'breaks':[]}}

class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.records=[block('kelas','k',['5','52']),block('guru','g5',['5']),block('guru','g52',['52'])]

    def test_multiple_teachers_match_once_each(self):
        matches,errors=reconcile(self.records)
        self.assertEqual(errors,[])
        self.assertEqual(len(matches),1)
        self.assertEqual(len(matches[0]['teacher_matches']),2)

    def test_missing_teacher_is_not_silently_accepted(self):
        _,errors=reconcile(self.records[:-1])
        self.assertIn('missing_match',{e['type'] for e in errors})

    def test_duplicate_teacher_match_is_ambiguous(self):
        duplicate=copy.deepcopy(self.records[-1]);duplicate['id']='duplicate'
        _,errors=reconcile(self.records+[duplicate])
        self.assertIn('ambiguous_match',{e['type'] for e in errors})

    def test_equal_jp_different_minutes_rejected(self):
        self.records[-1]['time']['kbm_minutes']=80
        _,errors=reconcile(self.records)
        self.assertIn('time_mismatch',{e['type'] for e in errors})

    def test_equal_minutes_different_break_rejected(self):
        self.records[-1]['time']['breaks']=[{'after_jp':1,'minutes':15}]
        _,errors=reconcile(self.records)
        self.assertIn('time_mismatch',{e['type'] for e in errors})

    def test_same_teacher_reference_cannot_be_reused(self):
        duplicate=copy.deepcopy(self.records[0]);duplicate['id']='k2'
        _,errors=reconcile(self.records+[duplicate])
        self.assertIn('teacher_block_reused',{e['type'] for e in errors})

    def test_touching_intervals_and_breaks_are_not_overlap(self):
        a={'id':'a','reference':{'day':'rabu'},'teacher_codes':['39'],'class_ids':['XI-1'],
           'time':{'kbm_intervals':[{'start':'10:45','end':'11:30'},{'start':'12:15','end':'13:00'}]}}
        b={'id':'b','reference':{'day':'rabu'},'teacher_codes':['39'],'class_ids':['XII-9'],
           'time':{'kbm_intervals':[{'start':'11:30','end':'12:15'}]}}
        self.assertEqual(overlap_findings([a,b]),[])
        b['time']['kbm_intervals']=[{'start':'12:15','end':'13:00'}]
        findings=overlap_findings([a,b])
        self.assertEqual(len(findings),1)
        self.assertEqual(findings[0]['class_ids'],[])
        self.assertEqual(findings[0]['teacher_codes'],['39'])

if __name__=='__main__': unittest.main()
