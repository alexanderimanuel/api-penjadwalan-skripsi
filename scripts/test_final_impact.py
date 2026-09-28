import copy
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from final_impact import build_report, change, write_report, validate
from final_rules import FinalModel, raw_bounds
from test_final_foundation import event, fixture


def impact_fixture():
    events = [event('a'), event('b'), event('c',teacher='t2',group='X-2'), event('d',teacher='t2',group='X-2')]
    positions = {'a':1,'b':4,'c':1,'d':2}
    for e in events: e['reference'] = {'day':'d1','start_period':positions[e['id']]}
    return FinalModel(*fixture(events))


def recommendation(model, label='A', positions=None):
    positions = positions or {'a':('d1',1),'b':('d1',2),'c':('d1',1),'d':('d1',3)}
    return {'label':label, 'dataset_hash':model.dataset_hash, 'rules_hash':model.rules_hash,
            'timetable':model.timetable(positions)}


class ImpactTests(unittest.TestCase):
    def test_zero_baseline_has_na_percentage_and_absolute_difference(self):
        row = change(0,3)
        self.assertIsNone(row['percentage_change'])
        self.assertEqual(row['percentage_status'],'N/A_BASELINE_ZERO')
        self.assertEqual(row['difference'],3)
        self.assertEqual(row['absolute_difference'],3)
        row = change(4,1)
        self.assertEqual(row['percentage_change'],-75)
        self.assertEqual(row['difference'],-3)
        self.assertEqual(row['absolute_difference'],3)

    def test_total_improvement_can_include_teacher_with_more_gaps(self):
        model = impact_fixture(); report = build_report(model,[recommendation(model)])
        self.assertEqual(report['evaluations']['BASELINE']['raw']['SC1'],2)
        self.assertEqual(report['evaluations']['A']['raw']['SC1'],1)
        self.assertEqual(report['teacher_gap_change_distribution']['A'],{'DECREASED':1,'UNCHANGED':0,'INCREASED':1})
        self.assertLess(report['evaluations']['A']['S'],report['evaluations']['BASELINE']['S'])
        self.assertEqual(len(report['details']['BASELINE']['sc1_teacher_day']),4)

    def test_fresh_independent_validation_for_every_schedule(self):
        model=impact_fixture()
        with patch('final_impact.validate',wraps=validate) as calls:
            report=build_report(model,[recommendation(model,label) for label in 'ABC'])
        self.assertEqual(calls.call_count,4)
        self.assertEqual(report['recommendation_labels'],list('ABC'))

    def test_reject_mismatched_provenance_and_immutable_mutation(self):
        model=impact_fixture()
        for key in ('dataset_hash','rules_hash'):
            rec=recommendation(model);rec[key]='different'
            with self.assertRaisesRegex(ValueError,'mismatch'):build_report(model,[rec])
        rec=recommendation(model);rec['timetable'][0]['teacher_codes']=['other']
        with self.assertRaisesRegex(ValueError,'not independently feasible'):build_report(model,[rec])

    def test_infeasible_baseline_allowed_but_not_recommendation(self):
        events=[event('a',group='X-1'),event('b',group='X-2')]
        for e in events:e['reference']={'day':'d1','start_period':1}
        model=FinalModel(*fixture(events))
        rec=recommendation(model,positions={'a':('d1',1),'b':('d1',2)})
        report=build_report(model,[rec])
        self.assertEqual(report['evaluations']['BASELINE']['HC']['HC1'],1)
        self.assertFalse(report['evaluations']['BASELINE']['feasible'])
        self.assertTrue(report['evaluations']['A']['feasible'])
        rec['timetable'][1]['start_period']=1;rec['timetable'][1]['end_period']=1
        with self.assertRaises(ValueError):build_report(model,[rec])

    def test_sc2_pairs_class_day_and_subject_details_reconcile(self):
        events=[event('a'),event('b'),event('c')]
        for e,p in zip(events,[1,2,3]):e['reference']={'day':'d1','start_period':p}
        model=FinalModel(*fixture(events))
        rec=recommendation(model,positions={'a':('d1',1),'b':('d1',2),'c':('d2',1)})
        report=build_report(model,[rec])
        self.assertEqual(report['evaluations']['BASELINE']['raw']['SC2'],3)
        self.assertEqual(report['evaluations']['A']['raw']['SC2'],1)
        self.assertEqual(report['sc2_most_affected_class_days']['A'][0]['difference'],-2)

    def test_sc3_distinct_family_excess_and_high_jp_are_separate(self):
        events=[event('a',subject='S1',jp=2),event('b',subject='S2'),event('c',subject='S3')]
        for e,p in zip(events,[1,3,4]):e['reference']={'day':'d1','start_period':p}
        data,rules=fixture(events)
        rules['difficulty']['subject_families']={'S1':'Math','S2':'Math','S3':'Science'}
        rules['normalization']['upper_bounds']=raw_bounds(data,rules)
        model=FinalModel(data,rules)
        report=build_report(model,[])
        row=report['details']['BASELINE']['sc3_class_day'][0]
        self.assertEqual(row['high_distinct_subjects'],2)
        self.assertEqual(row['excess'],1)
        self.assertEqual(row['high_subject_jp'],4)
        self.assertEqual(report['details']['BASELINE']['sc3_class_day'][1]['high_subject_jp'],0)

    def test_baseline_only_is_explicit_and_csv_is_usable(self):
        model=impact_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'report'
            report=write_report(model,[],output)
            self.assertEqual(report['status'],'BASELINE_ONLY_NO_FEASIBLE_RECOMMENDATION')
            self.assertEqual(report['unavailable_labels'],list('ABC'))
            self.assertEqual(report['dataset_version'],'FOUNDATION-SYNTHETIC')
            self.assertIn('baseline_raw_value',(output/'comparison-metrics.csv').read_text())
            self.assertIn('belum tersedia',(output/'SUMMARY.md').read_text(encoding='utf-8'))
            with self.assertRaises(ValueError):write_report(model,[],output)

    def test_csv_na_and_no_nan_or_infinity(self):
        model=impact_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'report';write_report(model,[recommendation(model)],output)
            with (output/'comparison-metrics.csv').open(encoding='utf-8',newline='') as handle:
                rows=list(csv.DictReader(handle))
            self.assertEqual(next(r for r in rows if r['metric']=='HC1')['percentage_change'],'N/A')
            text=(output/'report.json').read_text(encoding='utf-8')
            self.assertNotIn('NaN',text);self.assertNotIn('Infinity',text)


if __name__=='__main__':unittest.main()
