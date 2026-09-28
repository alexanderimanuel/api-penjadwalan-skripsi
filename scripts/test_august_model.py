import copy
import json
import unittest
from model_august import ROOT, domain, evaluate, bounds

class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calendar=json.loads((ROOT/'data/calendar/2026-08-03/calendar.json').read_text(encoding='utf-8'))
    def event(self,id,teachers=None,jp=1,minutes=45,breaks=None):
        return {'id':id,'teacher_codes':teachers or ['1'],'class_ids':['X-1'],'subject_id':'MAT',
                'time':{'jp':jp,'kbm_minutes':minutes,'breaks':breaks or []}}
    def score(self,events,starts,day='selasa'):
        ds={e['id']:domain(e,self.calendar) for e in events}
        return evaluate(events,self.calendar,ds,{e['id']:{'day':day,'start_period':p} for e,p in zip(events,starts)},10000)
    def test_minutes_must_match(self):
        ds=domain(self.event('a',jp=2,minutes=90),self.calendar)
        self.assertIn({'day':'senin','start_period':2},ds)
        self.assertNotIn({'day':'jumat','start_period':2},ds)
    def test_break_pattern_includes_position_and_minutes(self):
        ds=domain(self.event('a',jp=2,minutes=90,breaks=[{'after_jp':1,'minutes':15}]),self.calendar)
        self.assertIn({'day':'selasa','start_period':4},ds)
        self.assertNotIn({'day':'selasa','start_period':6},ds)
        self.assertNotIn({'day':'selasa','start_period':2},ds)
    def test_fixed_and_outside_calendar_rejected(self):
        ds=domain(self.event('a',minutes=40),self.calendar)
        self.assertNotIn({'day':'jumat','start_period':1},ds)
        self.assertNotIn({'day':'jumat','start_period':10},ds)
    def test_pair_once_even_multiple_periods_and_teachers(self):
        es=[self.event(i,['1','2'],jp=2,minutes=90) for i in ['a','b']]
        s=self.score(es,[1,1])
        self.assertEqual((s['HC1'],s['HC2']),(1,1))
    def test_sc1_ignores_break_and_external_free_time(self):
        es=[self.event(i) for i in ['a','b']]
        self.assertEqual(self.score(es,[6,7])['SC1'],0)
        self.assertEqual(self.score(es,[5,7])['SC1'],1)
        self.assertEqual(self.score(es[:1],[5])['SC1'],0)
    def test_sc2_groups_class_subject_not_teacher(self):
        es=[self.event(str(i),[str(i)]) for i in range(1,4)]
        self.assertEqual(self.score(es,[1,2,3])['SC2'],3)
        es[-1]['subject_id']='MAT P'
        self.assertEqual(self.score(es,[1,2,3])['SC2'],1)
    def test_incomplete_or_extra_payload_rejected(self):
        es=[self.event('a')];ds={'a':domain(es[0],self.calendar)}
        for solution in [{},{'a':{'day':'senin','start_period':1}},
                         {'a':{'day':'selasa','start_period':1},'b':{'day':'selasa','start_period':2}}]:
            with self.assertRaises(ValueError):evaluate(es,self.calendar,ds,solution,10)
    def test_big_m_separates_hard_and_soft(self):
        es=[self.event('a'),self.event('b')]
        b=bounds(es,self.calendar,[{'source_code':'1'}])
        self.assertGreater(b['M'],b['S_upper'])

if __name__=='__main__':unittest.main()
