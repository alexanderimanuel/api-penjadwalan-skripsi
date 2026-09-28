"""CP-SAT batch feasibility: one solve per seed, shared model load."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cp_construct import OUTPUT, load, solve
from export_august import read_json
from final_experiment import atomic_json
from final_validator import validate


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--start', type=int, default=5000)
    p.add_argument('--count', type=int, default=30)
    p.add_argument('--output', type=Path, default=None)
    a = p.parse_args()
    outdir = a.output if a.output else OUTPUT
    seeds = list(range(a.start, a.start + a.count))
    model = load()
    for seed in seeds:
        path = outdir / f"attempt-seed-{seed}.json"
        if path.exists():
            print(f"seed {seed}: exists, skip", flush=True)
            continue
        res = solve(model, seed, 300, 8)
        out = {'engine': 'cp-sat-construct-v1', 'method': 'CP-SAT feasibility batch for SA polish',
               'dataset_hash': model.dataset_hash, 'rules_hash': model.rules_hash,
               'rules_version': model.rules['rules_version'], 'result': res}
        if res['placement'] is not None:
            check = validate(model, model.timetable({e: tuple(p) for e, p in res['placement'].items()}))
            out['independent_validation'] = {'H': check['H'], 'HC': check['HC'], 'feasible': check['feasible'],
                                             'S': check['S'], 'raw': check['raw']}
        outdir.mkdir(parents=True, exist_ok=True)
        atomic_json(path, out)
        v = out.get('independent_validation', {})
        print(f"seed {seed}: {res['status']} {res['runtime_seconds']:.1f}s H={v.get('H')} S={v.get('S')}", flush=True)


if __name__ == '__main__':
    main()
