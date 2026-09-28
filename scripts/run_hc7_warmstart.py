"""HC7 warm-start cohort runner: repaired baseline + feasible-only SA (HC1-HC7 screened).

Separate from frozen constructive main experiment and from older warm-start
cohorts (v1 total-JP A/B/C invalidated twice: first by HC7 introduction,
then by the 2026-09-24 period-rule correction). Resume-safe per seed.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from export_august import read_json
from final_experiment import atomic_json, atomic_text
from final_foundation import thaw
from final_impact import csv_table, write_report
from final_rules import stable_hash
from final_validator import validate
from sa_feasible_recommendations import (search, select, readable, html_schedule,
                                         teacher_readable, html_teacher_schedule)
from school_experiment import select_school_inputs

OUTPUT = Path(sys.argv[sys.argv.index("--output") + 1]) if "--output" in sys.argv else ROOT / "results" / "sa-feasible-hc7" / "v2"
REPAIR = Path(sys.argv[sys.argv.index("--repair") + 1]) if "--repair" in sys.argv else ROOT / "results" / "final-diagnostics" / "school-baseline-repair-hc7-v2" / "repair-candidate.json"
CONFIG = Path(sys.argv[sys.argv.index("--config") + 1]) if "--config" in sys.argv else ROOT / "config" / "sa-feasible-hc7.v2.json"


def main():
    config = read_json(CONFIG)
    protocol = Path(sys.argv[sys.argv.index("--protocol") + 1]) if "--protocol" in sys.argv else ROOT / "config" / "school-experiment.user-approved.v1.json"
    model, _ = select_school_inputs(read_json(protocol))
    repair = read_json(REPAIR)
    assert repair["dataset_hash"] == model.dataset_hash and repair["rules_hash"] == model.rules_hash, "repair provenance mismatch"
    initial = {eid: tuple(p) for eid, p in repair["placement"].items()}
    manifest = {"config": config, "dataset_hash": model.dataset_hash, "rules_hash": model.rules_hash,
                "initial_hash": stable_hash(initial)}
    if OUTPUT.exists():
        old = read_json(OUTPUT / "method-snapshot.json")
        assert old == manifest, "cannot mix versions"
    else:
        OUTPUT.mkdir(parents=True)
        atomic_json(OUTPUT / "method-snapshot.json", manifest)
        atomic_json(OUTPUT / "initial-placement.json", repair["placement"])
    runs = []
    for seed in config["seeds"]:
        folder = OUTPUT / f"seed-{seed}"
        path = folder / "result.json"
        if path.exists():
            result = read_json(path)
            assert result["initial_hash"] == manifest["initial_hash"] and result["dataset_hash"] == model.dataset_hash and result["rules_hash"] == model.rules_hash, "run provenance changed"
            assert validate(model, model.timetable(result["best_feasible"]))["feasible"], "cached run is infeasible"
            print(f"Seed {seed}: validated existing run", flush=True)
        else:
            print(f"Seed {seed}: feasible HC7 SA start", flush=True)
            result = search(model, initial, config, seed)
            folder.mkdir(parents=True, exist_ok=True)
            atomic_json(path, result)
            atomic_json(folder / "best-feasible-timetable.json", model.timetable(result["best_feasible"]))
            cols = result["search_log"]["columns"]
            curve = [{k: r[cols.index(k)] for k in ("evaluation", "search_evaluation", "temperature", "best_F", "best_H", "best_S")}
                     for r in result["search_log"]["rows"]]
            atomic_text(folder / "convergence.csv", csv_table(curve))
            q = result["best_feasible_quality"]
            print(f"Seed {seed}: H={q['H']} S={q['S']:.8f} HC7={q['HC']['HC7']}, search={result['search_evaluations']}, {result['stop_reason']}, {result['runtime_seconds']:.1f}s", flush=True)
        runs.append(result)
        selection = select(model, runs, initial, config)
        atomic_json(OUTPUT / "selection.json", thaw(selection))
        atomic_json(OUTPUT / "progress.json", {"completed_seeds": [r["seed"] for r in runs],
                                               "selected": len(selection["recommendations"]),
                                               "unique_best": selection["unique_noninitial_sa_best"]})
        if len(selection["recommendations"]) == config["selection"]["count"]:
            break
    comparisons = []
    for label, selected in zip("ABC", selection["recommendations"]):
        rows = model.timetable(selected["solution"])
        checked = validate(model, rows)
        assert checked["feasible"], checked["HC"]
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
    if comparisons and not (OUTPUT / "impact").exists():
        write_report(model, comparisons, OUTPUT / "impact",
                     {"method": config["method_version"], "completed_sa_runs": len(runs), "not_main_180_runs": True})
    print(f"Completed {len(runs)} SA runs; selected {len(comparisons)} independently feasible HC7 recommendations.", flush=True)


if __name__ == "__main__":
    main()
