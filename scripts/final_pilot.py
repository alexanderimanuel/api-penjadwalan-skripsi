"""Five-run preliminary verification, explicit data gates and frozen snapshots.

This module has no entry point for the 180-run main experiment.
"""
import argparse
import copy
import hashlib
import json
import math
import platform
import sys
import time
from pathlib import Path

from export_august import read_json
from final_rules import FinalModel, readiness, stable_hash, combine
from final_sa import run, score_equal
from final_validator import validate
from final_foundation import thaw
from model_august import ROOT

DEFAULT_CONFIG = ROOT/'config/pilot-experiment.v1.json'
CODE_FILES = ['final_pilot.py', 'final_sa.py', 'final_rules.py', 'final_foundation.py', 'final_validator.py', 'final_cli.py']


class PilotAnomaly(ValueError):
    pass


def write_json(path, value, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                               indent=None if compact else 2)+'\n', encoding='utf-8')
    temp.replace(path)


def validate_protocol(config):
    for key, count in [('pilot_seeds', 5), ('main_seeds', 30)]:
        seeds = config[key]
        if len(seeds) != count or any(type(s) is not int for s in seeds) or len(set(seeds)) != count:
            raise ValueError(f'{key}: require {count} unique integer seeds')
    if set(config['pilot_seeds']) & set(config['main_seeds']):
        raise ValueError('Pilot/main seed overlap')
    expected = {'max_evaluations': 100000, 'p0': .8, 'calibration_samples': 200,
                'Tmin': .001, 'stagnation_limit': 20000, 'max_candidate_attempts': 50}
    if any(config[k] != value for k, value in expected.items()):
        raise ValueError('Protocol differs from current approved search parameters; version changes explicitly')
    if config['operator_probabilities'] != {'move': .5, 'swap': .5}:
        raise ValueError('Current engine requires move/swap 0.5/0.5')
    declared = [(c['id'], c['alpha'], c['L_multiplier']) for c in config['configurations']]
    intended = [(f'C{i+1}', alpha, mult) for i, (mult, alpha) in enumerate((m, a) for m in [1, 3] for a in [.9, .95, .98])]
    if declared != intended or config['pilot_configuration'] not in [c[0] for c in declared]:
        raise ValueError('Invalid configuration matrix or pilot configuration')
    weights = config['soft_weights']
    if set(weights) != {'SC1', 'SC2', 'SC3'} or any(type(w) not in (int, float) or not math.isfinite(w) or w < 0 for w in weights.values()) or not math.isclose(sum(weights.values()), 1):
        raise ValueError('Invalid soft weights')
    if type(config['high_subject_daily_limit']) is not int or config['high_subject_daily_limit'] < 0 or not config['difficulty_category_source']:
        raise ValueError('Daily limit/source missing')
    audit = config['audit']
    if type(audit['independent_check_every']) is not int or audit['independent_check_every'] < 1:
        raise ValueError('Invalid independent audit interval')
    if not math.isfinite(audit['runtime_warning_seconds']) or audit['runtime_warning_seconds'] <= 0:
        raise ValueError('Invalid runtime review threshold')


def select_inputs(config):
    """Synthetic fallback is evidence for software only, never school readiness."""
    validate_protocol(config)
    blockers = []
    school_data = school_rules = None
    try:
        manifest_path = ROOT/config['school_manifest_path']
        manifest = read_json(manifest_path)
        for record in manifest['files']:
            p = manifest_path.parent/record['path']
            if hashlib.sha256(p.read_bytes()).hexdigest() != record['sha256']:
                raise ValueError('School snapshot hash mismatch: '+record['path'])
        school_data = read_json(manifest_path.parent/manifest['dataset_path'])
        school_rules = read_json(ROOT/config['school_rules_path'])
        blockers.extend(readiness(school_data, school_rules))
        if not school_rules.get('dataset_finalized'):
            blockers.append('Dataset sekolah belum difinalisasi; konfirmasi kelompok AGM dan bentrok sumber masih diperlukan.')
        decision = school_rules.get('assignment_semantics_decision', {})
        if not isinstance(decision, dict) or decision.get('status') != 'SCHOOL_CONFIRMED':
            blockers.append('Makna kelompok/pengajaran bersama belum berstatus SCHOOL_CONFIRMED.')
        for field, value in [('weights', config['soft_weights'])]:
            if school_rules.get(field) != value:
                blockers.append('Konfigurasi pilot tidak cocok dengan '+field+' sekolah.')
        if school_rules.get('difficulty', {}).get('daily_limit') != config['high_subject_daily_limit']:
            blockers.append('Daily limit pilot tidak cocok dengan config sekolah.')
        if school_rules.get('difficulty', {}).get('source') != config['difficulty_category_source']:
            blockers.append('Sumber difficulty config pilot tidak cocok dengan versi sekolah.')
    except (OSError, KeyError, ValueError, TypeError) as error:
        blockers.append('School input unavailable/invalid: '+str(error))
    blockers.extend(p['reason'] for p in config.get('main_prerequisites', []) if p['status'] != 'RESOLVED')
    if blockers:
        data = read_json(ROOT/config['technical_fallback']['dataset_path'])
        rules = copy.deepcopy(config['technical_fallback']['rules'])
        if data.get('status') != 'synthetic':
            raise ValueError('Fallback must be explicitly synthetic')
        kind = 'SYNTHETIC_TECHNICAL_VERIFICATION'
    else:
        data, rules, kind = school_data, school_rules, 'SCHOOL_PILOT'
    if rules['weights'] != config['soft_weights'] or rules['difficulty']['daily_limit'] != config['high_subject_daily_limit']:
        raise ValueError('Selected rule weights/daily limit diverge from experiment config')
    model = FinalModel(data, rules)
    context = {'dataset_kind': kind, 'main_experiment_status': 'BLOCKED BY REAL DATA' if blockers else 'AWAITING_PILOT_VALIDATION',
               'blockers': blockers, 'selected_dataset_hash': model.dataset_hash, 'selected_rules_hash': model.rules_hash,
               'school_dataset_hash': stable_hash(school_data) if school_data else None,
               'school_rules_hash': stable_hash(school_rules) if school_rules else None,
               'school_difficulty_source': config['difficulty_category_source'],
               'selected_difficulty_source': rules['difficulty']['source']}
    return model, context


def engine_config(config, n):
    c = next(c for c in config['configurations'] if c['id'] == config['pilot_configuration'])
    return {'id': c['id'], 'alpha': c['alpha'], 'L': c['L_multiplier']*n,
            'budget': config['max_evaluations'], 'Tmin': config['Tmin'],
            'stagnation': config['stagnation_limit'], 'p0': config['p0'],
            'calibration_samples': config['calibration_samples'], 'max_attempts': config['max_candidate_attempts'],
            'swap_probability': config['operator_probabilities']['swap']}


def quality_problems(score, rules):
    problems = []
    try:
        for value in score['HC'].values():
            if type(value) is not int or value < 0:
                problems.append('negative/noninteger hard penalty')
        for key in ['SC1', 'SC2', 'SC3']:
            raw, normal = score['raw'][key], score['normalized'][key]
            if type(raw) not in (int, float) or not math.isfinite(raw) or raw < 0:
                problems.append('negative/nonfinite raw '+key)
            if type(normal) not in (int, float) or not math.isfinite(normal) or not 0 <= normal <= 1:
                problems.append('normalized outside 0..1 '+key)
        for key in ('H', 'S', 'F'):
            if type(score[key]) not in (int, float) or not math.isfinite(score[key]) or score[key] < 0:
                problems.append('negative/nonfinite '+key)
        expected = combine(score['HC'], score['raw'], rules)
        if not score_equal(score, expected): problems.append('score/formula/feasibility mismatch')
        for alias in ('hard', 'soft_raw', 'soft_normalized'):
            if score.get(alias) != expected[alias]: problems.append('breakdown alias mismatch '+alias)
    except (TypeError, KeyError, ValueError):
        problems.append('malformed or out-of-bound score')
    return problems


class AuditedModel:
    """Check every scored candidate, including rejected/calibration candidates."""
    def __init__(self, model, stride):
        self.model, self.stride = model, stride
        self.calls = self.complete_calls = self.independent_checks = 0
        self.audit_seconds = 0.0

    def __getattr__(self, name):
        return getattr(self.model, name)

    def score(self, x, partial=False):
        score = self.model.score(x, partial=partial)
        started = time.perf_counter()
        self.calls += 1
        problems = quality_problems(score, self.model.rules)
        if not partial:
            self.complete_calls += 1
            if self.complete_calls == 1 or self.complete_calls % self.stride == 0:
                checked = self.model.evaluate(x)
                self.independent_checks += 1
                if not score_equal(checked, score): problems.append('independent candidate validation mismatch')
                if stable_hash(self.model.data) != self.model.dataset_hash: problems.append('canonical attribute mutation')
        self.audit_seconds += time.perf_counter()-started
        if problems: raise PilotAnomaly('; '.join(problems))
        return score


def audit_run(model, result, stride=250, exported_rows=None):
    """Re-evaluate finals and replay accepted placements; collect actionable anomalies."""
    issues = []
    checked_steps = independent_checks = 0
    def require(condition, message):
        if not condition: raise PilotAnomaly(message)
    def walk(value, path='run'):
        if isinstance(value, float) and not math.isfinite(value): issues.append(path+': NaN/Infinity')
        elif isinstance(value, dict):
            for k, v in value.items(): walk(v, path+'.'+k)
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value): walk(v, f'{path}[{i}]')
    walk(result)
    try:
        require(result['dataset_hash'] == model.dataset_hash and stable_hash(model.data) == model.dataset_hash, 'dataset hash / immutable attribute mismatch')
        require(result['rules_hash'] == model.rules_hash, 'rules hash mismatch')
        for state, score_key in [('initial', 'initial_quality'), ('best', 'best_quality'), ('current', 'current_quality'), ('best_feasible', 'best_feasible_quality')]:
            x, q = result[state], result[score_key]
            if x is None:
                require(state == 'best_feasible' and q is None, 'missing solution/quality '+state)
                continue
            checked = model.evaluate(x); independent_checks += 1
            require(checked['validation_status'] == 'VALID_TIMETABLE', 'missing/duplicate event, invalid placement or immutable violation: '+state)
            require(not quality_problems(q, model.rules), 'invalid quality: '+state)
            require(score_equal(q, checked), 'recomputed quality mismatch: '+state)
            if state == 'best_feasible': require(checked['feasible'], 'best feasible is not feasible')
        if exported_rows is not None:
            check = validate(model, exported_rows); independent_checks += 1
            require(check['feasible'], 'export: missing/duplicate, attribute mutation, invalid placement or infeasible')
            require(score_equal(check, result['best_feasible_quality']), 'export quality mismatch')
        require((result['status'] == 'FEASIBLE') == (result['best_feasible'] is not None), 'best feasible status mismatch')
        cal = result['calibration']
        require(len(cal['sample_log']['rows']) == 200 and cal['evaluations'] == 200, 'wrong calibration sample count')
        positives = [r[2] for r in cal['sample_log']['rows'] if r[2] > 0]
        expected_t = -sum(positives)/len(positives)/math.log(.8) if positives else 1.0
        require(math.isclose(expected_t, result['T0'], rel_tol=1e-12), 'T0 calibration formula mismatch')
        require(cal['positive_count'] == len(positives) and cal['fallback'] == (not positives), 'calibration summary mismatch')
        cfg = result['configuration']; ev = result['evaluations']
        require(all(type(v) is int and v >= 0 for v in ev.values()), 'invalid evaluation count')
        require(ev['total'] == sum(v for k, v in ev.items() if k != 'total') and ev['total'] <= cfg['budget'], 'evaluation budget mismatch')
        current = {eid: tuple(pos) for eid, pos in result['initial'].items()}
        best = current.copy(); best_q = model.score(best)
        feasible = current.copy() if best_q['feasible'] else None
        feasible_q = best_q if feasible is not None else None
        previous_q = best_q; stale = 0; temperature = result['T0']
        rows = result['search_log']['rows']; columns = result['search_log']['columns']
        require(len(rows) == ev['search'], 'search log count mismatch')
        start_index = ev['construction_candidates']+ev['initial_score']+ev['calibration']+1
        for index, values in enumerate(rows, 1):
            require(len(values) == len(columns), 'malformed search log row')
            r = dict(zip(columns, values)); checked_steps += 1; stale += 1
            require(r['evaluation_index'] == start_index+index and r['search_evaluation'] == index, 'evaluation index mismatch')
            require(temperature >= cfg['Tmin'], 'continued after Tmin')
            require(math.isclose(r['temperature'], temperature, rel_tol=1e-12), 'cooling mismatch')
            require(1 <= r['generation_attempts'] <= 50, 'candidate attempt limit violation')
            require(sum(r['attempted_operators'].values()) == r['generation_attempts'], 'operator attempts mismatch')
            changes = r['accepted_changes']
            require(len({c[0] for c in changes}) == len(changes), 'duplicate changed event')
            if not r['accepted']: require(not changes, 'rejected candidate mutated current')
            if r['operator'] == 'no_change':
                require(r['generation_attempts'] == 50 and not changes and r['delta'] == 0, 'invalid no-change record')
            elif r['accepted']:
                require(len(changes) == (1 if r['operator'] == 'move' else 2), 'wrong move/swap change count')
            old = current.copy()
            for eid, day, period in changes:
                require(eid in current and (day, period) in model.allowed[eid], 'invalid placement / unknown event')
                current[eid] = (day, period)
            if r['operator'] == 'swap' and r['accepted']:
                a, b = [c[0] for c in changes]
                require(current[a] == old[b] and current[b] == old[a], 'not a reciprocal swap')
            score = model.score(current)
            require(not quality_problems(score, model.rules), 'invalid replay quality')
            if r['accepted']: require(math.isclose(r['delta'], score['F']-previous_q['F'], abs_tol=1e-12), 'delta mismatch')
            if r['delta'] <= 0: require(r['accepted'] and r['random_draw'] is None, 'non-uphill candidate rejected')
            else:
                p = math.exp(-r['delta']/temperature)
                require(math.isclose(p, r['acceptance_probability'], abs_tol=1e-12), 'acceptance probability mismatch')
                require(0 <= r['random_draw'] < 1 and r['accepted'] == (r['random_draw'] < p), 'random acceptance mismatch')
            if score['F'] < best_q['F']: best, best_q, stale = current.copy(), score, 0
            if score['feasible'] and (feasible_q is None or score['S'] < feasible_q['S']): feasible, feasible_q = current.copy(), score
            for prefix, q in [('current', score), ('best', best_q)]:
                require(all(r[prefix+'_'+key] == q[key] for key in ('H', 'S', 'F')), 'logged quality mismatch '+prefix)
            require(r['best_feasible_F'] == (feasible_q['F'] if feasible_q else None) and r['best_feasible_S'] == (feasible_q['S'] if feasible_q else None), 'logged best feasible mismatch')
            if index % stride == 0:
                require(score_equal(model.evaluate(current), score), 'independent replay validation mismatch'); independent_checks += 1
            previous_q = score
            if index % cfg['L'] == 0: temperature *= cfg['alpha']
            if index < len(rows): require(stale < cfg['stagnation'], 'continued after stagnation limit')
        for key, expected in [('current', current), ('best', best), ('best_feasible', feasible)]:
            stored = result[key]
            stored = {eid: tuple(pos) for eid, pos in stored.items()} if stored is not None else None
            require(stored == expected, 'replay final state mismatch '+key)
        require(stale == result['stagnation_search_evaluations'], 'stagnation counter mismatch')
        require(temperature == result['final_temperature'], 'final temperature mismatch')
        conditions = []
        if ev['total'] >= cfg['budget']: conditions.append('TOTAL_EVALUATION_BUDGET')
        if temperature < cfg['Tmin']: conditions.append('TMIN')
        if stale >= cfg['stagnation']: conditions.append('STAGNATION')
        require(conditions and conditions == result['stop_conditions'] and conditions[0] == result['stop_reason'], 'stop reason mismatch')
    except (PilotAnomaly, KeyError, TypeError, ValueError, IndexError, OverflowError) as error:
        issues.append(str(error))
    return {'status': 'PASS' if not issues else 'FAIL', 'anomalies': issues,
            'replayed_search_evaluations': checked_steps, 'independent_checks': independent_checks}


def summarize(result, audit, monitor, audit_seconds, warning_seconds):
    records = [dict(zip(result['search_log']['columns'], r)) for r in result['search_log']['rows']]
    uphill = sum(r['delta'] > 0 for r in records)
    worse = sum(r['delta'] > 0 and r['accepted'] for r in records)
    stats = result['candidate_statistics']
    return {'seed': result['seed'], 'T0': result['T0'], 'calibration_fallback': result['calibration']['fallback'],
            'initial': {k: result['initial_quality'][k] for k in ('H', 'S', 'F')},
            'best': {k: result['best_quality'][k] for k in ('H', 'S', 'F')},
            'best_feasible': result['best_feasible_quality'] is not None and result['best_feasible_quality']['feasible'],
            'evaluations': result['evaluations'], 'runtime_seconds': result['runtime_seconds'],
            'online_audit_seconds': monitor.audit_seconds, 'postrun_audit_seconds': audit_seconds,
            'runtime_scope': 'engine wall time including online audit; excludes post-run audit and file IO',
            'accepted_worse_count': worse, 'uphill_proposals': uphill,
            'accepted_worse_rate': worse/uphill if uphill else None,
            'rate_denominator': 'search proposals with positive delta; no calibration samples',
            'move_count': stats.get('move_evaluations', 0), 'swap_count': stats.get('swap_evaluations', 0),
            'no_change_count': stats.get('no_change_evaluations', 0),
            'accepted_structural_changes': sum(bool(r['accepted_changes']) for r in records),
            'candidate_statistics': stats, 'stop_reason': result['stop_reason'], 'audit': audit,
            'online_score_checks': monitor.calls, 'online_independent_checks': monitor.independent_checks,
            'warnings': ['Runtime needs review'] if result['runtime_seconds'] > warning_seconds else []}


def freeze_snapshot(config, context, summaries, hashes, evidence):
    stable = (len(summaries) == 5 and sorted(r['seed'] for r in summaries) == sorted(config['pilot_seeds'])
              and all(r['audit']['status'] == 'PASS' and r['evaluations']['search'] > 0 for r in summaries)
              and all(sum(r[k] for r in summaries) > 0 for k in ['move_count', 'swap_count', 'accepted_structural_changes']))
    if not stable: raise PilotAnomaly('Pilot incomplete, anomalous or search did not move; cannot freeze')
    ready = context['dataset_kind'] == 'SCHOOL_PILOT' and not context['blockers'] and not any(r['warnings'] for r in summaries)
    return {'artifact': 'FROZEN_EXPERIMENT_CONFIG', 'schema_version': 1,
            'parameter_status': 'FROZEN_AFTER_PILOT',
            'freeze_scope': 'SCHOOL_DATASET' if ready else 'TECHNICAL_VERIFICATION_ONLY',
            'ready_for_main_experiment': ready, 'main_execution_authorized': False,
            'main_experiment_status': 'READY_CONFIGURATION_ONLY' if ready else 'BLOCKED BY REAL DATA',
            'limitations': context['blockers'] + ([] if ready else ['Synthetic verification cannot establish school-scale runtime or final school-data validity.']),
            'protocol': config, 'protocol_hash': stable_hash(config), 'input_context': context,
            'code_hashes': hashes, 'evidence_hashes': evidence,
            'pilot_seeds_completed': [r['seed'] for r in summaries],
            'selection_policy': 'Parameters declared before pilot; no main results consulted. Any later change requires a new version and pilot.',
            'main_runs_executed': 0}


def report_markdown(context, summaries, frozen):
    lines = ['# Hasil uji pendahuluan', '', 'Jenis data: **'+context['dataset_kind']+'**.',
             'Status eksperimen utama: **'+frozen['main_experiment_status']+'**.', '',
             'Runtime mencakup audit online; audit setelah run dilaporkan terpisah. Rate accepted-worse memakai jumlah kandidat search dengan delta positif sebagai penyebut.', '',
             '| Seed | T0 | Initial H / S / F | Best H / S / F | Feasible | Evaluasi | Runtime s | Worse diterima / uphill | Move / swap / no-change | Stop | Audit |',
             '|---|---|---|---|---|---|---|---|---|---|---|']
    for r in summaries:
        triplet = lambda q: f"{q['H']} / {q['S']:.6f} / {q['F']:.6f}"
        rate = '-' if r['accepted_worse_rate'] is None else f"{100*r['accepted_worse_rate']:.2f}%"
        lines.append(f"| {r['seed']} | {r['T0']:.6f} | {triplet(r['initial'])} | {triplet(r['best'])} | {r['best_feasible']} | {r['evaluations']['total']} | {r['runtime_seconds']:.3f} | {r['accepted_worse_count']} / {r['uphill_proposals']} ({rate}) | {r['move_count']} / {r['swap_count']} / {r['no_change_count']} | {r['stop_reason']} | {r['audit']['status']} |")
    lines += ['', '## Batas kesimpulan', '', 'Pilot ini memeriksa kebenaran teknis; hasilnya bukan hasil akhir skripsi. Tidak ada main experiment dijalankan.', '',
              'FROZEN_EXPERIMENT_CONFIG.json merekam parameter dan bukti pilot. Flag ready_for_main_experiment menjadi gerbang kesiapan; nama snapshot tidak menghapus blocker data.']
    lines += ['', *['- '+b for b in frozen['limitations']], '']
    return '\n'.join(lines)


def execute(config, output):
    model, context = select_inputs(config)
    output = Path(output)
    if output.exists(): raise ValueError('Output already exists; use a new directory for a new pilot version')
    output.mkdir(parents=True)
    hashes = {name: hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest() for name in CODE_FILES}
    write_json(output/'config-snapshot.json', config)
    write_json(output/'dataset-snapshot.json', thaw(model.data))
    write_json(output/'rules-snapshot.json', thaw(model.rules))
    write_json(output/'input-readiness.json', context)
    write_json(output/'environment.json', {'python': sys.version, 'platform': platform.platform(), 'code_hashes': hashes})
    cfg = engine_config(config, len(model.ids)); summaries = []
    for seed in config['pilot_seeds']:
        print(f'Pilot seed {seed}: start ({context["dataset_kind"]})', flush=True)
        monitor = AuditedModel(model, config['audit']['independent_check_every'])
        try:
            result = run(monitor, cfg, seed)
            result['purpose'] = context['dataset_kind']
            rows = model.timetable(result['best_feasible']) if result['best_feasible'] is not None else None
            started = time.perf_counter()
            audit = audit_run(model, result, config['audit']['independent_check_every'], rows)
            audit_seconds = time.perf_counter()-started
            write_json(output/f'seed-{seed}/run.json', result, compact=True)
            if rows is not None: write_json(output/f'seed-{seed}/best-feasible-timetable.json', rows)
            write_json(output/f'seed-{seed}/audit.json', audit)
            if audit['status'] != 'PASS': raise PilotAnomaly('; '.join(audit['anomalies']))
            summary = summarize(result, audit, monitor, audit_seconds, config['audit']['runtime_warning_seconds'])
            summaries.append(summary)
            write_json(output/'summary.json', {'context': context, 'runs': summaries})
            print(f'Pilot seed {seed}: PASS, {result["stop_reason"]}, {result["evaluations"]["total"]} evaluations, {result["runtime_seconds"]:.2f}s', flush=True)
        except Exception as error:
            write_json(output/'failure.json', {'seed': seed, 'error': str(error), 'type': type(error).__name__,
                       'parameter_status': 'NOT_FROZEN', 'main_execution_authorized': False})
            raise
    evidence = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob('*.json')}
    frozen = freeze_snapshot(config, context, summaries, hashes, evidence)
    frozen['snapshot_hash'] = stable_hash(frozen)
    write_json(output/'FROZEN_EXPERIMENT_CONFIG.json', frozen)
    (output/'PILOT_REPORT.md').write_text(report_markdown(context, summaries, frozen), encoding='utf-8')
    return {'output': str(output.resolve()), 'pilot_status': 'STABLE_TECHNICAL_VERIFICATION',
            'main_experiment_status': frozen['main_experiment_status'], 'ready_for_main_experiment': frozen['ready_for_main_experiment'],
            'runs_completed': len(summaries), 'main_runs_executed': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check', 'run'])
    parser.add_argument('--config', default=str(DEFAULT_CONFIG)); parser.add_argument('--output')
    args = parser.parse_args(argv); config = read_json(args.config)
    if args.command == 'check':
        model, context = select_inputs(config)
        result = {**context, 'events': len(model.ids), 'engine_configuration': engine_config(config, len(model.ids))}
    else:
        if not args.output: parser.error('run requires --output (new directory)')
        result = execute(config, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
