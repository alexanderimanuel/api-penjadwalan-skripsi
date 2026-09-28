"""Versioned feasible-neighborhood SA with a shared repaired-baseline warm start.

Distinct from frozen constructive SA experiments. Keeps their scoring unchanged.
"""
import argparse
from collections import Counter, defaultdict
import copy
import hashlib
import html
import json
import math
from pathlib import Path
import random
import time

from export_august import read_json
from final_experiment import atomic_json, atomic_text, load_frozen
from final_foundation import thaw
from final_impact import csv_table, write_report
from final_rules import stable_hash
from final_sa import metropolis, score_equal
from final_validator import validate
from model_august import ROOT

CONFIG=ROOT/'config/sa-feasible-recommendations.v1.json'
PILOT=ROOT/'results/final-pilot/school-user-approved-v1'
REPAIR=ROOT/'results/final-diagnostics/school-baseline-repair-v1/repair-candidate.json'


class FeasibleNeighborhood:
    def __init__(self,model):
        self.model=model
        self.resources={eid:[('teacher',t) for t in e['teacher_codes']]+[('class',c) for c in e['class_ids']]
                        for eid,e in model.events.items()}
        self.jp={eid:e['time']['jp'] for eid,e in model.events.items()}
        self.friday_limits=dict(model.rules.get('friday_jp_limits') or {})
        self.kbm={d:sorted(periods) for d,periods in model.active.items()}
        self.slots={(eid,tuple(pos)):tuple((kind,resource,pos[0],p)
                     for kind,resource in self.resources[eid] for p in range(pos[1],pos[1]+self.jp[eid]))
                    for eid in model.ids for pos in model.domains[eid]}
        self.other_positions={(eid,tuple(p)):[tuple(q) for q in model.domains[eid] if tuple(q)!=tuple(p)]
                              for eid in model.ids for p in model.domains[eid]}

    def occupancy(self,x):
        occupied={}
        for eid,pos in x.items():
            for slot in self.slots[eid,tuple(pos)]:
                if slot in occupied:raise ValueError('Initial state is not resource feasible')
                occupied[slot]=eid
        return occupied

    def propose(self,x,occupied,rng,attempt_limit=50,swap_probability=.5):
        rejected=Counter();attempted=Counter()
        for attempt in range(1,attempt_limit+1):
            op='swap' if rng.random()<swap_probability else 'move';attempted[op]+=1
            if op=='swap':
                if not self.model.swap_pairs:rejected['no_swap_pairs']+=1;continue
                a,b=rng.choice(self.model.swap_pairs)
                if x[a]==x[b] or x[b] not in self.model.allowed[a] or x[a] not in self.model.allowed[b]:
                    rejected['structural_incompatible']+=1;continue
                changes={a:x[b],b:x[a]}
            else:
                eid=rng.choice(self.model.ids);options=self.other_positions[eid,x[eid]]
                if not options:rejected['singleton_domain']+=1;continue
                changes={eid:rng.choice(options)}
            new_slots={};conflict=False
            for eid,pos in changes.items():
                for slot in self.slots[eid,pos]:
                    if (slot in occupied and occupied[slot] not in changes) or slot in new_slots:
                        conflict=True;break
                    new_slots[slot]=eid
                if conflict:break
            if conflict:rejected['hard_conflict_screen']+=1;continue
            if self.friday_limits:
                violated=False
                for ceid,pos in changes.items():
                    if pos[0]!='jumat':continue
                    last=pos[1]+self.jp[ceid]-1
                    for c in self.model.events[ceid]['class_ids']:
                        if last>self.friday_limits.get(c.split('-')[0],10**9):
                            violated=True;break
                    if violated:break
                if violated:rejected['friday_jp_screen']+=1;continue
            y={**x,**changes}
            pairs=set()
            for ceid,pos in changes.items():
                for c in self.model.events[ceid]['class_ids']:
                    pairs.add((c,x[ceid][0]));pairs.add((c,pos[0]))
            occ={}
            for ceid,(cday,s) in y.items():
                for c in self.model.events[ceid]['class_ids']:
                    if (c,cday) in pairs:
                        occ.setdefault((c,cday),set()).update(range(s,s+self.jp[ceid]))
            hole=False
            for (c,d),ps in occ.items():
                if ps and any((p<max(ps) if self.model.rules.get('class_start_first_kbm',False) else min(ps)<p<max(ps)) and p not in ps for p in self.kbm[d]):
                    hole=True;break
            if hole:rejected['class_hole_screen']+=1;continue
            return y,{'operator':op,'attempts':attempt,'attempted':dict(attempted),'rejections':dict(rejected),'status':'feasible_change','changes':changes}
        return x.copy(),{'operator':'no_change','attempts':attempt_limit,'attempted':dict(attempted),'rejections':dict(rejected),'status':'attempt_limit','changes':{}}

    def apply(self,occupied,old,changes):
        for eid in changes:
            for slot in self.slots[eid,old[eid]]:del occupied[slot]
        for eid,pos in changes.items():
            for slot in self.slots[eid,pos]:occupied[slot]=eid


def groups(model):
    result=defaultdict(list)
    for eid,e in model.events.items():
        signature=stable_hash([e['subject_id'],sorted(e['class_ids']),sorted(e['teacher_codes']),e['time']['jp'],e['time']['kbm_minutes'],e['time']['breaks']])
        result[signature].append(eid)
    return dict(sorted(result.items()))


def canonical(grouped,x):
    return stable_hash([[signature,sorted([list(x[e]) for e in ids])] for signature,ids in grouped.items()])


def distance(grouped,a,b):
    return sum(len(ids)-sum((Counter(tuple(a[e]) for e in ids)&Counter(tuple(b[e]) for e in ids)).values()) for ids in grouped.values())


def search(model,initial,config,seed):
    started=time.perf_counter();x={eid:tuple(p) for eid,p in initial.items()};initial=copy.deepcopy(x)
    initial_score=validate(model,model.timetable(initial))
    if not initial_score['feasible']:raise ValueError('Warm start must be independently feasible')
    neighborhood=FeasibleNeighborhood(model);occupied=neighborhood.occupancy(x)
    calibration_rng=random.Random(int(stable_hash([config['method_version'],seed,'calibration'])[:16],16))
    rng=random.Random(int(stable_hash([config['method_version'],seed,'search'])[:16],16))
    calibration=[];positive=[];count=1
    for index in range(config['calibration_samples']):
        y,meta=neighborhood.propose(initial,occupied,calibration_rng,config['max_candidate_attempts'],config['swap_probability'])
        q=model.score(y);count+=1
        if not q['feasible']:raise ValueError('Feasibility screening bug during calibration')
        delta=q['F']-initial_score['F']
        calibration.append({'sample':index+1,'delta':delta,**meta})
        if delta>0:positive.append(delta)
    T0=-(sum(positive)/len(positive))/math.log(config['p0']) if positive else 1.0
    T=T0;L=config['L_multiplier']*len(model.ids)
    score=initial_score;best=x.copy();best_score=copy.deepcopy(score);stale=0;step=0
    logs=[];stats=Counter();proposal_stats=Counter();anomalies=[]
    while True:
        if count+1>=config['max_evaluations']:stop='TOTAL_EVALUATION_BUDGET';break
        if T<config['Tmin']:stop='TMIN';break
        if stale>=config['stagnation_limit']:stop='STAGNATION';break
        y,meta=neighborhood.propose(x,occupied,rng,config['max_candidate_attempts'],config['swap_probability'])
        trial=model.score(y) if meta['changes'] else copy.deepcopy(score)
        count+=1;step+=1;stale+=1
        if not trial['feasible']:raise ValueError('Feasibility screening bug during search')
        delta=trial['F']-score['F'];accepted,prob,draw=metropolis(delta,T,rng)
        stats[meta['operator']+'_evaluations']+=1
        proposal_stats.update(meta['rejections'])
        for op,value in meta['attempted'].items():stats[op+'_attempts']+=value
        if accepted:
            neighborhood.apply(occupied,x,meta['changes']);x=y;score=trial
            stats[meta['operator']+'_accepted']+=1
            if delta>0:stats['accepted_worse']+=1
        if score['F']<best_score['F']:
            best=x.copy();best_score=copy.deepcopy(score);stale=0;stats['best_improvements']+=1
        logs.append([count,step,T,meta['operator'],delta,accepted,prob,draw,score['F'],score['H'],score['S'],
                     best_score['F'],best_score['H'],best_score['S'],meta['status'],meta['attempts'],
                     [[e,*p] for e,p in meta['changes'].items()] if accepted else []])
        if step%L==0:T*=config['alpha']
    checked=validate(model,model.timetable(best));count+=1
    if not checked['feasible'] or not score_equal(checked,best_score):raise ValueError('Independent best validation mismatch')
    return {'purpose':config['purpose'],'method_version':config['method_version'],'seed':seed,
            'configuration':{**config,'L':L},'dataset_hash':model.dataset_hash,'rules_hash':model.rules_hash,
            'dataset_version':model.data.get('id'),'rules_version':model.rules['rules_version'],
            'initial':initial,'initial_hash':stable_hash(initial),'initial_quality':initial_score,
            'best_feasible':best,'best_feasible_quality':checked,'final_current':x,'final_current_quality':score,
            'T0':T0,'final_temperature':T,'calibration':calibration,'positive_calibration_deltas':len(positive),
            'evaluations':count,'search_evaluations':step,'stop_reason':stop,'statistics':dict(stats),
            'screening_rejections':dict(proposal_stats),'runtime_seconds':time.perf_counter()-started,
            'budget_semantics':'Initial validation + 200 calibration proposals + search proposals (including no-change) + final best validation; screening uses resource occupancy, not soft evaluations.',
            'search_log':{'columns':['evaluation','search_evaluation','temperature','operator','delta','accepted','probability','draw','current_F','current_H','current_S','best_F','best_H','best_S','status','attempts','accepted_changes'],'rows':logs}}


def select(model,runs,initial,config,initials=None):
    grouped=groups(model);excluded={canonical(grouped,initial)}
    for other in initials or []:excluded.add(canonical(grouped,other))
    unique={}
    for run in runs:
        if run['dataset_hash']!=model.dataset_hash or run['rules_hash']!=model.rules_hash:raise ValueError('Run provenance mismatch')
        x=run['best_feasible'];cid=canonical(grouped,x)
        if cid in excluded:continue
        checked=validate(model,model.timetable(x))
        if not checked['feasible'] or not score_equal(checked,run['best_feasible_quality']):raise ValueError('Invalid SA recommendation')
        if cid not in unique:unique[cid]={'canonical_id':cid,'solution':x,'score':checked,'seed':run['seed']}
    ranked=sorted(unique.values(),key=lambda r:(r['score']['S'],r['score']['raw']['SC3'],r['score']['raw']['SC1'],r['canonical_id']))
    chosen=[];threshold=math.ceil(config['selection']['minimum_distance_fraction']*len(model.ids))
    for r in ranked:
        distances=[distance(grouped,r['solution'],c['solution']) for c in chosen]
        if all(d>=threshold for d in distances):chosen.append({**r,'distances_to_previous':distances})
        if len(chosen)==config['selection']['count']:break
    return {'recommendations':chosen,'minimum_distance':threshold,'unique_noninitial_sa_best':len(unique)}


def readable(model,x):
    rows=[]
    for eid in model.ids:
        e=model.events[eid];day,start=x[eid];end=start+e['time']['jp']-1
        slots={s['period']:s for s in model.days[day]['segments'] if s['period'] is not None}
        clock=lambda n:f'{n//60:02d}:{n%60:02d}'
        rows.append({'event_id':eid,'class_ids':','.join(e['class_ids']),'subject_id':e['subject_id'],
                     'teacher_ids':','.join(e['teacher_codes']),'day':day,'start_period':start,'end_period':end,
                     'start_time':clock(slots[start]['start_minute']),'end_time':clock(slots[end]['end_minute']),
                     'jp':e['time']['jp'],'kbm_minutes':e['time']['kbm_minutes']})
    return rows


def html_schedule(label,rows,model):
    classes=sorted({c for e in model.events.values() for c in e['class_ids']});days=list(model.days)
    parts=[f'<!doctype html><html lang="id"><meta charset="utf-8"><title>Jadwal SA {label}</title>',
           '<style>body{font:14px Arial;margin:28px;color:#123}table{border-collapse:collapse;width:100%;margin-bottom:24px}th,td{border:1px solid #bbc;padding:7px;text-align:center;font-size:11px}th{background:#edf3fa}h2{page-break-before:auto}@media print{section{page-break-inside:avoid}}small{color:#456}</style>',
           f'<h1>Rekomendasi SA {label}</h1><p>Data sekolah 3 Agustus 2026 · model penelitian dengan asumsi yang disetujui · seluruh HC=0</p><p>Angka dalam kurung adalah kode guru. Sel kosong berarti tidak ada event pelajaran; kegiatan tetap/istirahat mengikuti kalender sumber.</p>']
    for c in classes:
        parts.append('<section><h2>'+html.escape(c)+'</h2><table><tr><th>Hari / JP</th>'+''.join(f'<th>{p}</th>' for p in range(1,11))+'</tr>')
        for d in days:
            parts.append('<tr><th>'+html.escape(d.title())+'</th>')
            segments={s['period']:s for s in model.days[d]['segments'] if s['period'] is not None}
            for p in range(1,11):
                found=[r for r in rows if c in r['class_ids'].split(',') and r['day']==d and r['start_period']<=p<=r['end_period']]
                text='<br>'.join(html.escape(r['subject_id']+' ('+r['teacher_ids']+')') for r in found)
                if not text and p in segments and segments[p]['kind']=='fixed':text='Kegiatan tetap'
                if p not in segments:text='—'
                parts.append('<td>'+text+'</td>')
            parts.append('</tr>')
        parts.append('</table></section>')
    return ''.join(parts)+'</html>'


def teacher_readable(rows):
    """Expand each event once per assigned teacher for teacher-facing exports."""
    expanded=[]
    for row in rows:
        for teacher_id in row['teacher_ids'].split(','):
            expanded.append({
                'teacher_id':teacher_id,
                'day':row['day'],
                'start_period':row['start_period'],
                'end_period':row['end_period'],
                'start_time':row['start_time'],
                'end_time':row['end_time'],
                'class_ids':row['class_ids'],
                'subject_id':row['subject_id'],
                'event_id':row['event_id'],
                'jp':row['jp'],
                'kbm_minutes':row['kbm_minutes'],
            })
    day_order={day:index for index,day in enumerate(model_day_ids(rows))}
    return sorted(expanded,key=lambda r:(r['teacher_id'],day_order.get(r['day'],99),r['start_period'],r['event_id']))


def model_day_ids(rows):
    preferred=['senin','selasa','rabu','kamis','jumat']
    present={r['day'] for r in rows}
    return [d for d in preferred if d in present]+sorted(present-set(preferred))


def html_teacher_schedule(label,rows,model):
    teachers=sorted({teacher for row in rows for teacher in row['teacher_ids'].split(',')})
    days=list(model.days)
    parts=[f'<!doctype html><html lang="id"><meta charset="utf-8"><title>Jadwal Guru SA {label}</title>',
           '<style>body{font:14px Arial;margin:28px;color:#123}table{border-collapse:collapse;width:100%;margin-bottom:24px}th,td{border:1px solid #bbc;padding:7px;text-align:center;font-size:11px}th{background:#edf3fa}h2{page-break-before:auto}@media print{section{page-break-inside:avoid}}</style>',
           f'<h1>Jadwal Guru — Rekomendasi SA {label}</h1><p>Data sekolah 3 Agustus 2026 · telah divalidasi dengan HC1–HC8 = 0.</p><p>Isi sel: mata pelajaran — kelas. Kode guru dianonimkan sesuai data penelitian.</p>']
    for teacher in teachers:
        parts.append('<section><h2>Guru '+html.escape(teacher)+'</h2><table><tr><th>Hari / JP</th>'+''.join(f'<th>{p}</th>' for p in range(1,11))+'</tr>')
        for day in days:
            parts.append('<tr><th>'+html.escape(day.title())+'</th>')
            segments={s['period']:s for s in model.days[day]['segments'] if s['period'] is not None}
            for period in range(1,11):
                found=[r for r in rows if teacher in r['teacher_ids'].split(',') and r['day']==day and r['start_period']<=period<=r['end_period']]
                text='<br>'.join(html.escape(r['subject_id']+' — '+r['class_ids']) for r in found)
                if not text and period in segments and segments[period]['kind']=='fixed':text='Kegiatan tetap'
                if period not in segments:text='—'
                parts.append('<td>'+text+'</td>')
            parts.append('</tr>')
        parts.append('</table></section>')
    return ''.join(parts)+'</html>'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True);parser.add_argument('--config',default=str(CONFIG))
    args=parser.parse_args(argv);output=Path(args.output);config=read_json(args.config)
    model,frozen,_,_=load_frozen(PILOT/'FROZEN_EXPERIMENT_CONFIG.json');repair=read_json(REPAIR)
    if repair['dataset_hash']!=model.dataset_hash or repair['rules_hash']!=model.rules_hash:raise ValueError('Warm start provenance differs')
    initial=repair['placement'];manifest={'config':config,'dataset_hash':model.dataset_hash,'rules_hash':model.rules_hash,
        'initial_hash':stable_hash(initial),'source_frozen_hash':frozen['snapshot_hash'],
        'code_hash':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'revision':'Feasible-only SA neighborhood and repaired baseline warm start; previous frozen experiment unchanged.'}
    if output.exists():
        if read_json(output/'method-snapshot.json')!=manifest:raise ValueError('Cannot mix versions in resumed output')
    else:
        output.mkdir(parents=True);atomic_json(output/'method-snapshot.json',manifest)
        atomic_json(output/'initial-placement.json',initial)
    runs=[]
    for seed in config['seeds']:
        folder=output/f'seed-{seed}';path=folder/'result.json'
        if path.exists():
            result=read_json(path)
            if result['initial_hash']!=manifest['initial_hash']:raise ValueError('Initial changed')
            if not score_equal(validate(model,model.timetable(result['best_feasible'])),result['best_feasible_quality']):raise ValueError('Persisted run invalid')
            print(f'Seed {seed}: validated existing run',flush=True)
        else:
            print(f'Seed {seed}: feasible SA start',flush=True)
            result=search(model,initial,config,seed)
            atomic_json(path,result);atomic_json(folder/'best-feasible-timetable.json',model.timetable(result['best_feasible']))
            cols=result['search_log']['columns'];curve=[{k:r[cols.index(k)] for k in ('evaluation','search_evaluation','temperature','best_F','best_H','best_S')} for r in result['search_log']['rows']]
            atomic_text(folder/'convergence.csv',csv_table(curve))
            print(f"Seed {seed}: H={result['best_feasible_quality']['H']} S={result['best_feasible_quality']['S']:.8f}, search={result['search_evaluations']}, {result['stop_reason']}, {result['runtime_seconds']:.1f}s",flush=True)
        runs.append(result);selection=select(model,runs,initial,config)
        atomic_json(output/'selection.json',selection)
        atomic_json(output/'progress.json',{'completed_seeds':[r['seed'] for r in runs], 'selected':len(selection['recommendations']),'unique_best':selection['unique_noninitial_sa_best']})
        if len(selection['recommendations'])==config['selection']['count']:break
    comparisons=[]
    for label,selected in zip('ABC',selection['recommendations']):
        rows=model.timetable(selected['solution']);checked=validate(model,rows)
        if not checked['feasible']:raise ValueError('Export validation failed')
        atomic_json(output/f'recommendation-{label}.json',{'label':label,'method':config['method_version'],'seed':selected['seed'],
                    'dataset_hash':model.dataset_hash,'rules_hash':model.rules_hash,'timetable':rows,'validation':checked})
        flat=readable(model,selected['solution']);atomic_text(output/f'jadwal-{label}.csv',csv_table(flat))
        atomic_text(output/f'jadwal-{label}.html',html_schedule(label,flat,model))
        comparisons.append({'label':label,'dataset_hash':model.dataset_hash,'rules_hash':model.rules_hash,'timetable':rows,
                             'source':{'method':config['method_version'],'seed':selected['seed'],'not_main_180_runs':True}})
    if not (output/'impact').exists():write_report(model,comparisons,output/'impact',{'method':config['method_version'],'completed_sa_runs':len(runs),'not_main_180_runs':True})
    print(f"Completed {len(runs)} SA runs; selected {len(comparisons)} independently feasible recommendations.",flush=True)


if __name__=='__main__':main()
