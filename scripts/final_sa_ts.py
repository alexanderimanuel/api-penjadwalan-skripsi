"""SA then Tabu Search chain for the constructive main experiment.
Phase 1 is unchanged final-sa-v2 dynamics (randomized greedy construction,
calibration, Metropolis search) capped at SA_BUDGET evaluations: all 180
prior SA-only runs stagnated at 20000 search evaluations (~27000 total), so
the cap loses nothing. Phase 2 is tabu search from the SA best solution with
an H-lexicographic objective: because |delta S| < 2 <= |delta H| under
F = 2H + S, ordering by (H, F) is identical to ordering by F, with hard
violations strictly dominant. Same result schema as final_sa.run so the
audited cohort runner, validation, metrics and selection work unchanged.
"""
import copy
import random
import time
from collections import Counter
from final_foundation import thaw
from final_rules import stable_hash
from final_sa import (SEARCH_LOG_COLUMNS, calibrate, construct, metropolis,
                      neighbor, score_equal)
from final_validator import validate

ENGINE_VERSION = 'final-sa-ts-v1'
SA_BUDGET = 30000
TS_CANDIDATES = 32
TS_TENURE = 30
TS_STAGNATION = 500
TS_CONFIG = {'sa_budget': SA_BUDGET, 'candidates_per_iteration': TS_CANDIDATES,
             'tabu_tenure_iterations': TS_TENURE, 'stagnation_iterations': TS_STAGNATION,
             'objective': 'lexicographic_H_then_F_identical_to_F'}


def run(model, config, seed):
    cfg = {'budget': 100000, 'Tmin': .001, 'stagnation': 20000, 'calibration_samples': 200, 'p0': .8,
           'max_attempts': 50, 'swap_probability': .5, **config}
    if type(seed) is not int:
        raise ValueError('Seed must be integer')
    if not model.ids:
        raise ValueError('Search requires at least one event')
    if type(cfg.get('alpha')) not in (int, float) or not 0 < cfg['alpha'] < 1 or any(
            type(cfg.get(k)) is not int or cfg[k] < 1 for k in ['budget', 'L', 'stagnation']):
        raise ValueError('Invalid run parameters')
    if cfg['budget'] != 100000:
        raise ValueError('Chained budget must be 100000')
    if cfg['calibration_samples'] != 200 or cfg['p0'] != .8 or cfg['max_attempts'] != 50 or cfg['swap_probability'] != .5:
        raise ValueError('Final methodology constants changed')
    minimum = sum(map(len, model.domains.values())) + 202 + 3
    if SA_BUDGET < minimum:
        raise ValueError('SA phase budget too small')
    started = time.perf_counter()
    streams = {'construction': seed, 'calibration': seed ^ 0x5A17,
               'search': int(stable_hash(['search', 'final-sa-v2', seed])[:16], 16),
               'ts': int(stable_hash(['ts', ENGINE_VERSION, seed])[:16], 16)}
    rng = random.Random(streams['search'])
    construction_trace = []
    current, init_count, order = construct(model, random.Random(streams['construction']), trace=construction_trace)
    initial = copy.deepcopy(current)
    score = model.score(current)
    checked = validate(model, model.timetable(current))
    if not score_equal(score, checked):
        raise ValueError('Independent initial validation mismatch')
    calibration = calibrate(model, initial, score, random.Random(streams['calibration']))
    T = calibration['T0']
    T0 = T
    best = current.copy()
    best_score = copy.deepcopy(score)
    feasible = current.copy() if score['feasible'] else None
    feasible_score = copy.deepcopy(score) if feasible is not None else None
    initial_score = copy.deepcopy(score)
    search = 0
    stale = 0
    counts = Counter()
    reasons = Counter()
    trace = []
    no_change_records = []
    search_log = []
    total = init_count + 2 + 200
    conditions = []
    while True:
        conditions = []
        if total + 3 >= SA_BUDGET:
            conditions.append('SA_BUDGET')
        if T < cfg['Tmin']:
            conditions.append('TMIN')
        if stale >= cfg['stagnation']:
            conditions.append('STAGNATION')
        if conditions:
            break
        y, meta = neighbor(model, current, rng, cfg['max_attempts'], cfg['swap_probability'])
        trial = model.score(y)
        search += 1
        total += 1
        stale += 1
        counts['generation_attempts'] += meta['attempts']
        counts[meta['operator'] + '_evaluations'] += 1
        reasons.update(meta['rejections'])
        if meta['operator'] == 'no_change':
            no_change_records.append({'search_evaluation': search, **meta})
        delta = trial['F'] - score['F']
        accepted, probability, draw = metropolis(delta, T, rng)
        changes = [[eid, *y[eid]] for eid in model.ids if accepted and y[eid] != current[eid]]
        if accepted:
            current = y
            score = trial
            counts['accepted'] += 1
            if delta > 0:
                counts['accepted_uphill'] += 1
        else:
            counts['rejected'] += 1
        if (score['H'], score['F']) < (best_score['H'], best_score['F']):
            best = current.copy()
            best_score = copy.deepcopy(score)
            stale = 0
            trace.append({'search_evaluations': search, 'total_evaluations': total, 'T': T, 'best': copy.deepcopy(best_score)})
        if score['feasible'] and (feasible_score is None or score['S'] < feasible_score['S']):
            feasible = current.copy()
            feasible_score = copy.deepcopy(score)
        search_log.append([total, search, T, meta['operator'], delta, accepted, probability, draw,
                           score['F'], score['H'], score['S'], best_score['F'], best_score['H'], best_score['S'],
                           feasible_score['F'] if feasible_score is not None else None,
                           feasible_score['S'] if feasible_score is not None else None,
                           meta['status'], meta['attempts'], meta['attempted_operators'], meta['rejections'], changes])
        if search % cfg['L'] == 0:
            T *= cfg['alpha']
    sa_stop = conditions[0]
    sa_search = search
    sa_total = total
    ts_rng = random.Random(streams['ts'])
    tabu = {}
    iteration = 0
    ts_stale = 0
    ts_evals = 0
    ts_improvements = 0
    ts_tabu_hits = 0
    ts_aspirations = 0
    ts_candidates_seen = 0
    first_feasible_iteration = None
    if feasible is not None:
        first_feasible_iteration = 0
    best_key = (best_score['H'], best_score['F'])
    ts_conditions = []
    while True:
        ts_conditions = []
        if total + TS_CANDIDATES + 3 >= cfg['budget']:
            ts_conditions.append('TS_BUDGET')
        if ts_stale >= TS_STAGNATION:
            ts_conditions.append('TS_STAGNATION')
        if ts_conditions:
            break
        iteration += 1
        ts_stale += 1
        expired = [a for a, e in tabu.items() if e < iteration]
        for a in expired:
            del tabu[a]
        winner = None
        for _ in range(TS_CANDIDATES):
            y, meta = neighbor(model, current, ts_rng, cfg['max_attempts'], cfg['swap_probability'])
            if meta['operator'] == 'no_change':
                continue
            counts['generation_attempts'] += meta['attempts']
            counts[meta['operator'] + '_evaluations'] += 1
            reasons.update(meta['rejections'])
            trial = model.score(y)
            ts_evals += 1
            search += 1
            total += 1
            ts_candidates_seen += 1
            changed = [eid for eid in model.ids if y[eid] != current[eid]]
            key = (trial['H'], trial['F'])
            is_tabu = any((eid, tuple(y[eid])) in tabu for eid in changed)
            aspirated = key < best_key
            if is_tabu and aspirated:
                ts_aspirations += 1
            if is_tabu and not aspirated:
                ts_tabu_hits += 1
                continue
            if winner is None or key < winner[0]:
                winner = (key, y, trial, meta, changed)
        if winner is None:
            continue
        _, y, trial, meta, changed = winner
        delta = trial['F'] - score['F']
        for eid in changed:
            tabu[(eid, tuple(current[eid]))] = iteration + TS_TENURE
        current = y
        score = trial
        counts['accepted'] += 1
        changes = [[eid, *current[eid]] for eid in changed]
        if (score['H'], score['F']) < best_key:
            best = current.copy()
            best_score = copy.deepcopy(score)
            best_key = (score['H'], score['F'])
            ts_stale = 0
            ts_improvements += 1
            trace.append({'search_evaluations': search, 'total_evaluations': total, 'T': None, 'best': copy.deepcopy(best_score)})
        if score['feasible']:
            if first_feasible_iteration is None:
                first_feasible_iteration = iteration
            if feasible_score is None or score['S'] < feasible_score['S']:
                feasible = current.copy()
                feasible_score = copy.deepcopy(score)
        search_log.append([total, search, None, meta['operator'], delta, True, 1.0, None,
                           score['F'], score['H'], score['S'], best_score['F'], best_score['H'], best_score['S'],
                           feasible_score['F'] if feasible_score is not None else None,
                           feasible_score['S'] if feasible_score is not None else None,
                           meta['status'], meta['attempts'], meta['attempted_operators'], meta['rejections'], changes])
    final_checks = {}
    for name, x, expected in [('best', best, best_score), ('current', current, score), ('best_feasible', feasible, feasible_score)]:
        if x is None:
            recheck = validate(model, model.timetable(best))
            total += 1
            if recheck['feasible']:
                raise ValueError('Feasible tracking lost a solution')
            final_checks[name] = None
            final_checks['no_feasible_recheck'] = recheck
            continue
        check = validate(model, model.timetable(x))
        total += 1
        if not score_equal(expected, check):
            raise ValueError('Final validator mismatch: ' + name)
        final_checks[name] = check
    if total > cfg['budget']:
        raise ValueError('Chained evaluations exceeded budget')
    return {'status': 'FEASIBLE' if feasible is not None else 'NO_FEASIBLE_FOUND', 'engine_version': ENGINE_VERSION,
            'dataset_version': model.data.get('id'), 'dataset_hash': model.dataset_hash, 'rules_version': model.rules['rules_version'],
            'rules_hash': model.rules_hash, 'difficulty_category_version': model.rules['difficulty']['version'],
            'normalization_version': model.rules['normalization_version'], 'weights_version': model.rules['weights_version'], 'weights': thaw(model.rules['weights']),
            'seed': seed, 'configuration': cfg, 'alpha': cfg['alpha'], 'L': cfg['L'], 'T0': T0, 'final_temperature': T,
            'evaluations': {'construction_candidates': init_count, 'initial_score': 1, 'calibration': 200, 'search': search,
                            'independent_validation': 1 + sum(v is not None for v in final_checks.values()), 'total': total},
            'stop_reason': sa_stop + '+TS_' + ts_conditions[0] if ts_conditions else sa_stop + '+TS_NONE',
            'stop_conditions': conditions + ['TS_' + c for c in ts_conditions],
            'stagnation_search_evaluations': stale,
            'calibration': calibration, 'runtime_seconds': time.perf_counter() - started, 'initial_quality': initial_score,
            'best_quality': best_score, 'current_quality': score, 'best_feasible_quality': feasible_score,
            'initial': initial, 'best': best, 'current': current, 'best_feasible': feasible,
            'initial_hash': stable_hash(initial), 'rng_stream_seeds': streams, 'construction_trace': construction_trace,
            'search_log': {'schema_version': 1, 'columns': SEARCH_LOG_COLUMNS.copy(), 'rows': search_log,
                           'state_values': 'after acceptance and best updates; SA rows carry temperature before cooling, TS rows carry null temperature and always move',
                           'replay': 'Start at initial; apply accepted_changes [event_id,day,start_period] in order.'},
            'stop_snapshot': {'search_evaluations': search, 'evaluations_before_final_validation': total - 3,
                              'reserved_final_validations': 3, 'temperature': T, 'stagnation': stale},
            'initial_order': order, 'candidate_statistics': dict(counts), 'candidate_rejection_reasons': dict(reasons), 'trace': trace,
            'no_change_records': no_change_records,
            'final_validation': final_checks, 'normalization': thaw(model.rules['normalization']),
            'scoring_backend': 'full occupancy recalculation; no incremental cache',
            'ts_config': dict(TS_CONFIG), 'sa_stop_reason': sa_stop, 'sa_search_evaluations': sa_search, 'sa_total_evaluations': sa_total,
            'ts_summary': {'iterations': iteration, 'candidate_evaluations': ts_evals, 'candidates_seen': ts_candidates_seen,
                           'improvements': ts_improvements, 'tabu_hits': ts_tabu_hits, 'aspirations': ts_aspirations,
                           'stagnation_iterations': ts_stale, 'stop_reason': ts_conditions[0] if ts_conditions else 'NONE',
                           'first_feasible_iteration': first_feasible_iteration}}
