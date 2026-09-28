"""Simulated Annealing untuk model Agustus v1; sumber dan snapshot tidak diubah."""
from pathlib import Path
from collections import defaultdict, Counter
from itertools import combinations
import argparse
import hashlib
import json
import math
import platform
import random
import time
from model_august import ROOT, SNAPSHOT, OUT as MODEL_DIR, evaluate, domain, bounds

SCORE_KEYS=['HC1','HC2','H','SC1','SC2','S','F']

class Problem:
    def __init__(self, events, calendar, domains, initial, M):
        self.events=events;self.calendar=calendar;self.domains=domains;self.M=M
        self.ids=[e['id'] for e in events]
        self.initial=[(initial[eid]['day'],initial[eid]['start_period']) for eid in self.ids]
        self.options=[[(p['day'],p['start_period']) for p in domains[eid]] for eid in self.ids]
        self.allowed=[set(opts) for opts in self.options]
        self.movable=[i for i,opts in enumerate(self.options) if len(opts)>1]
        self.periods={d['id']:set(s['period'] for s in d['segments'] if s['kind']=='kbm') for d in calendar['days']}
        # Static pair selection makes the proposal reversible/symmetric.
        self.swap_pairs=[(i,j) for i,j in combinations(self.movable,2)
            if events[i]['time']['jp']==events[j]['time']['jp']
            and set(events[i]['class_ids'])&set(events[j]['class_ids'])
            and len(self.allowed[i]&self.allowed[j])>=2]

    def solution(self,state):
        return {eid:{'day':day,'start_period':start} for eid,(day,start) in zip(self.ids,state)}

    def score(self,state,details=False):
        if len(state)!=len(self.events) or any(pos not in self.allowed[i] for i,pos in enumerate(state)):
            raise ValueError('State incomplete or outside model domains')
        teacher_slots=defaultdict(list);class_slots=defaultdict(list)
        teacher_days=defaultdict(set);spread=Counter()
        for i,(e,(day,start)) in enumerate(zip(self.events,state)):
            periods=range(start,start+e['time']['jp'])
            for t in e['teacher_codes']:
                teacher_days[t,day].update(periods)
                for p in periods:teacher_slots[t,day,p].append(i)
            for c in e['class_ids']:
                spread[c,e['subject_id'],day]+=1
                for p in periods:class_slots[c,day,p].append(i)
        # Deduplicate per event pair, not per period or per shared teacher.
        hp=[set(pair for members in slots.values() if len(members)>1 for pair in combinations(members,2))
            for slots in [teacher_slots,class_slots]]
        sc1=sum(len({p for p in self.periods[day] if min(busy)<p<max(busy)}-busy)
                for (_,day),busy in teacher_days.items())
        sc2=sum(n*(n-1)//2 for n in spread.values())
        H=sum(map(len,hp));S=sc1+sc2
        result={'HC1':len(hp[0]),'HC2':len(hp[1]),'H':H,'SC1':sc1,'SC2':sc2,'S':S,'F':self.M*H+S}
        if details:result['conflict_indices']=sorted({i for pairs in hp for pair in pairs for i in pair})
        return result

    def propose(self,state,rng,swap_probability):
        if rng.random()<swap_probability:
            if not self.swap_pairs:return None,'swap'
            i,j=rng.choice(self.swap_pairs)
            if state[i]==state[j] or state[j] not in self.allowed[i] or state[i] not in self.allowed[j]:return None,'swap'
            candidate=state.copy();candidate[i],candidate[j]=state[j],state[i]
            return candidate,'swap'
        if not self.movable:return None,'move'
        i=rng.choice(self.movable);position=rng.choice(self.options[i])
        if position==state[i]:return None,'move'
        candidate=state.copy();candidate[i]=position
        return candidate,'move'

def metropolis(delta,T,rng):
    if delta<=0:return True
    if T<=0 or not math.isfinite(T):raise ValueError('Temperature must be finite and positive')
    return rng.random()<math.exp(-delta/T)

def repair(problem,state,max_rounds):
    """Optional deterministic descent before SA; evaluated proposals counted separately."""
    state=state.copy();score=problem.score(state);evaluations=1;history=[]
    for iteration in range(max_rounds):
        if score['H']==0:break
        indices=problem.score(state,True)['conflict_indices'];evaluations+=1
        chosen=None;best_score=score
        for i in indices:
            for position in problem.options[i]:
                if position==state[i]:continue
                candidate=state.copy();candidate[i]=position
                trial=problem.score(candidate);evaluations+=1
                if trial['F']<best_score['F']:chosen=candidate;best_score=trial
        for i,j in problem.swap_pairs:
            if i not in indices and j not in indices:continue
            if state[i]==state[j] or state[j] not in problem.allowed[i] or state[i] not in problem.allowed[j]:continue
            candidate=state.copy();candidate[i],candidate[j]=state[j],state[i]
            trial=problem.score(candidate);evaluations+=1
            if trial['F']<best_score['F']:chosen=candidate;best_score=trial
        if chosen is None:break
        state=chosen;score=best_score;history.append({'round':iteration+1,**score})
    return state,{'evaluations':evaluations,'improvements':history,'score':score,'feasible':score['H']==0}

def anneal(problem,config):
    if config['iterations']<1 or config['chain_length']<1 or config['repair_rounds']<0:raise ValueError('Invalid iteration counts')
    if not 0<config['alpha']<1 or not 0<=config['swap_probability']<=1:raise ValueError('Invalid alpha/probability')
    if not all(math.isfinite(config[k]) and config[k]>0 for k in ['t0','tmin']) or config['t0']<config['tmin']:raise ValueError('Invalid temperatures')
    rng=random.Random(config['seed']);started=time.perf_counter()
    state,initialization=repair(problem,problem.initial,config['repair_rounds'])
    current=problem.score(state);best=current.copy();best_state=state.copy()
    best_feasible_state=state.copy() if current['H']==0 else None
    best_feasible_score=current.copy() if current['H']==0 else None
    initial_score=current.copy();initial_state=state.copy();T=config['t0'];counts=Counter();trace=[];stop='iteration_budget'
    first_feasible=0 if current['H']==0 else None;last_iteration=0
    for step in range(1,config['iterations']+1):
        if T<config['tmin']:stop='temperature_minimum';break
        last_iteration=step
        candidate,operator=problem.propose(state,rng,config['swap_probability'])
        counts['attempted_'+operator]+=1
        improved=False
        if candidate is None:counts['null_proposals']+=1
        else:
            trial=problem.score(candidate);counts['sa_evaluations']+=1
            delta=trial['F']-current['F']
            if metropolis(delta,T,rng):
                counts['accepted_'+operator]+=1
                if delta>0:counts['accepted_uphill']+=1
                state=candidate;current=trial
                if current['F']<best['F']:
                    best=current.copy();best_state=state.copy();improved=True
                if current['H']==0:
                    if first_feasible is None:first_feasible=step
                    if best_feasible_score is None or current['S']<best_feasible_score['S']:
                        best_feasible_state=state.copy();best_feasible_score=current.copy()
        if improved or step%config['chain_length']==0 or step==config['iterations']:
            trace.append({'iteration':step,'temperature':T,'current':current.copy(),'best':best.copy()})
        if best['H']==0 and best['S']==0:stop='zero_lower_bound';break
        if step%config['chain_length']==0:T*=config['alpha']
    return {'best_state':best_state,'best_score':best,'best_feasible_state':best_feasible_state,
        'best_feasible_score':best_feasible_score,'final_state':state,'final_score':current,
        'initialization':initialization,'sa_initial_score':initial_score,'sa_initial_state':initial_state,'first_feasible_iteration':first_feasible,
        'iterations':last_iteration,'stop_reason':stop,'counters':dict(counts),'trace':trace,
        'search_seconds':time.perf_counter()-started}

def load_problem():
    hashes={}
    manifest_path=SNAPSHOT/'manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    for f in manifest['files']:
        if hashlib.sha256((SNAPSHOT/f['path']).read_bytes()).hexdigest()!=f['sha256']:raise ValueError('Snapshot modified')
    paths=[manifest_path,SNAPSHOT/manifest['dataset_path'],MODEL_DIR/'model.json',MODEL_DIR/'domains.json',MODEL_DIR/'reference-solution.json',Path(__file__),ROOT/'scripts/model_august.py']
    for p in paths:hashes[p.relative_to(ROOT).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    model=json.loads((MODEL_DIR/'model.json').read_text(encoding='utf-8'))
    if model['snapshot_manifest_sha256']!=hashes[manifest_path.relative_to(ROOT).as_posix()]:raise ValueError('Wrong dataset version')
    if model['model_script_sha256']!=hashes['scripts/model_august.py']:raise ValueError('Model code changed')
    data=json.loads((SNAPSHOT/manifest['dataset_path']).read_text(encoding='utf-8'))
    domains=json.loads((MODEL_DIR/'domains.json').read_text(encoding='utf-8'))
    reference=json.loads((MODEL_DIR/'reference-solution.json').read_text(encoding='utf-8'))
    if domains!={e['id']:domain(e,data['calendar']) for e in data['events']}:raise ValueError('Domain file differs from model')
    if reference!={e['id']:{k:e['reference'][k] for k in ['day','start_period']} for e in data['events']}:raise ValueError('Reference changed')
    if model['bounds']!=bounds(data['events'],data['calendar'],data['teachers']):raise ValueError('Penalty bounds changed')
    return Problem(data['events'],data['calendar'],domains,reference,model['bounds']['M']),hashes

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed',type=int,default=0)
    parser.add_argument('--iterations',type=int,default=10000)
    parser.add_argument('--t0',type=float,default=50)
    parser.add_argument('--tmin',type=float,default=.1)
    parser.add_argument('--alpha',type=float,default=.95)
    parser.add_argument('--chain-length',type=int,default=513)
    parser.add_argument('--swap-probability',type=float,default=.7)
    parser.add_argument('--repair-rounds',type=int,default=10)
    parser.add_argument('--output',required=True)
    args=parser.parse_args();config=vars(args).copy();out=Path(config.pop('output')).resolve()
    if out.exists():raise ValueError('Output already exists; choose a new run directory')
    problem,hashes=load_problem()
    result=anneal(problem,config)
    verification={}
    for label in ['best','final','best_feasible','sa_initial']:
        state=result[label+'_state']
        if state is None:verification[label]=None;continue
        score=evaluate(problem.events,problem.calendar,problem.domains,problem.solution(state),problem.M)
        if any(score[k]!=result[label+'_score'][k] for k in SCORE_KEYS):raise ValueError('Reference evaluator mismatch')
        verification[label]=score
    out.mkdir(parents=True)
    for label in ['best','final','best_feasible','sa_initial']:
        state=result.pop(label+'_state')
        if state is not None:(out/(label+'-solution.json')).write_text(json.dumps(problem.solution(state),indent=2)+'\n',encoding='utf-8')
    trace=result.pop('trace')
    (out/'trace.json').write_text(json.dumps(trace,indent=2)+'\n',encoding='utf-8')
    (out/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    result.update({'config':config,'input_sha256':hashes,'python':platform.python_version(),'platform':platform.platform(),
        'scope':'Implementation pilot on model-v1 assumptions; not main thesis experiment or school approval.',
        'verified_by_reference_evaluator':True,'swap_pair_count':len(problem.swap_pairs)})
    (out/'run.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k in ['best_score','initialization','first_feasible_iteration','search_seconds','iterations','counters','stop_reason']},indent=2))

if __name__=='__main__':main()
