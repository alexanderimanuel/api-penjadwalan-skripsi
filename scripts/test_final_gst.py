"""Greedy-SA-TS chain tests on small synthetic calendars; never run school experiments."""
import unittest

from final_greedy import build
from final_gst import ENGINE_VERSION, run
from final_rules import stable_hash
from final_sa import configurations, score_equal
from final_validator import validate
from test_final_sa_engine import small_model


def config(**changes):
    return {**configurations(4)[1], 'budget': 100000, **changes}


class GSTChainTests(unittest.TestCase):
    def test_greedy_builds_complete_deterministic_placement(self):
        m = small_model()
        a, sa = build(m, 5)
        b, sb = build(m, 5)
        self.assertEqual(set(a), set(m.ids))
        self.assertEqual(a, b)
        self.assertEqual(sa['winner'], sb['winner'])
        for entry in sa['restarts']:
            self.assertIn('H', entry)

    def test_chain_schema_and_budget(self):
        m = small_model()
        r = run(m, config(), 17)
        self.assertEqual(r['engine_version'], ENGINE_VERSION)
        self.assertEqual(r['configuration']['budget'], 100000)
        self.assertLessEqual(r['evaluations']['total'], 100000)
        self.assertIn('greedy_summary', r)
        self.assertIn('ts_summary', r)
        self.assertEqual(stable_hash(r['initial']), r['initial_hash'])
        for state, quality in [('initial', 'initial_quality'), ('best', 'best_quality')]:
            self.assertTrue(score_equal(m.score(r[state]), r[quality]))

    def test_deterministic(self):
        m = small_model()
        a, b = run(m, config(), 17), run(m, config(), 17)
        a.pop('runtime_seconds')
        b.pop('runtime_seconds')
        self.assertEqual(a, b)

    def test_greedy_initial_matches_own_best_restart(self):
        m = small_model()
        r = run(m, config(), 17)
        g = r['greedy_summary']
        winner = min(g['restarts'], key=lambda e: (e['H'], e['F']))
        self.assertEqual((r['initial_quality']['H'], r['initial_quality']['F']), (winner['H'], winner['F']))


if __name__ == '__main__':
    unittest.main()
