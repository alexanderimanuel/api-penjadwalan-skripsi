"""Synthetic fixtures only; none of these categories/weights are school facts."""
import copy
import json
import math
import random
import unittest
from pathlib import Path
from model_august import ROOT
from final_rules import FinalModel,ConfigBlocked,raw_bounds,readiness
from final_validator import validate
from final_sa import run,construct,neighbor,calibrate,configurations,score_equal
from final_recommendations import select,canonical,sufficiently_different
from final_cli import plan

def fixture():
    calendar=json.loads((ROOT/'data/calendar/2026-08-03/calendar.json').read_text(encoding='utf-8'))
    events=[]
    for eid,c,t,s in [('a','X-1','t1','S1'),('b','X-1','t1','S1'),('c','X-1','t1','S2'),('d','XI-1','t2','S1')]:
        events.append({'id':eid,'class_ids':[c],'teacher_codes':[t],'subject_id':s,
            'time':{'jp':1,'kbm_minutes':45,'breaks':[]},'source_evidence':{'synthetic':eid},
            'reference':{'day':'selasa','start_period':1,'end_period':1}})
    data={'id':'SYNTHETIC-TEST-ONLY','status':'synthetic','events':events,'calendar':calendar,
          'subjects':[{'id':'S1'},{'id':'S2'}],'teachers':[{'source_code':'t1'},{'source_code':'t2'}]}
    rules={'rules_version':'TEST','weights_version':'TEST','normalization_version':'TEST','reviewed_for_dataset':True,
           'difficulty':{'version':'TEST','source':'synthetic unit test, not interview','daily_limit':1,
                         'by_grade':{'X':{'S1':'HIGH','S2':'HIGH'},'XI':{'S1':'MEDIUM','S2':'LOW'}}},
           'weights':{'SC1':.25,'SC2':.25,'SC3':.5},
           'normalization':{'method':'divide_by_upper_bound','source':'explicit synthetic test formula'},'additional_constraints':{},
           'friday_jp_limits':{'X':9,'XI':7,'XII':7}}
    rules['normalization']['upper_bounds']=raw_bounds(data,rules)
    return data,rules

class FinalMethodologyTests(unittest.TestCase):
    def setUp(self):
        self.data,self.rules=fixture();self.m=FinalModel(self.data,self.rules)
        self.x={'a':('selasa',1),'b':('selasa',2),'c':('selasa',3),'d':('selasa',1)}
    def cfg(self,**updates):return {**configurations(4)[1],'budget':450,'stagnation':100,'L':10,**updates}
    def test_school_missing_data_blocked(self):
        rules=json.loads((ROOT/'config/final-school.pending.json').read_text(encoding='utf-8'))
        data=json.loads((ROOT/'data/reconciliation/2026-08-03/candidate-dataset.json').read_text(encoding='utf-8'))
        self.assertTrue(readiness(data,rules))
        with self.assertRaises(ConfigBlocked):FinalModel(data,rules)
    def test_user_grade_categories_preserved(self):
        rules=json.loads((ROOT/'config/final-school.pending.json').read_text(encoding='utf-8'))
        self.assertEqual(rules['difficulty']['by_grade']['XI']['BIO'],'HIGH')
        self.assertEqual(rules['difficulty']['by_grade']['X']['BIO'],'MEDIUM')
        self.assertIsNone(rules['difficulty']['by_grade']['XI']['MAT P'])
    def test_confirmed_family_is_only_for_sc3(self):
        self.rules['difficulty']['subject_families']={'S1':'shared','S2':'shared'}
        self.rules['normalization']['upper_bounds']=raw_bounds(self.data,self.rules)
        m=FinalModel(self.data,self.rules);score=m.score(self.x)
        self.assertEqual(score['raw']['SC3'],0)
        self.assertEqual(score['raw']['SC2'],1)
        self.assertEqual(m.events['c']['subject_id'],'S2')
        self.assertTrue(score_equal(score,validate(m,m.timetable(self.x))))
    def test_timetable_payload_cannot_mutate_registry(self):
        rows=self.m.timetable(self.x);rows[0]['teacher_codes'].append('bad')
        rows[0]['source_metadata']['synthetic']='bad'
        self.assertEqual(self.m.events['a']['teacher_codes'],['t1'])
        self.assertEqual(self.m.events['a']['source_evidence'],{'synthetic':'a'})
    def test_sc3_distinct_and_grade_specific(self):
        score=self.m.score(self.x)
        self.assertEqual(score['raw'],{'SC1':0,'SC2':1,'SC3':1})
        self.assertEqual(score['F'],2*score['H']+score['S'])
        self.assertTrue(0<=score['S']<=1)
    def test_gap_is_internal_excludes_break(self):
        x={'a':('selasa',6),'b':('selasa',7),'c':('rabu',1),'d':('selasa',1)}
        self.assertEqual(self.m.score(x)['raw']['SC1'],0)
        x['a']=('selasa',5)
        self.assertEqual(self.m.score(x)['raw']['SC1'],1)
    def test_hard_priority_and_reference_agree(self):
        x=self.x.copy();x['b']=x['a']
        a=self.m.score(x);b=validate(self.m,self.m.timetable(x))
        self.assertTrue(score_equal(a,b));self.assertEqual(a['H'],2)
        self.assertGreater(a['F'],self.m.score(self.x)['F'])
    def test_randomized_evaluator_independence(self):
        rng=random.Random(45)
        for _ in range(50):
            x={e:rng.choice(self.m.domains[e]) for e in self.m.ids}
            self.assertTrue(score_equal(self.m.score(x),validate(self.m,self.m.timetable(x))))
    def test_validator_does_not_call_search_scorer(self):
        self.m.score=lambda *_:self.fail('Validator used search scorer')
        self.assertEqual(validate(self.m,self.m.timetable(self.x))['H'],0)
    def test_validator_missing_duplicate_extra(self):
        rows=self.m.timetable(self.x)
        for changed in [rows[:-1],rows+[rows[0]],rows+[{'event_id':'unknown'}]]:
            self.assertGreater(validate(self.m,changed)['HC']['HC3'],0)
    def test_validator_all_immutable_payload(self):
        for field,value in [('teacher_codes',['unknown']),('class_ids',['XI-2']),('subject_id','S2'),('source_metadata',{})]:
            rows=self.m.timetable(self.x);rows[0][field]=value
            self.assertGreater(validate(self.m,rows)['HC']['HC6'],0)
    def test_validator_minutes_jp_breaks_fixed(self):
        for field,value in [('jp',2),('kbm_minutes',40),('breaks',[{'after_jp':1,'minutes':15}])]:
            rows=self.m.timetable(self.x);rows[0][field]=value
            self.assertGreater(validate(self.m,rows)['HC']['HC4'],0)
        rows=self.m.timetable(self.x);rows[0].update(day='senin',start_period=1,end_period=1)
        self.assertGreater(validate(self.m,rows)['HC']['HC5'],0)
    def test_constructor_domain_order_and_reproducible(self):
        self.data['events'][0]['time']={'jp':3,'kbm_minutes':125,'breaks':[{'after_jp':1,'minutes':45}]}
        self.rules['normalization']['upper_bounds']=raw_bounds(self.data,self.rules)
        m=FinalModel(self.data,self.rules)
        a=construct(m,random.Random(7));b=construct(m,random.Random(7))
        self.assertEqual(a,b);self.assertEqual(a[2][0],'a')
        self.assertEqual(a[1],sum(map(len,m.domains.values())))
    def test_constructor_all_conflicting_chooses_least_hard(self):
        m=copy.deepcopy(self.m)
        m.domains={e:[('selasa',1)] for e in m.ids};m.allowed={e:set(v) for e,v in m.domains.items()}
        x,_,_=construct(m,random.Random(1))
        self.assertEqual(len(x),4);self.assertGreater(m.score(x)['H'],0)
    def test_attempt_limit_and_no_change(self):
        m=copy.deepcopy(self.m);m.domains={e:[self.x[e]] for e in m.ids};m.allowed={e:set(v) for e,v in m.domains.items()};m.swap_pairs=[]
        y,meta=neighbor(m,self.x,random.Random(2))
        self.assertEqual(y,self.x);self.assertEqual(meta['attempts'],50);self.assertEqual(meta['operator'],'no_change')
    def test_swap_does_not_require_same_class_and_preserves_signature(self):
        self.assertIn(('a','d'),self.m.swap_pairs)
        self.data['events'][0]['time']={'jp':2,'kbm_minutes':90,'breaks':[]}
        self.rules['normalization']['upper_bounds']=raw_bounds(self.data,self.rules)
        self.assertNotIn(('a','d'),FinalModel(self.data,self.rules).swap_pairs)
    def test_calibration_200_formula_and_immutability(self):
        original=copy.deepcopy(self.x)
        info=calibrate(self.m,self.x,self.m.score(self.x),random.Random(3))
        self.assertEqual(self.x,original);self.assertEqual(info['evaluations'],200)
        self.assertGreater(info['positive_count'],0)
        self.assertAlmostEqual(info['T0'],-info['mean_positive_delta']/math.log(.8))
    def test_calibration_fallback_and_no_change_budget(self):
        m=copy.deepcopy(self.m);m.domains={e:[self.x[e]] for e in m.ids};m.allowed={e:set(v) for e,v in m.domains.items()};m.swap_pairs=[]
        info=calibrate(m,self.x,m.score(self.x),random.Random(2))
        self.assertEqual(info['T0'],1);self.assertEqual(info['no_change_evaluations'],200)
        r=run(m,self.cfg(budget=230,stagnation=3),5)
        self.assertEqual(r['evaluations']['search'],3);self.assertEqual(r['stop_reason'],'STAGNATION')
        self.assertEqual(r['candidate_statistics']['no_change_evaluations'],3)
    def test_total_budget_accounting(self):
        r=run(self.m,self.cfg(budget=360,stagnation=999),4)
        c=r['evaluations'];self.assertLessEqual(c['total'],360)
        self.assertEqual(c['total'],sum(v for k,v in c.items() if k!='total'))
        self.assertEqual(c['calibration'],200);self.assertEqual(r['stop_reason'],'TOTAL_EVALUATION_BUDGET')
    def test_temperature_stop(self):
        r=run(self.m,self.cfg(Tmin=1e6),2)
        self.assertEqual(r['stop_reason'],'TMIN');self.assertEqual(r['evaluations']['search'],0)
    def test_seed_repeat_and_best_separate(self):
        a=run(self.m,self.cfg(),17);b=run(self.m,self.cfg(),17)
        json.dumps(a,allow_nan=False)  # Frozen config must be detached for persisted runs.
        for x in [a,b]:x.pop('runtime_seconds')
        self.assertEqual(a,b);self.assertLessEqual(a['best_quality']['F'],a['current_quality']['F'])
        self.assertIsNot(a['best'],a['current'])
    def test_plan_disjoint_seeds_exact_configurations(self):
        p=plan(self.data,self.rules)
        self.assertEqual(len(p['pilot_seeds']),5);self.assertFalse(set(p['pilot_seeds'])&set(p['main_seeds']))
        self.assertEqual([(c['alpha'],c['L']) for c in p['configurations']],[(a,m*4) for m in [1,3] for a in [.9,.95,.98]])
        self.assertFalse(p['main_execution_enabled'])
    def test_recommendations_dedup_rank_validation(self):
        def record(x,seed):return {'dataset_hash':self.m.dataset_hash,'rules_hash':self.m.rules_hash,'seed':seed,
             'configuration':{'id':'C1'},'best_feasible':x,'best_feasible_quality':self.m.score(x)}
        y={e:('rabu',pos[1]) for e,pos in self.x.items()}
        result=select(self.m,[record(self.x,1),record(self.x,2),record(y,3)])
        self.assertEqual(result['unique_feasible_candidates'],2);self.assertEqual(result['selected_count'],2)
        self.assertEqual(canonical(self.x),canonical(dict(reversed(list(self.x.items())))))
        bad=record(self.x,4);bad['best_feasible_quality']['S']=99
        self.assertEqual(select(self.m,[bad])['selected_count'],0)
    def test_normalization_missing_bounds_and_weights_rejected(self):
        for mutate in ['bound','weight','formula']:
            r=copy.deepcopy(self.rules)
            if mutate=='bound':r['normalization']['upper_bounds']['SC1']=0
            if mutate=='weight':r['weights']['SC1']=1
            if mutate=='formula':r['normalization']['method']=None
            with self.assertRaises(ConfigBlocked):FinalModel(self.data,r)
    def test_diversity_against_every_selected_recommendation(self):
        a={str(i):('selasa',1) for i in range(40)}
        b={**a,'0':('rabu',1),'1':('rabu',1)}
        c={**b,'2':('rabu',1)}
        self.assertTrue(sufficiently_different(b,[a],2))
        self.assertTrue(sufficiently_different(c,[a],2))
        self.assertFalse(sufficiently_different(c,[a,b],2))

if __name__=='__main__':unittest.main()
