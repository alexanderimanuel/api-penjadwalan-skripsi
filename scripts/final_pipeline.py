"""Final technical-audit entry point: real frozen inputs, unchanged constructive SA.

Historical warm-start experiments remain separate. Never manufacture missing A/B/C.
"""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import platform
from pathlib import Path
import sys

from export_august import read_json
from final_experiment import (CohortRunner, atomic_json, atomic_text, contained, digest,
    load_frozen, planned_runs, validate_result)
from final_foundation import thaw
from final_impact import write_report
from final_pilot import engine_config
from final_recommendations import select
from final_rules import FinalModel, stable_hash
from final_validator import validate
from model_august import ROOT
from school_experiment import select_school_inputs, run_pilot

CONFIG = ROOT/'config/school-experiment.user-approved.v1.json'
DEFAULT_PILOT = ROOT/'results/final-pilot/school-user-approved-v1'
DEFAULT_WORK = ROOT/'results/final-methodology-v2'
VERSION = 'final-technical-audit-v2'


def environment():
    return {'python': sys.version, 'implementation': platform.python_implementation(),
        'platform': platform.platform(), 'machine': platform.machine(),
        'processor': platform.processor(), 'dependencies': dict(sorted(
            (d.metadata['Name'], d.version) for d in importlib.metadata.distributions()
            if d.metadata.get('Name')))}


def code_hashes():
    return {p.name: digest(p) for p in sorted((ROOT/'scripts').glob('*.py'))
            if not p.name.startswith('test_')}


def counts(data):
    events = data['events']; ids = [e['id'] for e in events]
    if len(ids) != len(set(ids)): raise ValueError('Duplicate event IDs')
    return {'teachers': len(data['teachers']),
        'classes': len({c for e in events for c in e['class_ids']}),
        'subjects': len(data['subjects']), 'events': len(events),
        'class_jp': sum(e['time']['jp']*len(e['class_ids']) for e in events)}


def canonical_dataset(model, protocol):
    manifest = read_json(ROOT/protocol['school_manifest_path'])
    inventory = read_json(ROOT/'data/inventory/2026-08-03/register.json')
    return {'dataset_version': model.data['id'], 'dataset_snapshot_version': manifest['version'],
        'source_data_version': '2026-08-03',
        'effective_date': '2026-08-03', 'scope': 'FINAL_RESEARCH_INPUT',
        'dataset_hash': model.dataset_hash, 'counts': counts(model.data),
        'extraction_metadata': {'source_manifest': protocol['school_manifest_path'],
            'manifest_sha256': digest(ROOT/protocol['school_manifest_path']),
            'class_timetable_is_event_source': True, 'teacher_timetable_is_cross_check': True,
            'source_files': [{k: row[k] for k in ('id', 'frozen_path', 'sha256')}
                             for row in inventory['files']],
            'extraction_code': {name: digest(ROOT/'scripts'/name) for name in
                ('extract_august_entities.py', 'extract_august_blocks.py',
                 'build_august_calendar.py', 'reconcile_august.py')}},
        'dataset': thaw(model.data)}


def prepare(work, rebuild=False):
    protocol = read_json(CONFIG); model, _ = select_school_inputs(protocol)
    if rebuild:
        from reproduce_school_inputs import reproduce
        model = FinalModel(reproduce(work/'raw-reproduction', model.dataset_hash), thaw(model.rules))
    envelope = canonical_dataset(model, protocol)
    atomic_json(work/'canonical_dataset.json', envelope)
    return model


def baseline(model, work):
    x = {eid: (e['reference']['day'], e['reference']['start_period']) for eid,e in model.events.items()}
    result = validate(model, model.timetable(x))
    atomic_json(work/'baseline_metrics.json', result)
    return result


def freeze(work, pilot_dir):
    # Integrity checks retained; obsolete field-confirmation/runtime gates are not
    # applied to the user's final research data. No numeric parameter is changed.
    model, old, _, _ = load_frozen(pilot_dir/'FROZEN_EXPERIMENT_CONFIG.json')
    protocol = read_json(CONFIG)
    approved, _ = select_school_inputs(protocol)
    if (model.dataset_hash, model.rules_hash) != (approved.dataset_hash, approved.rules_hash):
        raise ValueError('Pilot does not use final dataset/rules')
    if stable_hash(protocol) != old['protocol_hash']: raise ValueError('Protocol differs from pilot')
    summary = read_json(pilot_dir/'summary.json')
    rows = summary['runs']
    if sorted(r['seed'] for r in rows) != sorted(protocol['pilot_seeds']):
        raise ValueError('Exactly five specified pilot seeds required')
    evidence = []
    for row in rows:
        if row['audit']['status'] != 'PASS': raise ValueError('Pilot anomaly unresolved')
        seed = row['seed']; path = pilot_dir/f'seed-{seed}'/'run.json'
        result = read_json(path)
        item = {'seed': seed, 'config': engine_config(protocol, len(model.ids))}
        validate_result(model, result, item, model.timetable(result['best_feasible']) if result['best_feasible'] else None)
        evidence.append({'seed': seed, 'result_path': str(path.relative_to(ROOT)),
                         'sha256': digest(path), 'fresh_final_validation': 'PASS',
                         'recorded_log_audit': row['audit']})
    payload = {'version': VERSION, 'scope': 'FINAL_RESEARCH_INPUT_CONSTRUCTIVE_SA',
        'protocol': protocol, 'protocol_hash': stable_hash(protocol),
        'dataset_hash': model.dataset_hash, 'rules_hash': model.rules_hash,
        'metadata': canonical_dataset(model, protocol), 'code_hashes': code_hashes(),
        'environment': environment(), 'pilot_evidence': evidence,
        'pilot_snapshot_hash': old['snapshot_hash'],
        'runtime_warnings': [r['seed'] for r in rows if r['warnings']],
        'runtime_policy': 'Warnings retained as observed costs; no numeric retuning or synthetic substitution.',
        'selection_policy': 'S, SC3, SC1, SC2, canonical ID; identical-event multisets; >=5% to every selected.'}
    payload['snapshot_hash'] = stable_hash(payload)
    path = work/'FROZEN_EXPERIMENT_CONFIG.json'
    if path.exists() and read_json(path) != payload: raise ValueError('Frozen inputs changed; use a new output version')
    atomic_json(path, payload); atomic_json(work/'pilot_results.json', {'runs': rows, 'evidence': evidence})
    return payload


def load_final(work):
    frozen = read_json(work/'FROZEN_EXPERIMENT_CONFIG.json')
    if stable_hash({k:v for k,v in frozen.items() if k != 'snapshot_hash'}) != frozen['snapshot_hash']:
        raise ValueError('Final frozen snapshot corrupted')
    if frozen['code_hashes'] != code_hashes(): raise ValueError('Source changed; invalidate/version results before rerunning')
    if frozen['environment'] != environment(): raise ValueError('Runtime changed; use the saved environment or a new version')
    model = FinalModel(frozen['metadata']['dataset'], read_json(ROOT/frozen['protocol']['school_rules_path']))
    if (model.dataset_hash, model.rules_hash) != (frozen['dataset_hash'], frozen['rules_hash']):
        raise ValueError('Final input hash mismatch')
    if stable_hash(read_json(CONFIG)) != frozen['protocol_hash']: raise ValueError('Active protocol changed')
    return model, frozen


class AuditedRunner(CohortRunner):
    """Add persistent complete metadata without modifying the frozen SA runner."""
    def validate_completed(self, item, pointer):
        summary = super().validate_completed(item, pointer)
        folder = contained(self.output/'runs'/item['run_id'], pointer['attempt'])
        marker = read_json(folder/'COMPLETED.json'); path = folder/'run_metadata.json'
        frozen = self.manifest['provenance']; meta = frozen['metadata']
        expected = {'run_id': item['run_id'], 'seed': item['seed'], 'config_id': item['config']['id'],
            'configuration': item['config'], 'dataset_version': meta['dataset_version'],
            'source_data_version': meta['source_data_version'], 'effective_date': meta['effective_date'],
            'dataset_snapshot_version': meta.get('dataset_snapshot_version', meta['dataset_version']),
            'dataset_hash': self.model.dataset_hash, 'rules_hash': self.model.rules_hash,
            'rules_version': self.model.rules['rules_version'],
            'difficulty_version': self.model.rules['difficulty']['version'],
            'daily_limit': self.model.rules['difficulty']['daily_limit'],
            'weights': thaw(self.model.rules['weights']), 'normalization': thaw(self.model.rules['normalization']),
            'protocol': frozen['protocol'], 'frozen_snapshot_hash': frozen['snapshot_hash'],
            'code_version': stable_hash(frozen['code_hashes']), 'code_hashes': frozen['code_hashes'],
            'git_commit': None, 'environment': frozen['environment'],
            'runtime': {k:v for k,v in summary.items() if k.startswith('runtime_')}}
        if 'run_metadata.json' not in marker['file_hashes']:
            status = read_json(folder/'status.json')
            if status['status'] != 'RUNNING' or not status.get('started_at'):
                raise ValueError('Completed run lacks original timestamp metadata')
            atomic_json(path, {**expected, 'started_at_utc': status['started_at'],
                'validated_at_utc': datetime.now(timezone.utc).isoformat()})
            marker['file_hashes']['run_metadata.json'] = digest(path)
            atomic_json(folder/'COMPLETED.json', marker)
        saved = read_json(path)
        if any(saved.get(k) != v for k,v in expected.items()): raise ValueError('Run metadata mismatch')
        if not saved.get('started_at_utc') or not saved.get('validated_at_utc'): raise ValueError('Run timestamp missing')
        return summary


def runner_for(work, engine_name=None):
    model, frozen = load_final(work)
    return AuditedRunner(model, planned_runs(frozen['protocol'], len(model.ids)), work/'main', frozen, engine_name)


def select_completed(work, partial=False):
    runner = runner_for(work); model = runner.model; runs = []
    for item in runner.plan:
        path = runner.output/'runs'/item['run_id']/'CURRENT.json'
        if not path.exists(): continue
        pointer = read_json(path)
        if pointer['status'] != 'COMPLETED': continue
        runner.validate_completed(item, pointer)
        runs.append(read_json(contained(path.parent, pointer['attempt'])/'result.json'))
    if len(runs) != 180 and not partial: raise ValueError('Final selection requires 180 valid completed runs; use --partial only for dry-run')
    result = select(model, runs)
    result.update(scope='DRY_RUN_PARTIAL' if len(runs) != 180 else 'FINAL_180_RUNS',
                  completed_runs=len(runs), expected_runs=180)
    atomic_json(work/'candidate_pool.json', result['candidate_pool'])
    atomic_json(work/'recommendations.json', result)
    return model, result


def export(work, partial=False):
    from build_school_review import teacher_codes, grid, schedule_html, REVIEW_FIELDS, page
    from final_impact import csv_table
    model, selection = select_completed(work, partial)
    recommendations = []
    schedules = {'BASELINE': {eid: (e['reference']['day'],e['reference']['start_period']) for eid,e in model.events.items()}}
    for label, candidate in zip('ABC', selection['recommendations']):
        x = candidate['solution']; schedules[label] = x
        recommendations.append({'label': label, 'dataset_hash': model.dataset_hash,
            'rules_hash': model.rules_hash, 'timetable': model.timetable(x),
            'source': {'runs': candidate['source_runs'], 'canonical_id': candidate['canonical_id']}})
    output = work/'export'
    report = write_report(model, recommendations, output/'impact', {'scope': selection['scope']})
    codes = teacher_codes(model); checks = {}
    links = []
    for label,x in schedules.items():
        checks[label] = validate(model, model.timetable(x))
        for kind, ids, suffix in [('kelas', sorted({c for e in model.events.values() for c in e['class_ids']}), 'class'),
                                  ('guru', list(codes), 'teacher')]:
            grids = []; flat = []
            for resource in ids:
                g, rows = grid(model,x,kind,resource,codes); grids.append(g); flat.extend(rows)
            name = ('baseline' if label == 'BASELINE' else 'recommendation_'+label)+'_schedule_'+suffix
            atomic_json(output/(name+'.json'), flat)
            atomic_text(output/(name+'.csv'), csv_table(flat).replace('\r\n','\n'))
            # Same directory, so adapt only navigation, never timetable content.
            atomic_text(output/(name+'.html'), schedule_html(label,kind,grids).replace('../index.html','index.html'))
            links.append('<li><a href="'+name+'.html">'+label+' — '+kind+'</a></li>')
    atomic_json(output/'validation_report.json', checks)
    atomic_json(output/'goal_based_evaluation_support.json', {'scope': selection['scope'],
        'objective_evidence': report, 'reviewer_responses': [],
        'note': 'SA supplies objective evidence only; no human assessment is fabricated.'})
    atomic_text(output/'school-review-input.csv', ','.join(REVIEW_FIELDS)+'\n')
    atomic_text(output/'index.html', page('Peninjauan jadwal', '<h1>Peninjauan jadwal</h1><p>'+selection['scope']+
        ' — '+str(selection['selected_count'])+' rekomendasi feasible tersedia.</p><ul>'+''.join(links)+
        '</ul><p>Baseline tetap menampilkan pelanggaran sumber. Penilaian manusia belum diisi.</p>'))
    atomic_json(output/'export_manifest.json', {'scope': selection['scope'], 'dataset_hash': model.dataset_hash,
        'rules_hash': model.rules_hash, 'selection_hash': stable_hash(selection),
        'files': {p.relative_to(output).as_posix(): digest(p) for p in sorted(output.rglob('*')) if p.is_file()}})
    return {'scope': selection['scope'], 'recommendations': selection['selected_count'], 'output': str(output)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare','baseline','pilot','freeze','plan','main','select','export','dry-run'])
    parser.add_argument('--work', type=Path, default=DEFAULT_WORK)
    parser.add_argument('--protocol', type=Path, default=None,
        help='Optional protocol config path (relative to repo root or absolute) overriding '
             'config/school-experiment.user-approved.v1.json for this invocation only.')
    parser.add_argument('--pilot-dir', type=Path, default=DEFAULT_PILOT)
    parser.add_argument('--rebuild', action='store_true'); parser.add_argument('--partial', action='store_true')
    parser.add_argument('--max-runs', type=int); parser.add_argument('--run-id')
    parser.add_argument('--engine', type=str, default=None,
        help='Search engine module for a fresh main cohort (final_sa or final_sa_ts). '
             'Omitted once the cohort manifest exists: select/export continue its recorded engine.')
    args = parser.parse_args(argv); work = args.work.resolve(); work.mkdir(parents=True, exist_ok=True)
    global CONFIG
    if args.protocol is not None:
        CONFIG = args.protocol if args.protocol.is_absolute() else ROOT/args.protocol
    if args.command == 'prepare': result = {'counts': counts(prepare(work,args.rebuild).data)}
    elif args.command == 'baseline': result = baseline(prepare(work),work)
    elif args.command == 'pilot': result = run_pilot(read_json(CONFIG),args.pilot_dir)
    elif args.command == 'freeze': result = {'snapshot_hash': freeze(work,args.pilot_dir)['snapshot_hash']}
    elif args.command == 'plan':
        model,frozen = load_final(work); plan = planned_runs(frozen['protocol'],len(model.ids))
        atomic_json(work/'main_plan.json',plan); result = {'expected_runs':len(plan)}
    elif args.command == 'main': result = runner_for(work, args.engine).execute(rerun=args.run_id,max_runs=args.max_runs)
    elif args.command == 'select':
        _,s = select_completed(work,args.partial); result = {k:s[k] for k in ('scope','selected_count','completed_runs')}
    elif args.command == 'export': result = export(work,args.partial)
    else:
        baseline(prepare(work,True),work); freeze(work,args.pilot_dir)
        runner = runner_for(work); result = runner.execute(max_runs=1)
        if result['failed_runs']: raise ValueError('Dry-run main experiment failed')
        exported = export(work,True)
        result = {'status':'PASS','scope':'ONE_PRODUCTION_RUN_AND_EXISTING_FIVE_PILOTS_REVALIDATED',
            'main':result,'export':exported,'raw_dataset_hash':runner.model.dataset_hash}
        atomic_json(work/'dry_run_report.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if result.get('failed_runs',0) else 0


if __name__ == '__main__': raise SystemExit(main())
