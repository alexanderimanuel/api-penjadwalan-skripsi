"""Regression evidence for final audit; fixtures are tests, never experiment input."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from export_august import read_json
from final_experiment import atomic_json, run_id
from final_foundation import thaw
from final_pipeline import (CONFIG, DEFAULT_PILOT, AuditedRunner, canonical_dataset,
    counts, freeze, load_final, select_completed, select_school_inputs)
from final_recommendations import canonical, placement_distance, ranking_key, select
from final_rules import FinalModel, raw_bounds
from test_final_foundation import event, fixture, model


class AuditRegressionTests(unittest.TestCase):
    def test_sc2_required_n_1_2_3_4(self):
        for n, expected in [(1,0),(2,1),(3,3),(4,6)]:
            m = model([event(str(i)) for i in range(n)])
            self.assertEqual(m.evaluate({str(i):('d1',i+1) for i in range(n)})['raw']['SC2'],expected)

    def test_hc7_friday_last_period_per_grade(self):
        from final_foundation import Calendar
        from final_rules import friday_limit
        data, rules = fixture([event('a', group='X-1', jp=2), event('b', group='X-2', jp=2)])
        rules['difficulty']['by_grade'] = {'X': {'S1': 'HIGH'}, 'XI': {'S1': 'HIGH'}, 'XII': {'S1': 'HIGH'}}
        rules['friday_jp_limits'] = {'X': 9, 'XI': 7, 'XII': 7}
        rules['normalization']['upper_bounds'] = raw_bounds(data, rules)
        m = FinalModel(data, rules)
        self.assertEqual(friday_limit(rules, 'X-1'), 9)
        self.assertEqual(friday_limit(rules, 'XI-1'), 7)
        # Non-Friday placements never trigger HC7; validator/scorer agree.
        s = m.score({'a': ('d1', 1), 'b': ('d1', 1)})
        v = m.evaluate({'a': ('d1', 1), 'b': ('d1', 1)})
        self.assertEqual(s['H'], v['H'])
        self.assertEqual(s['HC']['HC7'], 0)
        self.assertEqual(v['HC']['HC7'], 0)
        # Friday period semantics on a minimal jumat calendar (periods 2..9).
        start, segments = 440, []
        for p in range(2, 10):
            segments.append({'period': p, 'kind': 'kbm', 'start_minute': start,
                             'end_minute': start + 40, 'duration_minutes': 40})
            start += 40
        fdata = {'id': 'HC7-SEMANTICS', 'status': 'synthetic',
                 'events': [event('a', group='X-1', jp=2), event('b', group='XI-1', teacher='t2', jp=2)],
                 'calendar': {'days': [{'id': 'jumat', 'segments': segments}]},
                 'teachers': [{'source_code': 't1'}, {'source_code': 't2'}]}
        frules = {'rules_version': 'TEST', 'weights_version': 'TEST', 'normalization_version': 'TEST',
                  'reviewed_for_dataset': True,
                  'difficulty': {'version': 'TEST', 'source': 'synthetic test', 'daily_limit': 1,
                                 'by_grade': {'X': {'S1': 'HIGH'}, 'XI': {'S1': 'HIGH'}}},
                  'weights': {'SC1': .25, 'SC2': .25, 'SC3': .5},
                  'normalization': {'method': 'divide_by_upper_bound', 'source': 'synthetic bounds'},
                  'additional_constraints': {}, 'friday_jp_limits': {'X': 9, 'XI': 7, 'XII': 7}}
        frules['normalization']['upper_bounds'] = raw_bounds(fdata, frules)
        fm = FinalModel(fdata, frules)
        # XI ending at 9 violates (limit 7); X ending at 9 does not (limit 9).
        bad = {'a': ('jumat', 2), 'b': ('jumat', 8)}
        self.assertEqual(fm.score(bad)['HC']['HC7'], 1)
        self.assertEqual(fm.evaluate(bad)['HC']['HC7'], 1)
        self.assertEqual(fm.evaluate(bad)['H'], 1)
        good = {'a': ('jumat', 8), 'b': ('jumat', 2)}
        self.assertEqual(fm.score(good)['HC']['HC7'], 0)
        self.assertEqual(fm.evaluate(good)['HC']['HC7'], 0)
        self.assertTrue(fm.evaluate(good)['feasible'])
        # Real Friday KBM periods are 2..9, so XI/XII are barred from 8-9
        # while the X bound (<=9) never binds on Friday.
        import json as _json
        from model_august import ROOT as _ROOT
        cal = _json.loads((_ROOT/'data/calendar/2026-08-03/calendar.json').read_text(encoding='utf-8'))
        friday = next(d for d in cal['days'] if d['id'] == 'jumat')
        self.assertEqual(sorted(sg['period'] for sg in friday['segments'] if sg['kind'] == 'kbm'),
                         [2, 3, 4, 5, 6, 7, 8, 9])

    def test_hc8_class_day_has_no_holes(self):
        start, segments = 480, []
        for p in range(1, 6):
            segments.append({'period': p, 'kind': 'kbm', 'start_minute': start,
                             'end_minute': start + 40, 'duration_minutes': 40})
            start += 40
        hdata = {'id': 'HC8-SEMANTICS', 'status': 'synthetic',
                 'events': [event('a', group='X-1', jp=1), event('b', group='X-1', jp=1)],
                 'calendar': {'days': [{'id': 'senin', 'segments': segments}]},
                 'teachers': [{'source_code': 't1'}]}
        hrules = {'rules_version': 'TEST', 'weights_version': 'TEST', 'normalization_version': 'TEST',
                  'reviewed_for_dataset': True,
                  'difficulty': {'version': 'TEST', 'source': 'synthetic test', 'daily_limit': 1,
                                 'by_grade': {'X': {'S1': 'HIGH'}}},
                  'weights': {'SC1': .25, 'SC2': .25, 'SC3': .5},
                  'normalization': {'method': 'divide_by_upper_bound', 'source': 'synthetic bounds'},
                  'additional_constraints': {}}
        hrules['normalization']['upper_bounds'] = raw_bounds(hdata, hrules)
        hm = FinalModel(hdata, hrules)
        # Periods 2 and 4 leave KBM period 3 empty inside the span: one hole.
        holed = {'a': ('senin', 2), 'b': ('senin', 4)}
        self.assertEqual(hm.score(holed)['HC']['HC8'], 1)
        self.assertEqual(hm.evaluate(holed)['HC']['HC8'], 1)
        self.assertEqual(hm.evaluate(holed)['details']['HC8'][0]['empty_kbm_periods'], [3])
        # Contiguous periods 2 and 3: feasible.
        tight = {'a': ('senin', 2), 'b': ('senin', 3)}
        self.assertEqual(hm.score(tight)['HC']['HC8'], 0)
        self.assertEqual(hm.evaluate(tight)['HC']['HC8'], 0)
        self.assertTrue(hm.evaluate(tight)['feasible'])

    def test_sc2_multi_jp_is_one_meeting(self):
        m = model([event('a',jp=2),event('b',jp=2)])
        self.assertEqual(m.evaluate({'a':('d1',1),'b':('d1',3)})['raw']['SC2'],1)

    def test_sc3_family_distinct_multi_jp_and_configured_limit(self):
        data,rules = fixture([event('a',subject='MAT',jp=2),event('b',subject='MAT P'),
                              event('c',subject='FIS'),event('d',subject='KIM')])
        rules['difficulty']['subject_families'] = {'MAT':'Matematika','MAT P':'Matematika','FIS':'Fisika','KIM':'Kimia'}
        x = {'a':('d1',1),'b':('d1',3),'c':('d1',4),'d':('d1',6)}
        for limit,expected in [(1,2),(2,1),(3,0)]:
            rules['difficulty']['daily_limit'] = limit
            rules['normalization']['upper_bounds'] = raw_bounds(data,rules)
            m = FinalModel(data,rules)
            self.assertEqual(m.evaluate(x)['raw']['SC3'],expected)
            self.assertEqual(m.evaluate(x)['raw']['SC2'],0)

    def test_identical_meeting_labels_have_zero_distance_and_same_canonical_id(self):
        m = model([event('a'),event('b')]); a = {'a':('d1',1),'b':('d1',2)}
        b = {'a':a['b'],'b':a['a']}
        self.assertEqual(canonical(a,m),canonical(b,m))
        self.assertEqual(placement_distance(a,b,m),0)
        b['a'] = ('d2',2)
        self.assertEqual(placement_distance(a,b,m),1)

    def test_different_immutable_assignments_are_not_canonicalized_together(self):
        m = model([event('a'),event('b',subject='S2')]); a = {'a':('d1',1),'b':('d1',2)}
        b = {'a':a['b'],'b':a['a']}
        self.assertNotEqual(canonical(a,m),canonical(b,m))
        self.assertEqual(placement_distance(a,b,m),2)

    def test_canonical_requires_complete_event_set(self):
        m = model([event('a'),event('b')])
        with self.assertRaises(ValueError): canonical({'a':('d1',1)},m)
        with self.assertRaises(ValueError): placement_distance({'a':('d1',1)},{'a':('d1',2)},m)

    def test_selection_dedup_and_does_not_force_three(self):
        m = model([event('a'),event('b')]); a = {'a':('d1',1),'b':('d1',2)}
        def record(x,seed):
            return {'dataset_hash':m.dataset_hash,'rules_hash':m.rules_hash,'seed':seed,
                'configuration':{'id':'C1'},'best_feasible':x,'best_feasible_quality':m.score(x)}
        r = select(m,[record(a,1),record({'a':a['b'],'b':a['a']},2)])
        self.assertEqual(r['selected_count'],1)
        self.assertEqual(r['unique_feasible_candidates'],1)
        self.assertEqual(len(r['recommendations'][0]['source_runs']),2)

    def test_ranking_tie_breaks_in_exact_final_order(self):
        base = {'score':{'S':.2,'raw':{'SC1':2,'SC2':2,'SC3':2}},'canonical_id':'b'}
        for field in ('S','SC3','SC1','SC2','canonical_id'):
            better = copy.deepcopy(base)
            if field == 'canonical_id': better[field]='a'
            elif field == 'S': better['score'][field]=.1
            else: better['score']['raw'][field]=1
            self.assertLess(ranking_key(better),ranking_key(base))
        candidates=[]
        for s,s3,s1,s2,c in [(.1,9,9,9,'z'),(.2,1,9,9,'z'),(.2,2,1,9,'z'),(.2,2,2,1,'z'),(.2,2,2,2,'a')]:
            candidates.append({'score':{'S':s,'raw':dict(SC3=s3,SC1=s1,SC2=s2)},'canonical_id':c})
        self.assertEqual(sorted(reversed(candidates),key=ranking_key),candidates)

    def test_selection_caps_three_and_rejects_infeasible_and_wrong_identity(self):
        m = model([event('a'),event('b')]); runs=[]
        for seed,period in enumerate((1,2,3,6)):
            x={'a':('d1',period),'b':('d2',period)}
            runs.append({'dataset_hash':m.dataset_hash,'rules_hash':m.rules_hash,'seed':seed,
                'configuration':{'id':'C1'},'best_feasible':x,'best_feasible_quality':m.score(x)})
        invalid=copy.deepcopy(runs[0]);invalid['best_feasible']['b']=('d1',1)
        wrong=copy.deepcopy(runs[0]);wrong['dataset_hash']='wrong'
        result=select(m,[*runs,invalid,wrong])
        self.assertEqual(result['selected_count'],3);self.assertEqual(len(result['rejected_runs']),2)
        self.assertEqual(result['recommendations'][0]['canonical_id'],result['candidate_pool'][0]['canonical_id'])

    def test_final_data_counts_and_one_class_block_per_event(self):
        protocol=read_json(CONFIG);m,_=select_school_inputs(protocol)
        self.assertEqual(counts(m.data),dict(teachers=58,classes=27,subjects=21,events=513,class_jp=1215))
        blocks=[]
        for eid,e in m.events.items():
            source=e['source_evidence'];b=source['class_block'];blocks.append(b['id'])
            self.assertEqual(eid,'E-'+b['id']);self.assertEqual(b['source'],'kelas')
            self.assertTrue(b['raw_text']);self.assertTrue(b['label_ref']['raw'])
            self.assertTrue(all(t['source']=='guru' for t in source['teacher_blocks']))
            self.assertEqual(e['subject_id'],b['subject_id'])
        self.assertEqual(len(blocks),len(set(blocks)))
        envelope=canonical_dataset(m,protocol)
        self.assertEqual(envelope['dataset_version'],m.data['id'])
        self.assertEqual(envelope['dataset_snapshot_version'],'2026-08-03-work-v1')
        for field in ('dataset_version','source_data_version','effective_date','extraction_metadata'):
            self.assertTrue(envelope[field])
        self.assertEqual(envelope['dataset'],thaw(m.data))

    def test_real_final_freeze_has_no_synthetic_substitution_or_numeric_change(self):
        with tempfile.TemporaryDirectory() as td:
            work=Path(td);f=freeze(work,DEFAULT_PILOT);m,loaded=load_final(work)
            self.assertEqual(f,loaded);self.assertEqual(len(m.ids),513)
            self.assertEqual(f['protocol'],read_json(CONFIG));self.assertEqual(len(f['pilot_evidence']),5)
            self.assertEqual(m.rules['normalization']['upper_bounds'],dict(SC1=2146,SC2=126,SC3=155))
            with self.assertRaises(ValueError):select_completed(work)
            _,s=select_completed(work,partial=True)
            self.assertEqual(s['scope'],'DRY_RUN_PARTIAL');self.assertEqual(s['selected_count'],0)
            f['protocol']['p0']=.9;atomic_json(work/'FROZEN_EXPERIMENT_CONFIG.json',f)
            with self.assertRaises(ValueError):load_final(work)

    def test_metadata_timestamps_resume_and_tampering(self):
        m=model([event('a'),event('b')]);config=dict(id='C1',alpha=.9,L=2,budget=260,Tmin=.001,
            stagnation=5,calibration_samples=200,p0=.8,max_attempts=50,swap_probability=.5)
        plan=[{'run_id':run_id('C1',1),'seed':1,'config':config}]
        frozen={'metadata':{'dataset_version':'TEST','source_data_version':'TEST','effective_date':'TEST'},
            'protocol':{},'snapshot_hash':'TEST','code_hashes':{},'environment':{'python':'TEST'}}
        with tempfile.TemporaryDirectory() as td:
            runner=AuditedRunner(m,plan,td,frozen);first=runner.execute()
            self.assertEqual(first['completed_runs'],1)
            folder=Path(td)/'runs'/plan[0]['run_id']/'attempt-0001'
            meta=read_json(folder/'run_metadata.json')
            for key in ('started_at_utc','validated_at_utc','source_data_version','rules_version',
                        'difficulty_version','daily_limit','weights','normalization','environment','code_hashes'):
                self.assertIsNotNone(meta[key])
            with patch('final_experiment.engine.run',side_effect=AssertionError('must skip')):
                self.assertEqual(runner.execute()['attempted_this_invocation'],0)
            meta['daily_limit']=99;atomic_json(folder/'run_metadata.json',meta)
            with patch('final_experiment.engine.run',side_effect=AssertionError('explicit rerun required')):
                result=runner.execute();self.assertEqual(result['failed_runs'],1)


if __name__=='__main__':unittest.main()
