"""Revision entry point in the existing workspace. No 180-run execution command."""
from pathlib import Path
import argparse
import hashlib
import json
import platform
import sys
from model_august import ROOT,SNAPSHOT
from export_august import read_json
from final_rules import FinalModel,readiness,stable_hash
from final_sa import configurations,run

def load(config):
    manifest=read_json(SNAPSHOT/'manifest.json')
    for f in manifest['files']:
        if hashlib.sha256((SNAPSHOT/f['path']).read_bytes()).hexdigest()!=f['sha256']:raise ValueError('Snapshot hash changed')
    return read_json(SNAPSHOT/manifest['dataset_path']),read_json(config)

def plan(data,rules):
    missing=readiness(data,rules)
    return {'status':'BLOCKED BY REAL DATA' if missing else 'READY_FOR_PILOT',
        'dataset_hash':stable_hash(data),'rules_hash':stable_hash(rules),'blockers':missing,
        'pilot_seeds':[1000,1001,1002,1003,1004],'main_seeds':list(range(30)),
        'configurations':configurations(len(data['events'])),'expected_main_runs':180,
        'main_execution_enabled':False,'note':'Plan only; old pilot seeds/results are not evidence for final methodology.'}

def main():
    if sys.argv[1:2]==['pilot']:
        from final_pilot import main as pilot_main
        return pilot_main(sys.argv[2:])
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['check','plan','baseline','pilot-run'])
    parser.add_argument('--config',default=str(ROOT/'config/final-school.v1.json'))
    parser.add_argument('--output');parser.add_argument('--seed',type=int,default=1000)
    parser.add_argument('--configuration',choices=[f'C{i}' for i in range(1,7)],default='C2')
    args=parser.parse_args();data,rules=load(args.config);manifest=plan(data,rules)
    if args.command in ('check','plan'):result=manifest
    else:
        if manifest['blockers']:
            print(json.dumps(manifest,ensure_ascii=False,indent=2));return 2
        model=FinalModel(data,rules)
        if args.command=='baseline':
            x={eid:(e['reference']['day'],e['reference']['start_period']) for eid,e in model.events.items()}
            result={'dataset_hash':model.dataset_hash,'rules_hash':model.rules_hash,'baseline':model.evaluate(x)}
        else:
            if args.seed not in manifest['pilot_seeds']:raise ValueError('Use one of the five disjoint pilot seeds')
            if not args.output:raise ValueError('pilot-run requires --output')
            if Path(args.output).exists():raise ValueError('Output already exists')
            cfg=next(c for c in manifest['configurations'] if c['id']==args.configuration)
            result=run(model,cfg,args.seed)
            result.update(purpose='FINAL_METHODOLOGY_PILOT',environment={'python':sys.version,'platform':platform.platform()},
                config_snapshot=rules,code_hashes={name:hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest()
                for name in ['final_foundation.py','final_rules.py','final_validator.py','final_sa.py','final_recommendations.py','model_august.py']})
    if args.output:
        out=Path(args.output)
        if out.exists():raise ValueError('Output already exists')
        out.parent.mkdir(parents=True,exist_ok=True)
        # Column-labelled search rows keep every decision without repeated keys or
        # full timetables. Compact JSON avoids expanding each scalar onto a line.
        indent=None if args.command=='pilot-run' else 2
        temp=out.with_suffix(out.suffix+'.tmp');temp.write_text(json.dumps(result,ensure_ascii=False,indent=indent,allow_nan=False)+'\n',encoding='utf-8');temp.replace(out)
    print(json.dumps(result if args.command in ('check','plan','baseline') else {k:result[k] for k in ['status','evaluations','stop_reason','best_feasible_quality']},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':sys.exit(main())
