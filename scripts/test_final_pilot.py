"""Pilot gates and deliberate corruption detection; small synthetic runs only."""
import copy
import math
import unittest
from unittest.mock import patch

from export_august import read_json
from final_pilot import (DEFAULT_CONFIG, PilotAnomaly, AuditedModel, audit_run,
                         engine_config, freeze_snapshot, quality_problems, select_inputs, validate_protocol)
from final_sa import run
from test_final_sa_engine import small_model, config


class PilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = small_model()
        cls.result = run(cls.model, config(budget=340), 42)
        cls.protocol = read_json(DEFAULT_CONFIG)

    def test_five_disjoint_pilot_seeds_and_thirty_main_seeds(self):
        validate_protocol(self.protocol)
        for field, values in [('pilot_seeds', [0, 1, 2, 3, 4]), ('pilot_seeds', [1000]*5), ('main_seeds', [0]*30)]:
            bad = copy.deepcopy(self.protocol); bad[field] = values
            with self.subTest(field=field, values=values), self.assertRaises(ValueError): validate_protocol(bad)

    def test_incomplete_school_uses_explicit_synthetic_and_blocks_main(self):
        m, context = select_inputs(self.protocol)
        self.assertEqual(context['dataset_kind'], 'SYNTHETIC_TECHNICAL_VERIFICATION')
        self.assertEqual(context['main_experiment_status'], 'BLOCKED BY REAL DATA')
        self.assertTrue(context['blockers'])
        self.assertEqual(m.data['status'], 'synthetic')
        self.assertEqual(len(m.ids), 24)
        self.assertIn('NOT school', context['selected_difficulty_source'])

    def test_engine_parameters_come_from_protocol(self):
        r = engine_config(self.protocol, 24)
        self.assertEqual((r['id'], r['alpha'], r['L']), ('C2', .95, 24))
        self.assertEqual(r['budget'], self.protocol['max_evaluations'])
        self.assertEqual(r['stagnation'], self.protocol['stagnation_limit'])
        self.assertEqual(r['calibration_samples'], self.protocol['calibration_samples'])
        self.assertEqual(r['max_attempts'], self.protocol['max_candidate_attempts'])

    def test_clean_result_passes_replay_and_export_audit(self):
        rows = self.model.timetable(self.result['best_feasible'])
        a = audit_run(self.model, self.result, stride=5, exported_rows=rows)
        self.assertEqual(a['status'], 'PASS', a)
        self.assertEqual(a['replayed_search_evaluations'], self.result['evaluations']['search'])

    def test_nan_negative_and_out_of_range_normalization_detected(self):
        for target, key, value in [('raw', 'SC1', -1), ('normalized', 'SC3', 1.01), ('raw', 'SC2', math.nan)]:
            with self.subTest(target=target):
                r = copy.deepcopy(self.result); r['best_quality'][target][key] = value
                self.assertEqual(audit_run(self.model, r)['status'], 'FAIL')

    def test_missing_duplicate_and_invalid_event_placements_detected(self):
        r = copy.deepcopy(self.result); r['current'].pop('a')
        self.assertEqual(audit_run(self.model, r)['status'], 'FAIL')
        r = copy.deepcopy(self.result); r['best']['a'] = ('d1', 999)
        self.assertEqual(audit_run(self.model, r)['status'], 'FAIL')
        rows = self.model.timetable(self.result['best_feasible'])
        self.assertEqual(audit_run(self.model, self.result, exported_rows=rows+[rows[0]])['status'], 'FAIL')
        self.assertEqual(audit_run(self.model, self.result, exported_rows=rows[:-1])['status'], 'FAIL')

    def test_export_attribute_mutation_detected(self):
        for field, value in [('teacher_codes', ['other']), ('class_ids', ['X-other']),
                             ('subject_id', 'other'), ('jp', 2), ('kbm_minutes', 999),
                             ('breaks', [{'after_jp': 1, 'minutes': 10}]), ('source_metadata', {})]:
            with self.subTest(field=field):
                rows = self.model.timetable(self.result['best_feasible']); rows[0][field] = value
                self.assertEqual(audit_run(self.model, self.result, exported_rows=rows)['status'], 'FAIL')

    def test_best_feasible_cannot_contain_hard_violation(self):
        r = copy.deepcopy(self.result)
        r['best_feasible']['b'] = r['best_feasible']['a']
        r['best_feasible_quality'] = self.model.score(r['best_feasible'])
        self.assertFalse(r['best_feasible_quality']['feasible'])
        self.assertEqual(audit_run(self.model, r)['status'], 'FAIL')

    def test_log_corruption_and_wrong_stop_reason_detected(self):
        for column, value in [('temperature', math.nan), ('generation_attempts', 51), ('current_F', -1)]:
            r = copy.deepcopy(self.result)
            index = r['search_log']['columns'].index(column); r['search_log']['rows'][0][index] = value
            self.assertEqual(audit_run(self.model, r)['status'], 'FAIL')
        r = copy.deepcopy(self.result); r['stop_reason'] = 'STAGNATION'
        self.assertEqual(audit_run(self.model, r)['status'], 'FAIL')

    def test_online_monitor_checks_rejected_candidate_quality_too(self):
        m = small_model(); x = self.result['initial']; bad = m.score(x)
        bad['normalized']['SC1'] = 2
        monitor = AuditedModel(m, 250)
        with patch.object(m, 'score', return_value=bad), self.assertRaises(PilotAnomaly): monitor.score(x)

    def test_freeze_never_promotes_synthetic_to_main_ready(self):
        _, context = select_inputs(self.protocol)
        summaries = [{'seed': s, 'audit': {'status': 'PASS'}, 'evaluations': {'search': 10},
                      'move_count': 5, 'swap_count': 5, 'accepted_structural_changes': 2, 'warnings': []}
                     for s in self.protocol['pilot_seeds']]
        frozen = freeze_snapshot(self.protocol, context, summaries, {}, {})
        self.assertFalse(frozen['ready_for_main_experiment'])
        self.assertFalse(frozen['main_execution_authorized'])
        self.assertEqual(frozen['freeze_scope'], 'TECHNICAL_VERIFICATION_ONLY')
        self.assertEqual(frozen['main_experiment_status'], 'BLOCKED BY REAL DATA')
        with self.assertRaises(PilotAnomaly): freeze_snapshot(self.protocol, context, summaries[:-1], {}, {})
        summaries[0]['audit']['status'] = 'FAIL'
        with self.assertRaises(PilotAnomaly): freeze_snapshot(self.protocol, context, summaries, {}, {})


if __name__ == '__main__': unittest.main()
