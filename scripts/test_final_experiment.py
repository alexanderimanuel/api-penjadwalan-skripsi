"""Transactional/resume tests use tiny synthetic cohorts, not 180 school runs."""
import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import final_sa as engine
from export_august import read_json
from final_experiment import (CohortRunner, MainBlocked, DEFAULT_FROZEN, aggregate, atomic_json,
                              exclusive_lock, instrumented_engine, load_frozen, planned_runs, run_id, validate_result)
from final_pilot import DEFAULT_CONFIG
from final_rules import stable_hash
from test_final_sa_engine import small_model, config


def test_plan(seeds=(71, 72), configurations=('C1', 'C2')):
    matrix = {c['id']: c for c in engine.configurations(4)}
    return [{'run_id': run_id(cid, s), 'seed': s, 'config': {**matrix[cid], 'budget': 330}}
            for s in seeds for cid in configurations]


class MainExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'cohort'
        self.model = small_model()
        self.plan = test_plan()

    def runner(self, plan=None, provenance=None):
        return CohortRunner(self.model, plan or self.plan, self.root,
                            provenance or {'purpose': 'SYNTHETIC_UNIT_TEST_NOT_MAIN'})

    def test_exact_180_plan_same_30_seeds_and_n_3n(self):
        protocol = read_json(DEFAULT_CONFIG); plan = planned_runs(protocol, 513)
        self.assertEqual(len(plan), 180)
        self.assertEqual(len({r['run_id'] for r in plan}), 180)
        for cid in [f'C{i}' for i in range(1, 7)]:
            rows = [r for r in plan if r['config']['id'] == cid]
            self.assertEqual([r['seed'] for r in rows], protocol['main_seeds'])
            self.assertEqual({r['config']['L'] for r in rows}, {513 if cid in ['C1', 'C2', 'C3'] else 1539})
        self.assertFalse(set(protocol['main_seeds']) & set(protocol['pilot_seeds']))

    def test_current_frozen_snapshot_stays_intact_and_blocks_main(self):
        _, frozen, plan, blockers = load_frozen(DEFAULT_FROZEN)
        self.assertFalse(frozen['ready_for_main_experiment'])
        self.assertTrue(blockers); self.assertEqual(len(plan), 180)
        with self.assertRaises(MainBlocked): load_frozen(DEFAULT_FROZEN, require_ready=True)
        self.assertFalse(self.root.exists())

    def test_instrumentation_preserves_all_search_decisions(self):
        cfg = config(budget=330)
        reference = engine.run(self.model, cfg, 71)
        seen = []
        with instrumented_engine(lambda value, trace: seen.append(copy.deepcopy(value))) as timings:
            observed = engine.run(self.model, cfg, 71)
        reference.pop('runtime_seconds'); observed.pop('runtime_seconds')
        self.assertEqual(reference, observed)
        self.assertEqual(seen[0][0], observed['initial'])
        self.assertEqual(set(timings), {'runtime_construction', 'runtime_calibration', 'runtime_search'})
        self.assertTrue(all(t >= 0 for t in timings.values()))

    def test_resume_skips_completed_valid_runs_and_completes_remaining(self):
        r = self.runner(); first = r.execute(max_runs=1)
        self.assertEqual(first['completed_runs'], 1)
        with patch.object(engine, 'run', wraps=engine.run) as calls:
            second = self.runner().execute()
            self.assertEqual(calls.call_count, 3)
        self.assertEqual(second['completed_runs'], 4); self.assertEqual(second['status'], 'FINAL')
        with patch.object(engine, 'run', side_effect=AssertionError('reran completed run')):
            third = self.runner().execute()
        self.assertEqual(third['attempted_this_invocation'], 0)

    def test_same_seed_shared_initial_before_each_configuration(self):
        self.runner().execute()
        for seed in (71, 72):
            summaries = [read_json(self.root/'runs'/run_id(cid, seed)/'attempt-0001/summary.json') for cid in ('C1', 'C2')]
            self.assertEqual(summaries[0]['initial_hash'], summaries[1]['initial_hash'])
            self.assertEqual(summaries[0]['initial_S'], summaries[1]['initial_S'])
            self.assertTrue((self.root/'initials'/f'seed-{seed:06d}.json').exists())

    def test_changed_initial_is_rejected_before_calibration(self):
        r = self.runner(); value = engine.construct(self.model, __import__('random').Random(71), trace=[])
        r.initial_guard(71, value, [])
        changed = copy.deepcopy(value); changed[0]['a'] = ('d2', 8)
        with self.assertRaises(ValueError): r.initial_guard(71, changed, [])

    def test_one_error_does_not_lose_other_runs_and_explicit_rerun_recovers(self):
        actual = engine.run
        def broken(model, cfg, seed):
            if cfg['id'] == 'C1' and seed == 71: raise RuntimeError('injected failure')
            return actual(model, cfg, seed)
        with patch.object(engine, 'run', side_effect=broken): first = self.runner().execute()
        self.assertEqual((first['completed_runs'], first['failed_runs']), (3, 1))
        with patch.object(engine, 'run', side_effect=AssertionError('automatic failure retry')):
            self.runner().execute()
        second = self.runner().execute(rerun=run_id('C1', 71))
        self.assertEqual((second['completed_runs'], second['failed_runs']), (4, 0))
        self.assertTrue((self.root/'runs'/run_id('C1', 71)/'attempt-0001/error.json').exists())
        self.assertTrue((self.root/'runs'/run_id('C1', 71)/'attempt-0002/COMPLETED.json').exists())

    def test_interrupted_attempt_restarts_without_overwriting_evidence(self):
        with patch.object(engine, 'run', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.runner().execute(max_runs=1)
        pointer = read_json(self.root/'runs'/self.plan[0]['run_id']/'CURRENT.json')
        self.assertEqual(pointer['status'], 'RUNNING')
        result = self.runner().execute(max_runs=1)
        self.assertEqual(result['completed_runs'], 1)
        pointer = read_json(self.root/'runs'/self.plan[0]['run_id']/'CURRENT.json')
        self.assertEqual(pointer['attempt'], 'attempt-0002')

    def test_commit_marker_recovers_crash_before_pointer_publish(self):
        self.runner().execute(max_runs=1)
        path = self.root/'runs'/self.plan[0]['run_id']/'CURRENT.json'
        pointer = read_json(path); pointer['status'] = 'RUNNING'; atomic_json(path, pointer)
        # The completed run is recovered before the next pending run starts.
        with self.assertRaises(KeyboardInterrupt):
            with patch.object(engine, 'run', side_effect=KeyboardInterrupt): self.runner().execute(max_runs=1)
        self.assertEqual(read_json(path)['status'], 'COMPLETED')
        self.assertFalse((path.parent/'attempt-0002').exists())

    def test_explicit_rerun_preserves_completed_attempt_and_only_runs_requested_id(self):
        self.runner().execute()
        target = self.plan[0]['run_id']
        with patch.object(engine, 'run', wraps=engine.run) as calls:
            result = self.runner().execute(rerun=target)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(result['completed_runs'], 4)
        folder = self.root/'runs'/target
        self.assertTrue((folder/'attempt-0001/COMPLETED.json').exists())
        self.assertTrue((folder/'attempt-0002/COMPLETED.json').exists())
        self.assertEqual(read_json(folder/'CURRENT.json')['attempt'], 'attempt-0002')

    def test_corrupt_completed_output_is_invalidated_not_silently_used(self):
        self.runner().execute(max_runs=1)
        path = self.root/'runs'/self.plan[0]['run_id']/'attempt-0001/best-feasible-timetable.json'
        rows = read_json(path); rows[0]['teacher_codes'] = ['tampered']; atomic_json(path, rows)
        result = self.runner().execute(max_runs=1)
        self.assertEqual(result['failed_runs'], 1)
        self.assertTrue((path.parent.parent/'INVALIDATED.json').exists())

    def test_independent_validator_rejects_false_feasible_even_with_matching_metadata(self):
        item = self.plan[0]; result = engine.run(self.model, item['config'], item['seed'])
        rows = self.model.timetable(result['best_feasible']); rows.append(copy.deepcopy(rows[0]))
        with self.assertRaises(ValueError): validate_result(self.model, result, item, rows)

    def test_resume_rejects_changed_config_or_frozen_identity(self):
        self.runner().execute(max_runs=1)
        with self.assertRaises(ValueError): self.runner(provenance={'purpose': 'different version'}).execute()
        changed = copy.deepcopy(self.plan); changed[0]['config']['alpha'] = .8
        with self.assertRaises(ValueError): self.runner(plan=changed).execute()

    def test_required_metrics_and_machine_readable_convergence(self):
        self.runner().execute(max_runs=1)
        folder = self.root/'runs'/self.plan[0]['run_id']/'attempt-0001'
        row = read_json(folder/'summary.json')
        required = ['run_id','config_id','seed','n_events','alpha','L','T0',
                    'initial_H','initial_SC1','initial_SC2','initial_SC3','initial_S','initial_F',
                    'best_H','best_SC1','best_SC2','best_SC3','best_S','best_F',
                    'best_feasible_found','best_feasible_SC1','best_feasible_SC2','best_feasible_SC3','best_feasible_S',
                    'runtime_construction','runtime_calibration','runtime_search','evaluation_count',
                    'final_temperature','stop_reason','valid_candidate_count','no_change_count',
                    'move_attempt_count','move_accepted_count','swap_attempt_count','swap_accepted_count',
                    'dataset_version','rule_version','difficulty_version','code_version']
        self.assertTrue(set(required) <= set(row))
        curve = list(csv.DictReader(io.StringIO((folder/'convergence.csv').read_text())))
        self.assertEqual(float(curve[-1]['best_F']), row['best_F'])
        self.assertEqual(float(curve[0]['best_F']), row['initial_F'])
        self.assertEqual(read_json(folder/'validation.json')['best_feasible']['H'], 0)

    def test_aggregation_feasible_only_and_failure_denominator(self):
        plan = test_plan(seeds=(1,2,3,4), configurations=('C1',))
        rows = {}
        for i, value in enumerate([.1,.3,None], 1):
            rows[run_id('C1', i)] = {'best_feasible_found': value is not None, 'best_feasible_S': value,
                'runtime_total': i, 'evaluation_count': 100*i, 'stop_reason': 'TMIN'}
        a = aggregate(plan, rows, {run_id('C1',4): 'error'})
        r = a['configurations'][0]
        self.assertEqual(r['total_runs'],4); self.assertEqual(r['feasible_runs'],2)
        self.assertEqual(r['feasibility_rate'], .5)
        self.assertAlmostEqual(r['mean_feasible_S'], .2)
        self.assertAlmostEqual(r['standard_deviation_feasible_S'], .1414213562373095)
        self.assertEqual(r['median_runtime'],2); self.assertEqual(r['median_evaluations'],200)
        self.assertEqual(a['status'], 'PROVISIONAL')
        b = aggregate(plan, {}, {})['configurations'][0]
        self.assertIsNone(b['mean_feasible_S']); self.assertIsNone(b['standard_deviation_feasible_S'])

    def test_exclusive_lock_released_after_exception(self):
        with exclusive_lock(self.root):
            with self.assertRaises(ValueError):
                with exclusive_lock(self.root): pass
        with exclusive_lock(self.root): pass


if __name__ == '__main__': unittest.main()
