"""Rebuild the reconciled dataset from source PDFs in an isolated work directory."""
import contextlib
import importlib
import shutil
from pathlib import Path

from export_august import read_json
from final_rules import stable_hash
from model_august import ROOT


def reproduce(workdir,expected):
    workdir=Path(workdir).resolve()
    if workdir.exists():raise ValueError('Use a new isolated raw-reproduction directory')
    register=Path('data/inventory/2026-08-03/register.json')
    inventory=read_json(ROOT/register)
    sources=[register,*[Path(s['frozen_path']) for s in inventory['files']]]
    for relative in sources:
        target=workdir/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/relative,target)
    stages=[('extract_august_entities','entities'),('build_august_calendar','calendar'),
            ('extract_august_blocks','blocks'),('reconcile_august','reconciliation')]
    with (workdir/'reproduction.log').open('w',encoding='utf-8') as log:
        for name,sub in stages:
            module=importlib.import_module(name);original_root,original_out=module.ROOT,module.OUT
            try:
                module.ROOT=workdir;module.OUT=workdir/f'data/{sub}/2026-08-03'
                with contextlib.redirect_stdout(log):module.main()
            finally:module.ROOT,module.OUT=original_root,original_out
    data=read_json(workdir/'data/reconciliation/2026-08-03/candidate-dataset.json')
    if stable_hash(data)!=expected:raise ValueError('Rebuilt PDF dataset differs from frozen source; do not silently publish')
    return data
