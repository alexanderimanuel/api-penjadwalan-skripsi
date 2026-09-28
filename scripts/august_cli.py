"""Pintu masuk program Agustus: run, validate, dan export."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from export_august import validate_run, export_run, KINDS

def main():
    if len(sys.argv)>1 and sys.argv[1]=='final':
        return subprocess.call([sys.executable,str(Path(__file__).with_name('final_cli.py')),*sys.argv[2:]])
    # Forward SA arguments unchanged, including --help, to the tested runner.
    if len(sys.argv)>1 and sys.argv[1]=='run':
        return subprocess.call([sys.executable,str(Path(__file__).with_name('anneal_august.py')),*sys.argv[2:]])
    parser=argparse.ArgumentParser(description=__doc__,epilog='Metodologi revisi: python scripts/august_cli.py final --help. Perintah run/validate/export tanpa final adalah model historis Agustus v1.')
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ['validate','export']:
        p=commands.add_parser(name)
        p.add_argument('--run',required=True)
        p.add_argument('--solution',choices=KINDS,default='best')
        if name=='export':p.add_argument('--output',required=True)
    args=parser.parse_args()
    if args.command=='validate':result=validate_run(args.run,args.solution)[3]
    else:result=export_run(args.run,args.output,args.solution)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':sys.exit(main())
