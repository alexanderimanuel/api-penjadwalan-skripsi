"""SA behavior tests on small synthetic calendars; never run school experiments."""
import copy
import json
import math
import random
import unittest
from unittest.mock import patch

from final_rules import FinalModel, stable_hash
from final_sa import construct, neighbor, calibrate, configurations, metropolis, run
from final_validator import validate
from test_final_foundation import event, model, fixture


def small_model():
    return model([event('a'), event('b'), event('c', group='X-2', subject='S2'),
                  event('d', teacher='t2', subject='S3')])


def config(**changes):
    return {**configurations(4)[1], 'budget': 650, 'stagnation': 300, **changes}


def records(result):
    log = result['search_log']
    return [dict(zip(log['columns'], row)) for row in log['rows']]


class SAEngineTests(unittest.TestCase):
    def test_end_to_end_reproducible_including_every_decision(self):
        m = small_model()
        a, b = run(m, config(), 17), run(m, config(), 17)
        self.assertGreater(a['evaluations']['search'], 0)
        self.assertEqual(a['evaluations']['calibration'], 200)
        self.assertEqual(a['status'], 'FEASIBLE')
        self.assertTrue(validate(m, m.timetable(a['best_feasible']))['feasible'])
        self.assertEqual(a['best_feasible_quality']['H'], 0)
        for result in (a, b):
            result.pop('runtime_seconds')
        self.assertEqual(a, b)
        self.assertEqual(json.dumps(a, sort_keys=True, allow_nan=False), json.dumps(b, sort_keys=True, allow_nan=False))

    def test_all_six_configs_share_initial_and_calibration_for_same_seed(self):
        m = small_model()
        results = [run(m, {**c, 'budget': 340}, 72) for c in configurations(len(m.ids))]
        for r in results[1:]:
            for key in ('initial', 'initial_quality', 'initial_hash', 'initial_order', 'construction_trace', 'calibration', 'T0'):
                self.assertEqual(r[key], results[0][key], key)
        self.assertEqual(len({r['initial_hash'] for r in results}), 1)
        self.assertEqual(results[0]['initial_hash'], stable_hash(results[0]['initial']))

    def test_best_initial_and_feasible_are_detached_from_current(self):
        m = small_model(); r = run(m, config(), 17)
        snapshots = {k: copy.deepcopy(r[k]) for k in ['initial', 'best', 'best_feasible', 'best_quality', 'best_feasible_quality']}
        r['current']['a'] = ('d2', 8)
        r['current'].clear()
        r['current_quality']['HC']['HC1'] = 999
        for key, expected in snapshots.items(): self.assertEqual(r[key], expected)
        self.assertIsNot(r['best'], r['best_feasible'])

    def test_constructor_incremental_trace_matches_partial_penalties(self):
        m = small_model(); trace = []
        x, evaluations, order = construct(m, random.Random(19), trace=trace)
        partial = {}; previous = m.score(partial, partial=True)
        for step in trace:
            partial[step['event_id']] = step['placement']
            score = m.score(partial, partial=True)
            for key in ('F', 'H', 'S'):
                self.assertAlmostEqual(step['additional_'+key], score[key]-previous[key])
            previous = score
        self.assertEqual(partial, x)
        self.assertEqual([s['domain_size'] for s in trace], sorted(s['domain_size'] for s in trace))
        self.assertEqual(evaluations, sum(len(m.domains[e]) for e in order))

    def test_constructor_fallback_selects_least_additional_hard_conflict(self):
        m = model([event('a', group='X-1'), event('b', group='X-2'),
                   event('c', group='X-3'), event('d', group='X-4')])
        # Three singleton events create two collisions at p1 and one at p2.
        m.domains = {'a': [('d1', 1)], 'b': [('d1', 1)], 'c': [('d1', 2)], 'd': [('d1', 1), ('d1', 2)]}
        m.allowed = {e: set(p) for e, p in m.domains.items()}
        trace = []; x, _, _ = construct(m, random.Random(7), trace)
        self.assertEqual(x['d'], ('d1', 2))
        self.assertEqual(trace[-1]['additional_H'], 1)
        self.assertTrue(trace[-1]['hard_conflict_fallback'])

    def test_move_changes_exactly_one_placement_without_mutating_input(self):
        m = small_model(); x, _, _ = construct(m, random.Random(5)); old = copy.deepcopy(x)
        y, meta = neighbor(m, x, random.Random(9), swap_probability=0)
        self.assertEqual(meta['operator'], 'move')
        changed = [eid for eid in x if x[eid] != y[eid]]
        self.assertEqual(len(changed), 1)
        self.assertIn(y[changed[0]], m.allowed[changed[0]])
        self.assertEqual(x, old)

    def test_swap_changes_two_placements_and_preserves_event_identity(self):
        m = small_model(); x = {'a': ('d1', 1), 'b': ('d1', 2), 'c': ('d1', 3), 'd': ('d1', 4)}
        before = stable_hash(m.data)
        y, meta = neighbor(m, x, random.Random(9), swap_probability=1)
        changed = [eid for eid in x if x[eid] != y[eid]]
        self.assertEqual(meta['operator'], 'swap'); self.assertEqual(len(changed), 2)
        a, b = changed
        self.assertEqual((y[a], y[b]), (x[b], x[a]))
        self.assertIn(y[a], m.allowed[a]); self.assertIn(y[b], m.allowed[b])
        self.assertEqual(stable_hash(m.data), before)

    def test_swap_rejects_different_minutes_breaks_or_reciprocal_domain(self):
        m = model([event('a', jp=2), event('b', jp=2, breaks=((1, 15),))])
        self.assertNotIn(('a', 'b'), m.swap_pairs)
        y, meta = neighbor(m, {'a': ('d1', 1), 'b': ('d1', 2)}, random.Random(5), swap_probability=1)
        self.assertEqual(meta['operator'], 'no_change')
        self.assertEqual(meta['attempts'], 50)
        self.assertEqual(meta['attempted_operators'], {'swap': 50})
        m = model([event('a'), event('b')])
        m.allowed['a'] = {('d1', 1)}; m.allowed['b'] = {('d1', 2)}
        x = {'a': ('d1', 1), 'b': ('d1', 2)}
        y, meta = neighbor(m, x, random.Random(1), swap_probability=1)
        self.assertEqual(y, x); self.assertEqual(meta['rejections']['swap_target_incompatible'], 50)

    def test_same_jp_different_actual_minutes_is_not_a_swap_pair(self):
        data, rules = fixture([event('a', jp=2), event('b', jp=2)])
        data['events'][1]['time']['kbm_minutes'] = 90
        start = 480
        for s in data['calendar']['days'][1]['segments']:
            duration = 45 if s['kind'] == 'kbm' else s['duration_minutes']
            s.update(start_minute=start, end_minute=start+duration, duration_minutes=duration)
            start += duration
        m = FinalModel(data, rules)
        self.assertTrue(m.domains['a']); self.assertTrue(m.domains['b'])
        self.assertNotIn(('a', 'b'), m.swap_pairs)

    def test_no_change_counts_one_evaluation_not_fifty(self):
        m = model([event('a')]); m.domains = {'a': [('d1', 1)]}; m.allowed = {'a': {('d1', 1)}}
        r = run(m, config(budget=260, stagnation=5), 99)
        self.assertEqual(r['stop_reason'], 'STAGNATION')
        self.assertEqual(r['evaluations']['search'], 5)
        self.assertEqual(r['candidate_statistics']['generation_attempts'], 250)
        self.assertEqual(r['calibration']['no_change_evaluations'], 200)
        self.assertEqual(r['T0'], 1)
        for row in records(r):
            self.assertEqual(row['generation_status'], 'no_change')
            self.assertEqual(row['generation_attempts'], 50)
            self.assertEqual(sum(row['attempted_operators'].values()), 50)
            self.assertEqual(sum(row['rejection_reasons'].values()), 50)
            self.assertEqual(row['delta'], 0)
            self.assertEqual(row['accepted_changes'], [])

    def test_calibration_uses_unchanged_initial_and_positive_deltas_only(self):
        m = small_model(); x, _, _ = construct(m, random.Random(3)); original = copy.deepcopy(x)
        info = calibrate(m, x, m.score(x), random.Random(44))
        self.assertEqual(x, original)
        self.assertEqual(len(info['sample_log']['rows']), 200)
        positive = [r[2] for r in info['sample_log']['rows'] if r[2] > 0]
        self.assertGreater(len(positive), 0)
        self.assertEqual(info['positive_count'], len(positive))
        self.assertAlmostEqual(info['T0'], -(sum(positive)/len(positive))/math.log(.8))

    def test_metropolis_fresh_draws_acceptance_rejection_and_boundaries(self):
        class Draws:
            def __init__(self): self.values = iter([.1, .9]); self.calls = 0
            def random(self): self.calls += 1; return next(self.values)
        rng = Draws()
        self.assertEqual(metropolis(0, 1, rng), (True, 1, None))
        self.assertEqual(metropolis(-1, 1, rng), (True, 1, None))
        a = metropolis(math.log(2), 1, rng); b = metropolis(math.log(2), 1, rng)
        self.assertTrue(a[0]); self.assertFalse(b[0]); self.assertEqual(rng.calls, 2)
        self.assertAlmostEqual(a[1], .5)
        self.assertEqual((a[2], b[2]), (.1, .9))

    def test_infeasible_current_never_overwrites_feasible_recommendation(self):
        m = model([event('a'), event('b')]); good = {'a': ('d1', 1), 'b': ('d1', 2)}
        bad = {'a': ('d1', 1), 'b': ('d1', 1)}
        meta = {'operator': 'move', 'attempts': 1, 'rejections': {}, 'attempted_operators': {'move': 1}, 'status': 'structural_change'}
        def forced_neighbor(*args, **kwargs): return bad.copy(), meta.copy()
        with patch('final_sa.construct', return_value=(good.copy(), 2, ['a', 'b'])):
            with patch('final_sa.neighbor', side_effect=forced_neighbor):
                with patch('final_sa.metropolis', return_value=(True, 1.0, 0.0)):
                    # Real scores/validator; only proposals/acceptance controlled.
                    r = run(m, config(budget=240, stagnation=1), 11)
        self.assertGreater(r['current_quality']['H'], 0)
        self.assertEqual(r['best_feasible'], good)
        self.assertEqual(r['best'], good)
        self.assertEqual(r['best_feasible_quality']['H'], 0)

    def test_no_feasible_solution_is_reported_without_recommendation(self):
        m = model([event('a'), event('b')])
        m.domains = {e: [('d1', 1)] for e in m.ids}; m.allowed = {e: {('d1', 1)} for e in m.ids}
        r = run(m, config(budget=240, stagnation=3), 8)
        self.assertEqual(r['status'], 'NO_FEASIBLE_FOUND')
        self.assertIsNone(r['best_feasible']); self.assertIsNone(r['best_feasible_quality'])
        self.assertGreater(r['best_quality']['H'], 0)

    def test_log_replays_current_best_and_best_feasible_without_full_snapshots(self):
        m = small_model(); r = run(m, config(), 42)
        current = copy.deepcopy(r['initial']); best = copy.deepcopy(current)
        bs = m.score(best); feasible = copy.deepcopy(best) if bs['feasible'] else None
        fs = m.score(feasible) if feasible is not None else None
        previous = m.score(current)
        rows = records(r)
        self.assertEqual(len(rows), r['evaluations']['search'])
        for index, row in enumerate(rows, 1):
            self.assertEqual(row['search_evaluation'], index)
            for eid, day, start in row['accepted_changes']: current[eid] = (day, start)
            score = m.score(current)
            self.assertEqual(row['current_F'], score['F'])
            self.assertEqual(row['current_H'], score['H']); self.assertEqual(row['current_S'], score['S'])
            if row['accepted']: self.assertAlmostEqual(row['delta'], score['F']-previous['F'])
            else: self.assertEqual(row['accepted_changes'], [])
            if row['random_draw'] is not None:
                self.assertEqual(row['accepted'], row['random_draw'] < row['acceptance_probability'])
                self.assertAlmostEqual(row['acceptance_probability'], math.exp(-row['delta']/row['temperature']))
            if score['F'] < bs['F']: best, bs = copy.deepcopy(current), score
            if score['feasible'] and (fs is None or score['S'] < fs['S']): feasible, fs = copy.deepcopy(current), score
            self.assertEqual(row['best_F'], bs['F'])
            self.assertEqual(row['best_feasible_F'], None if fs is None else fs['F'])
            previous = score
        self.assertEqual(current, r['current']); self.assertEqual(best, r['best']); self.assertEqual(feasible, r['best_feasible'])

    def test_cooling_occurs_exactly_after_L_search_evaluations(self):
        r = run(small_model(), config(alpha=.9, L=4, budget=340), 42)
        rows = records(r); self.assertGreater(len(rows), 8)
        for i, row in enumerate(rows):
            self.assertAlmostEqual(row['temperature'], r['T0']*.9**(i//4))
        self.assertAlmostEqual(r['final_temperature'], r['T0']*.9**(len(rows)//4))

    def test_budget_exact_includes_construction_calibration_and_validation(self):
        r = run(small_model(), config(budget=340), 5)
        ev = r['evaluations']
        self.assertEqual(r['stop_reason'], 'TOTAL_EVALUATION_BUDGET')
        self.assertEqual(ev['total'], 340)
        self.assertEqual(ev['total'], sum(v for k, v in ev.items() if k != 'total'))
        self.assertEqual(records(r)[-1]['evaluation_index'], 337)
        self.assertEqual(r['stop_snapshot']['reserved_final_validations'], 3)

    def test_temperature_early_stop_after_cooling(self):
        m = small_model(); first = run(m, config(budget=330), 19)
        r = run(m, config(alpha=.5, L=4, Tmin=first['T0']*.75), 19)
        self.assertEqual(r['stop_reason'], 'TMIN')
        self.assertEqual(r['evaluations']['search'], 4)

    def test_stagnation_counts_search_only_and_equal_best_does_not_reset(self):
        m = model([event('a'), event('b')])
        bad = {'a': ('d1', 1), 'b': ('d1', 1)}
        good = {'a': ('d1', 1), 'b': ('d1', 2)}
        calls = 0
        meta = {'operator': 'move', 'attempts': 1, 'rejections': {}, 'attempted_operators': {'move': 1}, 'status': 'structural_change'}
        def proposal(*args, **kwargs):
            nonlocal calls
            calls += 1
            # 200 calibration proposals, then improvement, deterioration, equal best.
            x = bad if calls <= 200 or calls == 202 else good
            return x.copy(), meta.copy()
        with patch('final_sa.construct', return_value=(bad.copy(), 2, ['a', 'b'])):
            with patch('final_sa.neighbor', side_effect=proposal):
                with patch('final_sa.metropolis', return_value=(True, 1.0, 0.0)):
                    r = run(m, config(budget=240, stagnation=2), 11)
        self.assertEqual(r['evaluations']['search'], 3)
        self.assertEqual(r['stop_reason'], 'STAGNATION')
        self.assertEqual(r['stagnation_search_evaluations'], 2)
        self.assertEqual(len(r['trace']), 1)
        self.assertEqual(r['trace'][0]['search_evaluations'], 1)

    def test_bad_parameters_fail_before_search(self):
        m = small_model()
        for bad in [{'alpha': float('nan')}, {'L': 0}, {'budget': 100001}, {'budget': 5}, {'Tmin': 0}, {'max_attempts': 51}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError): run(m, config(**bad), 1)


if __name__ == '__main__':
    unittest.main()
