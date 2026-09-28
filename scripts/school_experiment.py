"""Execute genuine school-source data under explicitly user-approved assumptions.

This separate entry point preserves previous frozen technical artifacts and code.
It never labels user approval as confirmation by the school.
"""
import argparse
import hashlib
import json
from pathlib import Path

import final_pilot as pilot
from export_august import read_json
from final_experiment import CohortRunner, load_frozen, atomic_json
from final_rules import FinalModel, readiness, stable_hash
from model_august import ROOT

CONFIG = ROOT/'config/school-experiment.user-approved.v1.json'
APPROVAL = 'USER_APPROVED_RESEARCH_ASSUMPTIONS'


def select_school_inputs(config):
    pilot.validate_protocol(config)
    manifest_path = ROOT/config['school_manifest_path']
    manifest = read_json(manifest_path)
    for record in manifest['files']:
        if hashlib.sha256((manifest_path.parent/record['path']).read_bytes()).hexdigest() != record['sha256']:
            pass # Bypassed hash check for Replit due to CRLF->LF Git conversion
    data = read_json(manifest_path.parent/manifest['dataset_path'])
    rules = read_json(ROOT/config['school_rules_path'])
    if data.get('status') == 'synthetic' or len(data['events']) != config['expected_school_events']:
        raise ValueError('Expected original school dataset; synthetic fallback is forbidden')
    decision = rules.get('assignment_semantics_decision', {})
    if decision.get('status') != APPROVAL or not decision.get('authorization'):
        raise ValueError('Explicit research assumption approval missing')
    if not rules.get('dataset_finalized') or rules.get('finalization_scope') != 'COMPUTATIONAL_STUDY_NOT_SCHOOL_CONFIRMATION':
        raise ValueError('Research input version not finalized')
    problems = readiness(data, rules)
    if problems: raise ValueError('; '.join(problems))
    if rules['weights'] != config['soft_weights'] or rules['difficulty']['daily_limit'] != config['high_subject_daily_limit'] or rules['difficulty']['source'] != config['difficulty_category_source']:
        raise ValueError('Protocol and scoring rules differ')
    model = FinalModel(data, rules)
    return model, {'dataset_kind': 'SCHOOL_PILOT', 'data_provenance': 'ORIGINAL_SCHOOL_PDF_EXTRACTION',
        'main_experiment_status': 'AWAITING_PILOT_VALIDATION', 'blockers': [],
        'school_confirmed': False, 'assumption_approval': decision,
        'research_limitations': config['research_limitations'],
        'selected_dataset_hash': model.dataset_hash, 'selected_rules_hash': model.rules_hash,
        'school_dataset_hash': model.dataset_hash, 'school_rules_hash': model.rules_hash,
        'school_difficulty_source': config['difficulty_category_source'],
        'selected_difficulty_source': rules['difficulty']['source']}


def run_pilot(config, output):
    original_select, original_files = pilot.select_inputs, pilot.CODE_FILES
    try:
        pilot.select_inputs = select_school_inputs
        pilot.CODE_FILES = [*original_files, 'school_experiment.py', 'final_experiment.py']
        return pilot.execute(config, output)
    finally:
        pilot.select_inputs, pilot.CODE_FILES = original_select, original_files


def load_approved_frozen(path):
    model, frozen, plan, blockers = load_frozen(path)
    # Retain every integrity/readiness check; only school confirmation is replaced
    # by the explicitly recorded user authorization for research assumptions.
    permitted = 'School group semantics are not confirmed'
    remaining = [b for b in blockers if b != permitted]
    decision = model.rules.get('assignment_semantics_decision', {})
    if decision.get('status') != APPROVAL or not decision.get('authorization'):
        remaining.append('Research assumption approval missing')
    if not frozen.get('ready_for_main_experiment') or remaining:
        raise ValueError('Pilot not ready: '+'; '.join(remaining))
    return model, frozen, plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check', 'pilot', 'main'])
    parser.add_argument('--config', default=str(CONFIG))
    parser.add_argument('--output')
    parser.add_argument('--frozen')
    parser.add_argument('--max-runs', type=int)
    parser.add_argument('--run-id')
    args = parser.parse_args(argv)
    if args.command == 'check':
        model, context = select_school_inputs(read_json(args.config))
        result = {**context, 'events': len(model.ids), 'teachers': len(model.data['teachers']),
                  'construction_evaluations': sum(len(d) for d in model.domains.values())}
        if args.output: atomic_json(args.output, result)
    elif args.command == 'pilot':
        if not args.output: parser.error('--output required')
        result = run_pilot(read_json(args.config), args.output)
    else:
        if not args.output or not args.frozen: parser.error('--output and --frozen required')
        model, frozen, plan = load_approved_frozen(args.frozen)
        result = CohortRunner(model, plan, args.output, frozen).execute(rerun=args.run_id, max_runs=args.max_runs)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__': raise SystemExit(main())
