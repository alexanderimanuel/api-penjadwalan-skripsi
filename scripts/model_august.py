"""Model kerja Agustus: domain dan evaluator referensi, belum algoritma SA."""
from pathlib import Path
from collections import Counter, defaultdict
from itertools import combinations
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]
SNAPSHOT=ROOT/'data/versions/2026-08-03-work-v1'
OUT=ROOT/'data/model/2026-08-03-model-v1'

def signature(day,start,jp):
    slots={s['period']:s for s in day['segments'] if s['period'] is not None}
    chosen=[slots.get(p) for p in range(start,start+jp)]
    if not chosen or any(s is None or s['kind']!='kbm' for s in chosen): return None
    return {'jp':jp,'kbm_minutes':sum(s['duration_minutes'] for s in chosen),
            'breaks':[{'after_jp':i,'minutes':b['start_minute']-a['end_minute']}
                for i,(a,b) in enumerate(zip(chosen,chosen[1:]),1) if b['start_minute']>a['end_minute']]}

def domain(event,calendar):
    target={k:event['time'][k] for k in ['jp','kbm_minutes','breaks']}
    return [{'day':day['id'],'start_period':s['period']}
            for day in calendar['days'] for s in day['segments'] if s['kind']=='kbm'
            and signature(day,s['period'],target['jp'])==target]

def bounds(events,calendar,teachers):
    # Internal empty slots cannot exceed K-2 for a teacher/day with K KBM slots.
    sc1=len(teachers)*sum(max(0,sum(s['kind']=='kbm' for s in d['segments'])-2) for d in calendar['days'])
    groups=Counter((c,e['subject_id']) for e in events for c in e['class_ids'])
    sc2=sum(n*(n-1)//2 for n in groups.values())
    return {'SC1_upper':sc1,'SC2_upper':sc2,'S_upper':sc1+sc2,'M':sc1+sc2+1}

def evaluate(events,calendar,domains,solution,penalty_M):
    """Search states must have exactly immutable event IDs and legal placements.

    Malformed payloads are rejected, not assigned an artificially small energy.
    HC3/HC6 hold by representation; HC4/HC5 by precomputed domain.
    """
    known={e['id'] for e in events}
    if len(known)!=len(events) or set(solution)!=known: raise ValueError('Solusi harus memuat setiap ID event tepat satu kali')
    if any(p not in domains[eid] for eid,p in solution.items()): raise ValueError('Penempatan di luar domain HC4/HC5')
    occupied={e['id']:set(range(solution[e['id']]['start_period'],solution[e['id']]['start_period']+e['time']['jp'])) for e in events}
    hc1=[];hc2=[]
    for a,b in combinations(events,2):
        if solution[a['id']]['day']!=solution[b['id']]['day'] or not occupied[a['id']]&occupied[b['id']]: continue
        for field,target in [('teacher_codes',hc1),('class_ids',hc2)]:
            shared=sorted(set(a[field])&set(b[field]))
            if shared: target.append({'event_ids':[a['id'],b['id']],'shared_ids':shared})
    teacher_days=defaultdict(set)
    for e in events:
        for teacher in e['teacher_codes']:
            teacher_days[teacher,solution[e['id']]['day']].update(occupied[e['id']])
    gap_details=[]
    for (teacher,day),busy in sorted(teacher_days.items()):
        available=[s['period'] for d in calendar['days'] if d['id']==day for s in d['segments'] if s['kind']=='kbm']
        gaps=[p for p in available if min(busy)<p<max(busy) and p not in busy]
        if gaps: gap_details.append({'teacher_code':teacher,'day':day,'empty_kbm_periods':gaps})
    sc1=sum(len(r['empty_kbm_periods']) for r in gap_details)
    group_days=defaultdict(list)
    for e in events:
        for c in e['class_ids']:group_days[c,e['subject_id'],solution[e['id']]['day']].append(e['id'])
    spread=[{'class_id':c,'subject_id':s,'day':d,'event_ids':ids,'pairs':len(ids)*(len(ids)-1)//2}
            for (c,s,d),ids in sorted(group_days.items()) if len(ids)>1]
    sc2=sum(g['pairs'] for g in spread)
    H=len(hc1)+len(hc2);S=sc1+sc2
    return {'HC1':len(hc1),'HC2':len(hc2),'HC3':0,'HC4':0,'HC5':0,'HC6':0,
            'H':H,'SC1':sc1,'SC2':sc2,'S':S,'M':penalty_M,'F':penalty_M*H+S,'feasible':H==0,
            'details':{'HC1_pairs':hc1,'HC2_pairs':hc2,'SC1_gaps':gap_details,'SC2_groups':spread},
            'scope':'Complete immutable events, domain-valid placements, model-v1 assumptions; not school approval.'}

def main():
    manifest=json.loads((SNAPSHOT/'manifest.json').read_text(encoding='utf-8'))
    for f in manifest['files']:
        if hashlib.sha256((SNAPSHOT/f['path']).read_bytes()).hexdigest()!=f['sha256']:raise ValueError('Snapshot hash mismatch')
    data=json.loads((SNAPSHOT/manifest['dataset_path']).read_text(encoding='utf-8'))
    events=data['events'];calendar=data['calendar']
    domains={e['id']:domain(e,calendar) for e in events}
    reference={e['id']:{k:e['reference'][k] for k in ['day','start_period']} for e in events}
    assert all(domains.values()) and all(reference[eid] in options for eid,options in domains.items())
    b=bounds(events,calendar,data['teachers'])
    score=evaluate(events,calendar,domains,reference,b['M'])
    assert score['S']<=b['S_upper']
    statistics={'event_count':len(events),'empty_domains':sum(not v for v in domains.values()),
        'min_domain_size':min(map(len,domains.values())),'max_domain_size':max(map(len,domains.values())),
        'domain_size_histogram':dict(sorted(Counter(len(v) for v in domains.values()).items())),
        'single_placement_events':[eid for eid,v in domains.items() if len(v)==1],
        'single_day_event_count':sum(len({p['day'] for p in v})==1 for v in domains.values())}
    config={'id':'2026-08-03-model-v1','dataset_version':manifest['version'],
        'snapshot_manifest_sha256':hashlib.sha256((SNAPSHOT/'manifest.json').read_bytes()).hexdigest(),
        'model_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'weights':{'SC1':1,'SC2':1},'bounds':b,
        'assumptions':{'grouped_teachers':'All recorded teachers attend one synchronized class event; experimental assumption, unconfirmed.',
            'break_equivalence':'Exact list of (position after JP, break minutes), plus total JP and KBM minutes.',
            'period_duration_sequence':'Recorded for audit but not an additional domain-equality condition.',
            'resource_conflict_unit':'One unordered event pair per HC category, regardless of overlapping periods or shared resource count.',
            'reference_conflict':'Source retained; feasible initializer or repair belongs to SA stage.'},
        'statistics':statistics}
    OUT.mkdir(parents=True,exist_ok=True)
    for name,value in [('model.json',config),('domains.json',domains),('reference-solution.json',reference),('reference-score.json',score)]:
        target=OUT/name;target.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        assert json.loads(target.read_text(encoding='utf-8'))==json.loads(json.dumps(value))
    print(json.dumps({'statistics':statistics,'bounds':b,'reference_score':{k:v for k,v in score.items() if k not in ['details','scope']}},indent=2))

if __name__=='__main__': main()
