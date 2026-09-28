import copy
import unittest

from diagnose_school_search import repair_baseline, diagnose_run
from final_rules import FinalModel
from final_sa import run
from final_validator import validate
from test_final_foundation import event, fixture
from test_final_sa_engine import small_model, config


class DiagnosticTests(unittest.TestCase):
    def test_repair_changes_placement_only_and_preserves_baseline(self):
        events=[event('a',group='X-1'),event('b',group='X-2')]
        for e in events:e['reference']={'day':'d1','start_period':1}
        m=FinalModel(*fixture(events));before=copy.deepcopy(events)
        result=repair_baseline(m)
        self.assertEqual(result['baseline_quality']['H'],1)
        self.assertEqual(result['selected']['changed_event_count'],1)
        self.assertTrue(validate(m,m.timetable(result['selected']['placement']))['feasible'])
        self.assertEqual(events,before)
        self.assertEqual(result['baseline'],{'a':('d1',1),'b':('d1',1)})
        self.assertEqual(repair_baseline(m)['selected']['placement'],result['selected']['placement'])

    def test_already_feasible_baseline_kept(self):
        events=[event('a')];events[0]['reference']={'day':'d1','start_period':1}
        result=repair_baseline(FinalModel(*fixture(events)))
        self.assertEqual(result['selected']['changed_event_count'],0)
        self.assertEqual(result['tested_candidates'],[])

    def test_no_feasible_repair_is_not_fabricated(self):
        events=[event('a'),event('b')]
        for e in events:e['reference']={'day':'d1','start_period':1}
        m=FinalModel(*fixture(events))
        # Deliberately bound this fixture's repair domain to its occupied slot.
        m.domains={e:[('d1',1)] for e in m.ids};m.swap_pairs=[]
        result=repair_baseline(m)
        self.assertIsNone(result['selected'])
        self.assertEqual(result['feasible_candidate_count'],0)

    def test_valid_log_and_corrupted_cooling_are_distinguished(self):
        m=small_model();result=run(m,config(budget=330),71)
        self.assertEqual(diagnose_run(m,result)['anomalies'],[])
        altered=copy.deepcopy(result)
        column=altered['search_log']['columns'].index('temperature')
        altered['search_log']['rows'][0][column]*=2
        self.assertIn('cooling mismatch',diagnose_run(m,altered)['anomalies'])

    def test_log_from_other_rules_is_rejected(self):
        m=small_model();result=run(m,config(budget=330),71)
        result['rules_hash']='other'
        with self.assertRaisesRegex(ValueError,'identity mismatch'):diagnose_run(m,result)


if __name__=='__main__':unittest.main()
