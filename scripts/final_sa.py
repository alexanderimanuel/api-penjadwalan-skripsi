"""Final-methodology SA mechanics; school runs require complete reviewed config."""
from collections import Counter
import copy
import math
import random
import time
from final_validator import validate
from final_foundation import thaw
from final_rules import stable_hash

ENGINE_VERSION='final-sa-v2'
SEARCH_LOG_COLUMNS=['evaluation_index','search_evaluation','temperature','operator','delta',
    'accepted','acceptance_probability','random_draw','current_F','current_H','current_S',
    'best_F','best_H','best_S','best_feasible_F','best_feasible_S',
    'generation_status','generation_attempts','attempted_operators','rejection_reasons','accepted_changes']

def rng_seeds(seed):
    """Separate streams: cooling/search cannot consume construction randomness."""
    return {'construction':seed,'calibration':seed^0x5A17,
            'search':int(stable_hash(['search',ENGINE_VERSION,seed])[:16],16)}

def metropolis(delta,temperature,rng):
    if not math.isfinite(delta) or not math.isfinite(temperature) or temperature<=0:
        raise ValueError('Metropolis requires finite delta and positive temperature')
    if delta<=0:return True,1.0,None
    probability=math.exp(-delta/temperature)
    draw=rng.random()  # Fresh independent draw in [0,1) for every uphill proposal.
    return draw<probability,probability,draw

def configurations(n):
    return [{'id':f'C{i+1}','alpha':a,'L':m*n,'budget':100000,'Tmin':.001,'stagnation':20000,
             'calibration_samples':200,'p0':.80,'max_attempts':50,'swap_probability':.5}
            for i,(m,a) in enumerate((m,a) for m in (1,3) for a in (.90,.95,.98))]

def construct(model,rng,trace=None):
    ids=model.ids.copy();rng.shuffle(ids);ids.sort(key=lambda e:len(model.domains[e]))
    x={};evaluations=0;order=[];previous={'H':0,'S':0.0,'F':0.0}
    for eid in ids:
        options=[];best=None
        for position in model.domains[eid]:
            candidate={**x,eid:position};score=model.score(candidate,partial=True);evaluations+=1
            # Lexicographic additional H/F makes the hard-conflict fallback explicit.
            # It is equivalent to minimum delta F because F=2H+S and S is bounded.
            key=(score['H']-previous['H'],score['F']-previous['F'])
            if best is None or key<best:best=key;options=[(position,score)]
            elif key==best:options.append((position,score))
        if not options:raise ValueError('Empty start domain: '+eid)
        position,chosen=rng.choice(options);x[eid]=tuple(position);order.append(eid)
        if trace is not None:
            trace.append({'event_id':eid,'domain_size':len(model.domains[eid]),'placement':tuple(position),
                'additional_H':chosen['H']-previous['H'],'additional_S':chosen['S']-previous['S'],
                'additional_F':chosen['F']-previous['F'],'tie_count':len(options),
                'hard_conflict_fallback':best[0]>0,'evaluation_index':evaluations})
        previous=chosen
    return x,evaluations,order

def neighbor(model,x,rng,max_attempts=50,swap_probability=.5):
    if type(max_attempts) is not int or not 1<=max_attempts<=50 or not 0<=swap_probability<=1:
        raise ValueError('Invalid neighbor parameters')
    reasons=Counter();operators=Counter();last_operator=None
    for attempt in range(1,max_attempts+1):
        if rng.random()<swap_probability:
            operators['swap']+=1;last_operator='swap'
            if not model.swap_pairs:reasons['no_compatible_swap_pairs']+=1;continue
            a,b=rng.choice(model.swap_pairs)
            if x[a]==x[b] or x[b] not in model.allowed[a] or x[a] not in model.allowed[b]:reasons['swap_target_incompatible']+=1;continue
            y=x.copy();y[a],y[b]=x[b],x[a]
            return y,{'operator':'swap','attempts':attempt,'rejections':dict(reasons),
                      'attempted_operators':dict(operators),'status':'structural_change'}
        operators['move']+=1;last_operator='move'
        eid=rng.choice(model.ids);options=[p for p in model.domains[eid] if p!=x[eid]]
        if not options:reasons['singleton_domain']+=1;continue
        y=x.copy();y[eid]=rng.choice(options)
        return y,{'operator':'move','attempts':attempt,'rejections':dict(reasons),
                  'attempted_operators':dict(operators),'status':'structural_change'}
    return x.copy(),{'operator':'no_change','attempts':max_attempts,'rejections':dict(reasons),
        'reason':'candidate_attempt_limit','attempted_operators':dict(operators),
        'last_attempted_operator':last_operator,'status':'no_change'}

def calibrate(model,initial,initial_score,rng,samples=200,p0=.8):
    if samples!=200 or p0!=.8:raise ValueError('Calibration requires 200 samples and p0=0.80')
    before=copy.deepcopy(initial);positive=[];no_change=0;attempts=0;no_change_records=[];sample_log=[]
    for index in range(samples):
        y,meta=neighbor(model,initial,rng);attempts+=meta['attempts']
        no_change+=meta['operator']=='no_change'
        if meta['operator']=='no_change':no_change_records.append({'evaluation':index+1,**meta})
        delta=model.score(y)['F']-initial_score['F']
        sample_log.append([index+1,meta['operator'],delta,meta['attempts'],meta['status']])
        if delta>0:positive.append(delta)
    assert initial==before
    return {'T0':-(sum(positive)/len(positive))/math.log(p0) if positive else 1.0,
            'evaluations':samples,'positive_count':len(positive),'mean_positive_delta':sum(positive)/len(positive) if positive else None,
            'fallback':not positive,'no_change_evaluations':no_change,'generation_attempts':attempts,'p0':p0,
            'no_change_records':no_change_records,
            'sample_log':{'columns':['sample','operator','delta','attempts','generation_status'],'rows':sample_log}}

def score_equal(a,b):
    return all(a[k]==b[k] for k in ['HC','H','raw','normalized','S','F','feasible'])

def run(model,config,seed):
    cfg={'budget':100000,'Tmin':.001,'stagnation':20000,'calibration_samples':200,'p0':.8,
         'max_attempts':50,'swap_probability':.5,**config}
    if type(seed) is not int:raise ValueError('Seed must be integer')
    if not model.ids:raise ValueError('Search requires at least one event')
    if type(cfg.get('alpha')) not in (int,float) or not 0<cfg['alpha']<1 or any(type(cfg.get(k)) is not int or cfg[k]<1 for k in ['budget','L','stagnation']):raise ValueError('Invalid run parameters')
    if cfg['budget']>100000:raise ValueError('Total budget cannot exceed 100000')
    if cfg['calibration_samples']!=200 or cfg['p0']!=.8 or cfg['max_attempts']!=50 or cfg['swap_probability']!=.5:raise ValueError('Final methodology constants changed')
    if type(cfg['Tmin']) not in (int,float) or not math.isfinite(cfg['Tmin']) or cfg['Tmin']<=0:raise ValueError('Invalid Tmin')
    # Reserve initial evaluation, independent initial validation, 200 calibration
    # evaluations and 3 independent final validations. All count toward budget.
    minimum=sum(map(len,model.domains.values()))+202+3
    if cfg['budget']<minimum:raise ValueError(f'Budget too small for construction/calibration/validation: need {minimum}')
    started=time.perf_counter();streams=rng_seeds(seed);rng=random.Random(streams['search']);construction_trace=[]
    current,init_count,order=construct(model,random.Random(streams['construction']),trace=construction_trace)
    initial=copy.deepcopy(current);score=model.score(current)
    checked=validate(model,model.timetable(current))
    if not score_equal(score,checked):raise ValueError('Independent initial validation mismatch')
    calibration=calibrate(model,initial,score,random.Random(streams['calibration']))
    T=calibration['T0'];T0=T;best=current.copy();best_score=copy.deepcopy(score)
    feasible=current.copy() if score['feasible'] else None;feasible_score=copy.deepcopy(score) if feasible is not None else None
    initial_score=copy.deepcopy(score);search=0;stale=0;counts=Counter();reasons=Counter();trace=[];no_change_records=[];search_log=[]
    total=init_count+2+200;conditions=[]
    while True:
        conditions=[]
        if total+3>=cfg['budget']:conditions.append('TOTAL_EVALUATION_BUDGET')
        if T<cfg['Tmin']:conditions.append('TMIN')
        if stale>=cfg['stagnation']:conditions.append('STAGNATION')
        if conditions:break
        y,meta=neighbor(model,current,rng,cfg['max_attempts'],cfg['swap_probability'])
        trial=model.score(y);search+=1;total+=1;stale+=1
        counts['generation_attempts']+=meta['attempts'];counts[meta['operator']+'_evaluations']+=1
        reasons.update(meta['rejections'])
        if meta['operator']=='no_change':no_change_records.append({'search_evaluation':search,**meta})
        delta=trial['F']-score['F']
        accepted,probability,draw=metropolis(delta,T,rng)
        changes=[[eid,*y[eid]] for eid in model.ids if accepted and y[eid]!=current[eid]]
        if accepted:
            current=y;score=trial;counts['accepted']+=1
            if delta>0:counts['accepted_uphill']+=1
        else:counts['rejected']+=1
        if score['F']<best_score['F']:
            best=current.copy();best_score=copy.deepcopy(score);stale=0
            trace.append({'search_evaluations':search,'total_evaluations':total,'T':T,'best':copy.deepcopy(best_score)})
        if score['feasible'] and (feasible_score is None or score['S']<feasible_score['S']):
            feasible=current.copy();feasible_score=copy.deepcopy(score)
        search_log.append([total,search,T,meta['operator'],delta,accepted,probability,draw,
            score['F'],score['H'],score['S'],best_score['F'],best_score['H'],best_score['S'],
            feasible_score['F'] if feasible_score is not None else None,
            feasible_score['S'] if feasible_score is not None else None,
            meta['status'],meta['attempts'],meta['attempted_operators'],meta['rejections'],changes])
        if search%cfg['L']==0:T*=cfg['alpha']
    final_checks={}
    for name,x,expected in [('best',best,best_score),('current',current,score),('best_feasible',feasible,feasible_score)]:
        if x is None:
            # Spend the reserved validation on an independent feasibility recheck;
            # never claim a best feasible solution exists when it does not.
            recheck=validate(model,model.timetable(best));total+=1
            if recheck['feasible']:raise ValueError('Feasible tracking lost a solution')
            final_checks[name]=None;final_checks['no_feasible_recheck']=recheck
            continue
        check=validate(model,model.timetable(x));total+=1
        if not score_equal(expected,check):raise ValueError('Final validator mismatch: '+name)
        final_checks[name]=check
    return {'status':'FEASIBLE' if feasible is not None else 'NO_FEASIBLE_FOUND','engine_version':ENGINE_VERSION,
        'dataset_version':model.data.get('id'),'dataset_hash':model.dataset_hash,'rules_version':model.rules['rules_version'],
        'rules_hash':model.rules_hash,'difficulty_category_version':model.rules['difficulty']['version'],
        'normalization_version':model.rules['normalization_version'],'weights_version':model.rules['weights_version'],'weights':thaw(model.rules['weights']),
        'seed':seed,'configuration':cfg,'alpha':cfg['alpha'],'L':cfg['L'],'T0':T0,'final_temperature':T,
        'evaluations':{'construction_candidates':init_count,'initial_score':1,'calibration':200,'search':search,
                       'independent_validation':1+sum(v is not None for v in final_checks.values()),'total':total},
        'stop_reason':conditions[0],'stop_conditions':conditions,'stagnation_search_evaluations':stale,
        'calibration':calibration,'runtime_seconds':time.perf_counter()-started,'initial_quality':initial_score,
        'best_quality':best_score,'current_quality':score,'best_feasible_quality':feasible_score,
        'initial':initial,'best':best,'current':current,'best_feasible':feasible,
        'initial_hash':stable_hash(initial),'rng_stream_seeds':streams,'construction_trace':construction_trace,
        'search_log':{'schema_version':1,'columns':SEARCH_LOG_COLUMNS.copy(),'rows':search_log,
                      'state_values':'after acceptance and best updates; temperature before cooling',
                      'replay':'Start at initial; apply accepted_changes [event_id,day,start_period] in order.'},
        'stop_snapshot':{'search_evaluations':search,'evaluations_before_final_validation':total-3,
                         'reserved_final_validations':3,'temperature':T,'stagnation':stale},
        'initial_order':order,'candidate_statistics':dict(counts),'candidate_rejection_reasons':dict(reasons),'trace':trace,'no_change_records':no_change_records,
        'final_validation':final_checks,'normalization':thaw(model.rules['normalization']),
        'scoring_backend':'full occupancy recalculation; no incremental cache'}
