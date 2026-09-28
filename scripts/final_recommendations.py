"""Validation, canonical deduplication and >=5% diversity selection."""
import math
from collections import Counter, defaultdict
from final_rules import stable_hash
from final_validator import validate
from final_sa import score_equal

def event_groups(model):
    """Equivalent meetings retain identity in storage, but not artificial diversity."""
    groups = defaultdict(list)
    for eid, event in model.events.items():
        signature = stable_hash([event['subject_id'], sorted(event['class_ids']),
            sorted(event['teacher_codes']), event['time']['jp'],
            event['time']['kbm_minutes'], event['time']['breaks']])
        groups[signature].append(eid)
    return dict(sorted(groups.items()))

def canonical(solution, model=None):
    if model is None:  # Preserve the placement-only utility's existing interface.
        return stable_hash([[eid,*solution[eid]] for eid in sorted(solution)])
    if set(solution) != set(model.events): raise ValueError('Incomplete event set')
    return stable_hash([[signature, sorted(tuple(solution[e]) for e in ids)]
                       for signature, ids in event_groups(model).items()])

def placement_distance(a,b,model=None):
    if set(a)!=set(b):raise ValueError('Distance requires identical event sets')
    if model is None: return sum(tuple(a[eid])!=tuple(b[eid]) for eid in a)
    if set(a) != set(model.events): raise ValueError('Incomplete event set')
    return sum(len(ids)-sum((Counter(tuple(a[e]) for e in ids) &
                            Counter(tuple(b[e]) for e in ids)).values())
               for ids in event_groups(model).values())

def sufficiently_different(candidate,selected,threshold,model=None):
    return all(placement_distance(candidate,previous,model)>=threshold for previous in selected)

def ranking_key(candidate):
    score = candidate['score']
    return (score['S'], score['raw']['SC3'], score['raw']['SC1'],
            score['raw']['SC2'], candidate['canonical_id'])

def select(model,runs):
    candidates={};rejected=[]
    for index,run in enumerate(runs):
        if run.get('dataset_hash')!=model.dataset_hash or run.get('rules_hash')!=model.rules_hash:
            rejected.append({'run_index':index,'reason':'different dataset/rules'});continue
        if run.get('best_feasible') is None:continue
        x={eid:tuple(pos) for eid,pos in run['best_feasible'].items()}
        try:
            if set(x)!=set(model.events):raise ValueError('incomplete event IDs')
            # Roundtrip with immutable canonical facts then independent validation.
            score=validate(model,model.timetable(x))
            if not score['feasible'] or not score_equal(score,run['best_feasible_quality']):raise ValueError('feasibility/score mismatch')
        except (ValueError,KeyError,TypeError) as error:
            rejected.append({'run_index':index,'reason':str(error)});continue
        cid=canonical(x,model)
        if cid not in candidates:candidates[cid]={'canonical_id':cid,'solution':x,'score':score,'source_runs':[]}
        candidates[cid]['source_runs'].append({'seed':run['seed'],'configuration':run['configuration']['id']})
    ranked=sorted(candidates.values(),key=ranking_key)
    selected=[];threshold=math.ceil(.05*len(model.events))
    for item in ranked:
        distances=[placement_distance(item['solution'],old['solution'],model) for old in selected]
        if sufficiently_different(item['solution'],[old['solution'] for old in selected],threshold,model):
            selected.append({**item,'distance_from_selected':distances})
        if len(selected)==3:break
    return {'recommendations':selected,'unique_feasible_candidates':len(ranked),'minimum_changed_placements':threshold,
            'rejected_runs':rejected,'selected_count':len(selected),'candidate_pool':ranked,
            'canonicalization_version':'identical-event-multiset-v2'}
