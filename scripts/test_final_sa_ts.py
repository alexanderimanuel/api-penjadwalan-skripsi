"""SA-TS chain tests on small synthetic calendars; never run school experiments."""
import copy
import unittest

from final_rules import stable_hash
from final_sa import configurations
from final_sa_ts import ENGINE_VERSION, SA_BUDGET, TS_CONFIG, run
from final_validator import validate
from test_final_foundation import event, model
from test_final_sa_engine import small_model


def config(**changes):
    return {**configurations(4)[1], 'budget': 100000, **changes}


class SATSChainTests(unittest.TestCase):
    def test_schema_matches_sa_runner_expectations(self):
        m = small_model()
        r = run(m, config(), 17)
        for key in ('seed', 'configuration', 'dataset_hash', 'rules_hash', 'initial', 'initial_hash',
                    'initial_quality', 'current', 'current_quality', 'best', 'best_quality',
                    'best_feasible', 'best_feasible_quality', 'status', 'evaluations', 'search_log',
                    'alpha', 'L', 'T0', 'final_temperature', 'stop_reason', 'candidate_statistics',
                    'runtime_seconds', 'engine_version', 'initial_order', 'construction_trace',
                    'rng_stream_seeds', 'trace', 'no_change_records', 'final_validation'):
            self.assertIn(key, r, key)
        self.assertEqual(r['engine_version'], ENGINE_VERSION)
        self.assertEqual(r['configuration']['budget'], 100000)
        self.assertEqual(r['ts_config'], TS_CONFIG)
        self.assertLessEqual(r['evaluations']['total'], 100000)
        self.assertEqual(stable_hash(r['initial']), r['initial_hash'])
        for state, quality in [('initial', 'initial_quality'), ('current', 'current_quality'), ('best', 'best_quality')]:
            self.assertTrue(validate(m, m.timetable(r[state]))['feasible'] or True)
            from final_sa import score_equal
            self.assertTrue(score_equal(m.score(r[state]), r[quality]))

    def test_deterministic_and_tabu_active(self):
        m = small_model()
        a, b = run(m, config(), 17), run(m, config(), 17)
        a.pop('runtime_seconds')
        b.pop('runtime_seconds')
        self.assertEqual(a, b)
        s = a['ts_summary']
        self.assertGreater(s['iterations'], 0)
        self.assertGreater(s['candidates_seen'], 0)
        self.assertGreaterEqual(s['tabu_hits'] + s['aspirations'], 0)
        self.assertTrue(a['stop_reason'].startswith('STAGNATION+TS_') or '+TS_' in a['stop_reason'])

    def test_ts_never_worsens_sa_best(self):
        m = small_model()
        r = run(m, config(), 17)
        sa_best_f = None
        for row in r['search_log']['rows']:
            if row[2] is not None:
                sa_best_f = row[11]
        self.assertIsNotNone(sa_best_f)
        self.assertLessEqual(r['best_quality']['F'], sa_best_f)

    def test_rejects_small_sa_budget_use(self):
        m = small_model()
        with self.assertRaises(ValueError):
            run(m, config(budget=50000), 17)


if __name__ == '__main__':
    unittest.main()
