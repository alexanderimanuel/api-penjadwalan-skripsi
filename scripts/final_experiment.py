"""Sequential main-experiment runner with immutable frozen-input gates.

Resume is at run boundaries: incomplete attempts restart, committed valid runs skip.
No frozen evaluator, engine, parameter or dataset is modified by this module.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
import copy
import csv
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time
import traceback

import final_sa as engine
from export_august import read_json
from final_foundation import thaw
from final_pilot import validate_protocol
from final_rules import FinalModel, stable_hash
from final_validator import validate
from model_august import ROOT

RUNNER_VERSION = 'final-experiment-v1'
DEFAULT_FROZEN = ROOT/'results/final-pilot/technical-v1/FROZEN_EXPERIMENT_CONFIG.json'
ENGINE_MODULES = {'final-sa-v2': 'final_sa', 'final-sa-ts-v1': 'final_sa_ts', 'final-gst-v1': 'final_gst'}


def resolve_engine(engine_name=None, output=None):
    """Explicit name wins; otherwise continue the engine recorded in a stored
    manifest so select/export can never silently switch engines mid-cohort."""
    if engine_name is None and output is not None:
        stored = Path(output)/'manifest.json'
        if stored.exists():
            engine_name = ENGINE_MODULES.get(read_json(stored).get('engine_version'))
            if engine_name is None:
                raise ValueError('Stored cohort engine is unknown')
    if engine_name is None:
        engine_name = 'final_sa'
    if engine_name not in ENGINE_MODULES.values():
        raise ValueError('Unknown engine module: '+str(engine_name))
    if output is not None:
        stored = Path(output)/'manifest.json'
        if stored.exists():
            recorded = read_json(stored).get('engine_version')
            if recorded != importlib.import_module(engine_name).ENGINE_VERSION:
                raise ValueError('Engine differs from stored cohort manifest')
    return importlib.import_module(engine_name)


class MainBlocked(ValueError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n', encoding='utf-8')
    os.replace(temp, path)


def atomic_text(path, text):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp'); temp.write_text(text, encoding='utf-8')
    os.replace(temp, path)


def contained(folder, relative):
    path = (Path(folder)/relative).resolve()
    path.relative_to(Path(folder).resolve())
    return path


@contextmanager
def exclusive_lock(folder):
    """OS releases the lock on process exit; no stale PID removal required."""
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    with (folder/'.runner.lock').open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError('Another runner holds this cohort lock') from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt': msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_id(config_id, seed):
    return f'{config_id}-seed-{seed:06d}'


def planned_runs(protocol, n):
    validate_protocol(protocol)
    return [{'run_id': run_id(c['id'], seed), 'seed': seed, 'config': {
        'id': c['id'], 'alpha': c['alpha'], 'L': c['L_multiplier']*n,
        'budget': protocol['max_evaluations'], 'Tmin': protocol['Tmin'],
        'stagnation': protocol['stagnation_limit'], 'calibration_samples': protocol['calibration_samples'],
        'p0': protocol['p0'], 'max_attempts': protocol['max_candidate_attempts'],
        'swap_probability': protocol['operator_probabilities']['swap']}}
        for seed in protocol['main_seeds'] for c in protocol['configurations']]


def load_frozen(path, require_ready=False):
    path = Path(path).resolve(); frozen = read_json(path)
    payload = {k: v for k, v in frozen.items() if k != 'snapshot_hash'}
    if stable_hash(payload) != frozen['snapshot_hash']: raise ValueError('Frozen snapshot hash mismatch')
    validate_protocol(frozen['protocol'])
    if stable_hash(frozen['protocol']) != frozen['protocol_hash']: raise ValueError('Frozen protocol hash mismatch')
    for name, expected in frozen['code_hashes'].items():
        if digest(contained(ROOT/'scripts', name)) != expected:
            raise ValueError('Frozen code changed; cannot mix results: '+name)
    for name, expected in frozen['evidence_hashes'].items():
        if digest(contained(path.parent, name)) != expected:
            raise ValueError('Frozen evidence changed: '+name)
    data = read_json(path.parent/'dataset-snapshot.json'); rules = read_json(path.parent/'rules-snapshot.json')
    model = FinalModel(data, rules)
    context = frozen['input_context']
    if model.dataset_hash != context['selected_dataset_hash'] or model.rules_hash != context['selected_rules_hash']:
        raise ValueError('Dataset/rules do not match frozen inputs')
    blockers = list(frozen.get('limitations', []))
    if frozen.get('ready_for_main_experiment') is not True: blockers.append('Frozen configuration is not ready for main experiment')
    if frozen.get('freeze_scope') != 'SCHOOL_DATASET' or context.get('dataset_kind') != 'SCHOOL_PILOT' or data.get('status') == 'synthetic':
        blockers.append('Synthetic/technical-only freeze cannot authorize main experiment')
    if rules.get('dataset_finalized') is not True: blockers.append('School dataset is not finalized')
    if rules.get('assignment_semantics_decision', {}).get('status') != 'SCHOOL_CONFIRMED':
        blockers.append('School group semantics are not confirmed')
    if context.get('blockers'): blockers.extend(context['blockers'])
    blockers = list(dict.fromkeys(blockers))
    if require_ready and blockers: raise MainBlocked('; '.join(blockers))
    plan = planned_runs(frozen['protocol'], len(model.ids))
    return model, frozen, plan, blockers


def same_state(a, b):
    return a is None and b is None or a is not None and b is not None and {k: tuple(v) for k, v in a.items()} == {k: tuple(v) for k, v in b.items()}


@contextmanager
def instrumented_engine(initial_guard, target=None):
    """Serial-only observation of phase boundaries; no RNG/score modifications.

    Search starts at calibration return and ends at entry to first final validation.
    It includes search setup and loop bookkeeping, not final validation.
    """
    originals = {k: getattr(target if target is not None else engine, k) for k in ('construct', 'calibrate', 'validate')}
    timings = {}; boundary = {'search_start': None}
    def construct(*args, **kwargs):
        start = time.perf_counter(); value = originals['construct'](*args, **kwargs)
        timings['runtime_construction'] = time.perf_counter()-start
        initial_guard(value, kwargs.get('trace', []))  # Before initial validation/calibration/search.
        return value
    def calibrate(*args, **kwargs):
        start = time.perf_counter(); value = originals['calibrate'](*args, **kwargs)
        timings['runtime_calibration'] = time.perf_counter()-start
        boundary['search_start'] = time.perf_counter()
        return value
    def validation(*args, **kwargs):
        if boundary['search_start'] is not None and 'runtime_search' not in timings:
            timings['runtime_search'] = time.perf_counter()-boundary['search_start']
        return originals['validate'](*args, **kwargs)
    target = target if target is not None else engine
    target.construct, target.calibrate, target.validate = construct, calibrate, validation
    try:
        yield timings
    finally:
        for key, value in originals.items(): setattr(target, key, value)


def validate_result(model, result, item, exported):
    if result['seed'] != item['seed'] or result['configuration'] != item['config']:
        raise ValueError('Run seed/config mismatch')
    if result['dataset_hash'] != model.dataset_hash or result['rules_hash'] != model.rules_hash:
        raise ValueError('Run dataset/rules mismatch')
    if stable_hash(result['initial']) != result['initial_hash']: raise ValueError('Initial hash mismatch')
    checks = {}
    for state, quality in [('initial', 'initial_quality'), ('current', 'current_quality'), ('best', 'best_quality')]:
        checked = model.evaluate(result[state])
        if checked['validation_status'] != 'VALID_TIMETABLE' or not engine.score_equal(checked, result[quality]):
            raise ValueError('Independent validation mismatch: '+state)
        checks[state] = checked
    if result['best_feasible'] is not None:
        if exported is None: raise ValueError('Missing best feasible timetable')
        check = validate(model, exported)  # Direct independent HC1-HC7 check on saved timetable.
        expected = model.evaluate(result['best_feasible'])
        if not check['feasible'] or not expected['feasible'] or not engine.score_equal(check, expected) or not engine.score_equal(check, result['best_feasible_quality']):
            raise ValueError('Best feasible timetable failed independent validation')
        if thaw(exported) != model.timetable(result['best_feasible']): raise ValueError('Export placements differ from best feasible')
        if result['status'] != 'FEASIBLE': raise ValueError('Feasibility status mismatch')
        checks['best_feasible'] = check
    else:
        if exported is not None or result['best_feasible_quality'] is not None or result['status'] != 'NO_FEASIBLE_FOUND' or checks['best']['feasible']:
            raise ValueError('Missing/false best feasible tracking')
        checks['best_feasible'] = None
    return checks


def metrics(model, result, timings, item, code_version):
    row = {'run_id': item['run_id'], 'config_id': item['config']['id'], 'seed': item['seed'],
           'n_events': len(model.ids), 'alpha': result['alpha'], 'L': result['L'], 'T0': result['T0'],
           'initial_hash': result['initial_hash'], **timings,
           'runtime_total': result['runtime_seconds'], 'evaluation_count': result['evaluations']['total'],
           'final_temperature': result['final_temperature'], 'stop_reason': result['stop_reason'],
           'valid_candidate_count': sum(result['candidate_statistics'].get(k+'_evaluations', 0) for k in ('move', 'swap')),
           'no_change_count': result['candidate_statistics'].get('no_change_evaluations', 0),
           'best_feasible_found': result['best_feasible'] is not None,
           'dataset_version': result['dataset_version'], 'rule_version': result['rules_version'],
           'difficulty_version': result['difficulty_category_version'], 'code_version': code_version,
           'engine_version': result['engine_version'], 'status': 'COMPLETED'}
    for prefix, key in [('initial', 'initial_quality'), ('best', 'best_quality')]:
        score = result[key]
        row.update({prefix+'_'+k: score[k] for k in ('H', 'S', 'F')})
        row.update({prefix+'_'+k: score['raw'][k] for k in ('SC1', 'SC2', 'SC3')})
    best = result['best_feasible_quality']
    row.update({'best_feasible_'+k: best['raw'][k] if best else None for k in ('SC1', 'SC2', 'SC3')})
    row['best_feasible_S'] = best['S'] if best else None
    logs = [dict(zip(result['search_log']['columns'], r)) for r in result['search_log']['rows']]
    for op in ('move', 'swap'):
        row[op+'_attempt_count'] = sum(r['attempted_operators'].get(op, 0) for r in logs)
        row[op+'_candidate_count'] = sum(r['operator'] == op for r in logs)
        row[op+'_accepted_count'] = sum(r['operator'] == op and r['accepted'] for r in logs)
        row[op+'_accepted_worse_count'] = sum(r['operator'] == op and r['accepted'] and r['delta'] > 0 for r in logs)
    return row


def convergence(result):
    first = result['initial_quality']
    rows = [{'evaluation_index': result['evaluations']['construction_candidates']+2,
             'search_evaluation': 0, 'temperature': result['T0'], 'best_H': first['H'],
             'best_S': first['S'], 'best_F': first['F'], 'best_feasible_S': first['S'] if first['feasible'] else None}]
    for values in result['search_log']['rows']:
        row = dict(zip(result['search_log']['columns'], values))
        rows.append({key: row[key] for key in rows[0]})
    return rows


def csv_text(rows):
    if not rows: return ''
    out = io.StringIO(newline=''); writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
    return out.getvalue()


def aggregate(plan, records, failures):
    summaries = []
    for config_id in dict.fromkeys(item['config']['id'] for item in plan):
        ids = {r['run_id'] for r in plan if r['config']['id'] == config_id}
        completed = [records[rid] for rid in ids if rid in records]
        feasible = [r['best_feasible_S'] for r in completed if r['best_feasible_found']]
        runtimes = [r['runtime_total'] for r in completed]; evaluations = [r['evaluation_count'] for r in completed]
        failed = sum(rid in failures for rid in ids)
        summaries.append({'config_id': config_id, 'total_runs': len(ids), 'completed_runs': len(completed),
            'failed_runs': failed, 'pending_runs': len(ids)-len(completed)-failed,
            'feasible_runs': len(feasible), 'feasibility_rate': len(feasible)/len(ids),
            'median_feasible_S': statistics.median(feasible) if feasible else None,
            'mean_feasible_S': statistics.mean(feasible) if feasible else None,
            'standard_deviation_feasible_S': statistics.stdev(feasible) if len(feasible) > 1 else None,
            'min_feasible_S': min(feasible) if feasible else None, 'max_feasible_S': max(feasible) if feasible else None,
            'median_runtime': statistics.median(runtimes) if runtimes else None,
            'mean_runtime': statistics.mean(runtimes) if runtimes else None,
            'median_evaluations': statistics.median(evaluations) if evaluations else None,
            'stop_reason_distribution': dict(sorted(Counter(r['stop_reason'] for r in completed).items()))})
    return {'status': 'FINAL' if len(records) == len(plan) else 'PROVISIONAL',
            'expected_runs': len(plan), 'completed_runs': len(records), 'failed_runs': len(failures),
            'analysis': 'Descriptive only. Feasibility denominator includes all planned seeds; S includes feasible runs only. Runtime/evaluations include valid completed runs only; SD is sample SD and null for <2 feasible runs.',
            'configurations': summaries}


class CohortRunner:
    """Low-level runner; production CLI must pass the frozen readiness gate first."""
    def __init__(self, model, plan, output, provenance, engine_name=None):
        self.model, self.plan, self.output = model, copy.deepcopy(plan), Path(output)
        self.engine = resolve_engine(engine_name, self.output)
        self.items = {p['run_id']: p for p in plan}
        if len(self.items) != len(plan): raise ValueError('Duplicate run IDs')
        for item in plan:
            if item['run_id'] != run_id(item['config']['id'], item['seed']): raise ValueError('Noncanonical run ID')
        self.code_version = RUNNER_VERSION+'-'+digest(__file__)[:12]
        self.manifest = {'runner_version': self.code_version, 'engine_version': self.engine.ENGINE_VERSION,
            'dataset_hash': model.dataset_hash, 'rules_hash': model.rules_hash, 'plan': self.plan,
            'provenance': provenance, 'environment': {'python': sys.version, 'platform': platform.platform()}}
        self.manifest['identity_hash'] = stable_hash(self.manifest)

    def initial_guard(self, seed, value, trace, require_existing=False):
        x, evaluations, order = value
        payload = {'seed': seed, 'dataset_hash': self.model.dataset_hash, 'rules_hash': self.model.rules_hash,
                   'initial': x, 'initial_hash': stable_hash(x), 'construction_evaluations': evaluations,
                   'order': order, 'trace': trace}
        path = self.output/'initials'/f'seed-{seed:06d}.json'
        if path.exists():
            saved = read_json(path)
            if saved['content_hash'] != stable_hash({k: v for k, v in saved.items() if k != 'content_hash'}): raise ValueError('Shared initial artifact corrupted')
            if saved['content_hash'] != stable_hash(payload): raise ValueError('Initial differs across configurations for seed '+str(seed))
        else:
            if require_existing: raise ValueError('Shared initial artifact missing')
            atomic_json(path, {**payload, 'content_hash': stable_hash(payload)})

    def validate_completed(self, item, pointer):
        folder = contained(self.output/'runs'/item['run_id'], pointer['attempt'])
        marker = read_json(folder/'COMPLETED.json')
        if marker['manifest_hash'] != self.manifest['identity_hash'] or marker['run_id'] != item['run_id']:
            raise ValueError('Completion identity mismatch')
        for name, expected in marker['file_hashes'].items():
            if digest(contained(folder, name)) != expected: raise ValueError('Artifact hash mismatch: '+name)
        result = read_json(folder/'result.json'); summary = read_json(folder/'summary.json')
        rows = read_json(folder/'best-feasible-timetable.json')
        validate_result(self.model, result, item, rows)
        self.initial_guard(item['seed'], (result['initial'], result['evaluations']['construction_candidates'], result['initial_order']), result['construction_trace'], require_existing=True)
        timings = {k: summary[k] for k in ('runtime_construction', 'runtime_calibration', 'runtime_search')}
        if any(type(v) not in (int, float) or not 0 <= v < float('inf') for v in timings.values()): raise ValueError('Invalid phase runtime')
        if summary != metrics(self.model, result, timings, item, self.code_version): raise ValueError('Summary mismatch')
        if read_json(folder/'config-snapshot.json') != item['config']: raise ValueError('Config snapshot mismatch')
        return summary

    def refresh_summary(self, records, failures):
        summary = aggregate(self.plan, records, failures)
        atomic_json(self.output/'aggregate.json', summary)
        ordered = [records[p['run_id']] for p in self.plan if p['run_id'] in records]
        atomic_text(self.output/'runs.csv', csv_text(ordered))
        atomic_json(self.output/'progress.json', {'expected': len(self.plan), 'completed': list(records), 'failed': failures,
            'pending': [p['run_id'] for p in self.plan if p['run_id'] not in records and p['run_id'] not in failures]})
        return summary

    def execute(self, rerun=None, max_runs=None):
        if rerun is not None and rerun not in self.items: raise ValueError('Unknown run ID')
        if max_runs is not None and (type(max_runs) is not int or max_runs < 1): raise ValueError('max-runs must be positive')
        with exclusive_lock(self.output):
            manifest_path = self.output/'manifest.json'
            if manifest_path.exists():
                if read_json(manifest_path) != self.manifest: raise ValueError('Cohort input/code/environment changed; use a new version, do not mix results')
                if stable_hash(read_json(self.output/'dataset-snapshot.json')) != self.model.dataset_hash or stable_hash(read_json(self.output/'rules-snapshot.json')) != self.model.rules_hash:
                    raise ValueError('Cohort input snapshot changed')
                if read_json(self.output/'frozen-config-snapshot.json') != self.manifest['provenance']:
                    raise ValueError('Cohort frozen snapshot changed')
            else:
                atomic_json(self.output/'dataset-snapshot.json', thaw(self.model.data))
                atomic_json(self.output/'rules-snapshot.json', thaw(self.model.rules))
                atomic_json(self.output/'frozen-config-snapshot.json', self.manifest['provenance'])
                # Publish the bootstrap commit last so interruption is restartable.
                atomic_json(manifest_path, self.manifest)
            records = {}; failures = {}; pointers = {}
            for item in self.plan:
                rid = item['run_id']; p = self.output/'runs'/rid/'CURRENT.json'
                if not p.exists(): continue
                try:
                    pointer = read_json(p); pointers[rid] = pointer
                    if pointer['status'] == 'COMPLETED' or (pointer['status'] == 'RUNNING' and (contained(p.parent, pointer['attempt'])/'COMPLETED.json').exists()):
                        records[rid] = self.validate_completed(item, pointer)
                        if pointer['status'] == 'RUNNING':
                            pointer['status'] = 'COMPLETED'
                            atomic_json(p, pointer)
                    elif pointer['status'] == 'ERROR': failures[rid] = pointer.get('error', 'Execution error')
                except (OSError, ValueError, KeyError, TypeError) as error:
                    failures[rid] = 'INVALIDATED: '+str(error)
                    atomic_json(p.parent/'INVALIDATED.json', {'reason': str(error), 'action': 'Preserved old artifacts; explicit rerun required', 'run_id': rid})
            self.refresh_summary(records, failures)
            attempted = skipped = 0
            for item in self.plan:
                rid = item['run_id']
                if rerun is not None and rid != rerun: continue
                if rerun is None and (rid in records or rid in failures): skipped += 1; continue
                if max_runs is not None and attempted >= max_runs: break
                attempted += 1; records.pop(rid, None); failures.pop(rid, None)
                run_folder = self.output/'runs'/rid; run_folder.mkdir(parents=True, exist_ok=True)
                numbers = [int(p.name.split('-')[1]) for p in run_folder.glob('attempt-*') if p.is_dir() and p.name.split('-')[1].isdigit()]
                attempt = f'attempt-{max(numbers, default=0)+1:04d}'
                folder = run_folder/attempt; folder.mkdir()
                pointer = {'run_id': rid, 'attempt': attempt, 'status': 'RUNNING'}
                atomic_json(run_folder/'CURRENT.json', pointer)
                self.refresh_summary(records, failures)
                atomic_json(folder/'config-snapshot.json', item['config'])
                atomic_json(folder/'status.json', {**pointer, 'started_at': datetime.now(timezone.utc).isoformat()})
                print(rid+': running '+attempt, flush=True)
                try:
                    guard = lambda value, trace: self.initial_guard(item['seed'], value, trace)
                    with instrumented_engine(guard, self.engine) as timings:
                        result = self.engine.run(self.model, item['config'], item['seed'])
                    rows = self.model.timetable(result['best_feasible']) if result['best_feasible'] is not None else None
                    checks = validate_result(self.model, result, item, rows)
                    summary = metrics(self.model, result, timings, item, self.code_version)
                    atomic_json(folder/'result.json', result)
                    atomic_json(folder/'summary.json', summary)
                    atomic_json(folder/'best-feasible-timetable.json', rows)
                    atomic_json(folder/'validation.json', checks)
                    atomic_text(folder/'convergence.csv', csv_text(convergence(result)))
                    hashes = {p.name: digest(p) for p in folder.iterdir() if p.name not in ('status.json',) and p.is_file()}
                    atomic_json(folder/'COMPLETED.json', {'run_id': rid, 'manifest_hash': self.manifest['identity_hash'], 'file_hashes': hashes})
                    pointer['status'] = 'COMPLETED'
                    # Re-read persisted output and validate before publishing completion.
                    records[rid] = self.validate_completed(item, pointer)
                    atomic_json(folder/'status.json', pointer)
                    atomic_json(run_folder/'CURRENT.json', pointer)
                    print(rid+': '+result['status'], flush=True)
                except Exception as error:
                    failures[rid] = str(error)
                    pointer.update(status='ERROR', error=str(error))
                    atomic_json(folder/'error.json', {'run_id': rid, 'error': str(error), 'traceback': traceback.format_exc()})
                    atomic_json(folder/'status.json', pointer); atomic_json(run_folder/'CURRENT.json', pointer)
                    print(rid+': ERROR '+str(error), flush=True)
                self.refresh_summary(records, failures)
            summary = self.refresh_summary(records, failures)
            return {**summary, 'attempted_this_invocation': attempted, 'skipped_this_invocation': skipped}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'run', 'rerun'])
    parser.add_argument('--frozen', default=str(DEFAULT_FROZEN))
    parser.add_argument('--output'); parser.add_argument('--run-id'); parser.add_argument('--max-runs', type=int)
    args = parser.parse_args(argv)
    try:
        model, frozen, plan, blockers = load_frozen(args.frozen, require_ready=args.command != 'plan')
        if args.command == 'plan':
            result = {'status': 'BLOCKED BY REAL DATA' if blockers else 'READY', 'blockers': blockers,
                      'expected_runs': len(plan), 'frozen_snapshot_hash': frozen['snapshot_hash'],
                      'n_events': len(model.ids), 'scope': frozen['freeze_scope'], 'plan': plan, 'runs_executed': 0}
            if args.output: atomic_json(args.output, result)
        else:
            if not args.output: parser.error('run/rerun requires --output cohort directory')
            if args.command == 'rerun' and not args.run_id: parser.error('rerun requires --run-id')
            if args.command == 'run' and args.run_id: parser.error('--run-id is only for rerun')
            result = CohortRunner(model, plan, args.output, frozen).execute(
                rerun=args.run_id if args.command == 'rerun' else None, max_runs=args.max_runs)
        printable = {k: v for k, v in result.items() if k != 'plan'}
        print(json.dumps(printable, ensure_ascii=False, indent=2))
        return 1 if result.get('failed_runs', 0) else 0
    except MainBlocked as error:
        print(json.dumps({'status': 'BLOCKED BY REAL DATA', 'reason': str(error), 'runs_executed': 0}, ensure_ascii=False, indent=2))
        return 2


if __name__ == '__main__': sys.exit(main())
