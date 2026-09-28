"""Polish each CP-SAT feasible start with the proven warm-start SA (1:1 pairing).

Layout mirrors run_hc7_warmstart.py so the thesis generator accepts it:
seed-{sa_seed}/result.json, progress.json with completed_seeds, selection
excluding every unchanged CP start, recommendation files, impact report.
Resume-safe per seed. Runs all 30 (no early break) for the full pool.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from export_august import read_json
from final_experiment import atomic_json, atomic_text, digest
from final_foundation import thaw
from final_impact import csv_table, write_report
from final_rules import stable_hash
from final_validator import validate
from sa_feasible_recommendations import (search, select, readable, html_schedule,
                                         teacher_readable, html_teacher_schedule)
from school_experiment import select_school_inputs

def _abs(p):
    p = Path(p)
    return p if p.is_absolute() else (ROOT / p)


OUTPUT = _abs(sys.argv[sys.argv.index("--output") + 1]) if "--output" in sys.argv else ROOT / "results" / "cp-polish-hc7hc8" / "v1"
CPDIR = _abs(sys.argv[sys.argv.index("--cpdir") + 1]) if "--cpdir" in sys.argv else ROOT / "results" / "cp-construct" / "v1"
CONFIG = _abs(sys.argv[sys.argv.index("--config") + 1]) if "--config" in sys.argv else ROOT / "config" / "cp-polish-hc7hc8.v1.json"


def main():
    config = read_json(CONFIG)
    protocol = sys.argv[sys.argv.index("--protocol") + 1] if "--protocol" in sys.argv else ROOT / "config" / "school-experiment.hc7hc8.v2.json"
    protocol = _abs(protocol)
    model, _ = select_school_inputs(read_json(protocol))
    pairs = list(zip(config["cp_seeds"], config["sa_seeds"]))
    initials = {}
    for cp_seed, sa_seed in pairs:
        cp = read_json(CPDIR / f"attempt-seed-{cp_seed}.json")
        assert cp["dataset_hash"] == model.dataset_hash and cp["rules_hash"] == model.rules_hash, "CP provenance mismatch"
        assert cp["independent_validation"]["feasible"], f"CP seed {cp_seed} not feasible"
        initials[sa_seed] = ({eid: tuple(p) for eid, p in cp["result"]["placement"].items()}, cp_seed)
    manifest = {"config": config, "dataset_hash": model.dataset_hash, "rules_hash": model.rules_hash,
                "initial_hashes": sorted(stable_hash(x) for x, _ in initials.values()),
                "code_hash": digest(ROOT / "scripts" / "sa_feasible_recommendations.py")}
    if OUTPUT.exists():
        old = read_json(OUTPUT / "method-snapshot.json")
        assert old == manifest, "cannot mix versions"
    else:
        OUTPUT.mkdir(parents=True)
        atomic_json(OUTPUT / "method-snapshot.json", manifest)
    runs = []
    for sa_seed, (initial, cp_seed) in sorted(initials.items()):
        folder = OUTPUT / f"seed-{sa_seed}"
        path = folder / "result.json"
        if path.exists():
            result = read_json(path)
            assert validate(model, model.timetable(result["best_feasible"]))["feasible"], "cached run is infeasible"
            print(f"Seed {sa_seed} (CP {cp_seed}): validated existing run", flush=True)
        else:
            print(f"Seed {sa_seed} (CP {cp_seed}): polish start ({len(runs)+1}/{len(pairs)})", flush=True)
            result = search(model, initial, config, sa_seed)
            folder.mkdir(parents=True, exist_ok=True)
            atomic_json(path, result)
            atomic_json(folder / "best-feasible-timetable.json", model.timetable(result["best_feasible"]))
            atomic_json(folder / "cp-source.json", {"cp_seed": cp_seed, "cp_file": str((CPDIR / f"attempt-seed-{cp_seed}.json").relative_to(ROOT))})
            cols = result["search_log"]["columns"]
            curve = [{k: r[cols.index(k)] for k in ("evaluation", "search_evaluation", "temperature", "best_F", "best_H", "best_S")}
                     for r in result["search_log"]["rows"]]
            atomic_text(folder / "convergence.csv", csv_table(curve))
            q = result["best_feasible_quality"]
            print(f"Seed {sa_seed}: H={q['H']} S={q['S']:.8f} raw={q['raw']}, search={result['search_evaluations']}, {result['stop_reason']}, {result['runtime_seconds']:.1f}s", flush=True)
        runs.append(result)
        atomic_json(OUTPUT / "progress.json", {"completed_seeds": [r["seed"] for r in runs]})
    initials_list = [r["initial"] for r in runs]
    selection = select(model, runs, runs[0]["initial"], config, initials_list)
    atomic_json(OUTPUT / "selection.json", thaw(selection))
    comparisons = []
    for label, selected in zip("ABC", selection["recommendations"]):
        rows = model.timetable(selected["solution"])
        checked = validate(model, rows)
        assert checked["feasible"], checked["HC"]
        src = next(r for r in runs if r["seed"] == selected["seed"])
        atomic_json(OUTPUT / f"recommendation-{label}.json",
                    {"label": label, "method": config["method_version"], "seed": selected["seed"],
                     "dataset_hash": model.dataset_hash, "rules_hash": model.rules_hash,
                     "timetable": rows, "validation": checked})
        flat = readable(model, selected["solution"])
        atomic_text(OUTPUT / f"jadwal-{label}.csv", csv_table(flat))
        atomic_text(OUTPUT / f"jadwal-{label}.html", html_schedule(label, flat, model))
        atomic_text(OUTPUT / f"jadwal-guru-{label}.csv", csv_table(teacher_readable(flat)))
        atomic_text(OUTPUT / f"jadwal-guru-{label}.html", html_teacher_schedule(label, flat, model))
        comparisons.append({"label": label, "dataset_hash": model.dataset_hash, "rules_hash": model.rules_hash,
                            "timetable": rows, "source": {"method": config["method_version"], "seed": selected["seed"],
                                                          "not_main_180_runs": True}})
    if comparisons:
        write_report(model, comparisons, OUTPUT / "impact",
                     {"method": config["method_version"], "completed_sa_runs": len(runs), "not_main_180_runs": True})
    print(f"Completed {len(runs)} polish runs; selected {len(comparisons)} recommendations.", flush=True)


if __name__ == "__main__":
    main()
