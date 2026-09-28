"""CP-SAT constructive feasibility for the school timetable (phase 1: 1 trial solve).

Variables range only over validated calendar domains with HC7-violating
Friday positions removed upfront (exact per-row HC7). HC1/HC2 become
at-most-one constraints per (resource, day, KBM period) after asserting
distinct periods never overlap in clock minutes (the validator's overlap
unit). HC8-first-KBM becomes a prefix constraint on class-day occupancy:
period k+1 occupied implies period k occupied, so each class-day is empty
or starts at the first KBM period with no holes. HC3/HC4/HC5/HC6 hold by
construction (every event placed exactly once, inside its validated
domain, immutable fields untouched). Proven by the independent validator.
"""
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ortools.sat.python import cp_model

from export_august import read_json
from final_experiment import atomic_json
from final_rules import friday_limit
from final_validator import validate
from school_experiment import select_school_inputs

PROTOCOL = ROOT / "config" / "school-experiment.hc7hc8.v2.json"
OUTPUT = ROOT / "results" / "cp-construct" / "v1"


def load():
    model, _ = select_school_inputs(read_json(PROTOCOL))
    return model


def minute_spans(model):
    spans = {}
    for day, dinfo in model.days.items():
        for s in dinfo['segments']:
            if s['period'] is not None:
                spans[day, s['period']] = (s['start_minute'], s['end_minute'])
    for day in model.days:
        ps = sorted(p for d, p in spans if d == day)
        for a, b in zip(ps, ps[1:]):
            if spans[day, a][1] > spans[day, b][0]:
                raise ValueError(f'Calendar slots overlap in minutes: {day} {a} {b}')
    return spans


def filtered_domains(model):
    domains = {}
    for eid in model.ids:
        keep = []
        for day, start in model.domains[eid]:
            e = model.events[eid]
            last = start + e['time']['jp'] - 1
            if day == 'jumat' and any(last > friday_limit(model.rules, c) for c in e['class_ids']):
                continue
            keep.append((day, start))
        if not keep:
            raise ValueError('HC7 filtering emptied a domain: ' + eid)
        domains[eid] = keep
    return domains


def solve(model, seed=7, timelimit=600, workers=8):
    spans = minute_spans(model)
    domains = filtered_domains(model)
    removed = sum(len(model.domains[e]) - len(domains[e]) for e in model.ids)
    m = cp_model.CpModel()
    x = {}
    b = {}
    for eid in model.ids:
        x[eid] = m.NewIntVar(0, len(domains[eid]) - 1, f'x_{eid}')
        b[eid] = []
        for i in range(len(domains[eid])):
            v = m.NewBoolVar(f'b_{eid}_{i}')
            m.Add(x[eid] == i).OnlyEnforceIf(v)
            m.Add(x[eid] != i).OnlyEnforceIf(v.Not())
            b[eid].append(v)
    cover = defaultdict(list)
    for eid in model.ids:
        e = model.events[eid]
        resources = [('t', t) for t in e['teacher_codes']] + [('c', c) for c in e['class_ids']]
        for i, (day, start) in enumerate(domains[eid]):
            for p in range(start, start + e['time']['jp']):
                if p not in model.active[day]:
                    continue
                for r in resources:
                    cover[r[0], r[1], day, p].append(b[eid][i])
    for key, vs in cover.items():
        m.Add(sum(vs) <= 1)
    y = {}
    for (kind, r, day, p), vs in cover.items():
        if kind != 'c':
            continue
        y[r, day, p] = m.NewBoolVar(f'y_{r}_{day}_{p}')
        for v in vs:
            m.Add(y[r, day, p] >= v)
        m.Add(y[r, day, p] <= sum(vs))
    for c in {c for e in model.events.values() for c in e['class_ids']}:
        for day in model.days:
            kbm = sorted(model.active[day])
            for a, cc in zip(kbm, kbm[1:]):
                ya, yb = y.get((c, day, a)), y.get((c, day, cc))
                if ya is not None and yb is not None:
                    m.Add(yb <= ya)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = timelimit
    solver.parameters.num_search_workers = workers
    solver.parameters.random_seed = seed
    t0 = time.perf_counter()
    status = solver.Solve(m)
    runtime = time.perf_counter() - t0
    name = solver.StatusName(status)
    placement = None
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        placement = {eid: [str(d), int(s)] for eid, (d, s) in
                     ((e, domains[e][solver.Value(x[e])]) for e in model.ids)}
    return {'status': name, 'runtime_seconds': runtime, 'placement': placement,
            'bools': sum(len(v) for v in b.values()), 'friday_filtered': removed,
            'seed': seed, 'timelimit': timelimit}


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--seed', type=int, default=7)
    p.add_argument('--timelimit', type=float, default=600)
    p.add_argument('--workers', type=int, default=8)
    a = p.parse_args()
    model = load()
    res = solve(model, a.seed, a.timelimit, a.workers)
    out = {'engine': 'cp-sat-construct-v1', 'method': 'CP-SAT feasibility then SA polish (phase 1: feasibility only)',
           'dataset_hash': model.dataset_hash, 'rules_hash': model.rules_hash,
           'rules_version': model.rules['rules_version'], 'result': res}
    if res['placement'] is not None:
        check = validate(model, model.timetable({e: tuple(p) for e, p in res['placement'].items()}))
        out['independent_validation'] = {'H': check['H'], 'HC': check['HC'], 'feasible': check['feasible'],
                                         'S': check['S'], 'raw': check['raw']}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    atomic_json(OUTPUT / f'attempt-seed-{a.seed}.json', out)
    print(f"status={res['status']} runtime={res['runtime_seconds']:.1f}s", flush=True)
    if 'independent_validation' in out:
        print('validator:', out['independent_validation'], flush=True)


if __name__ == '__main__':
    main()
