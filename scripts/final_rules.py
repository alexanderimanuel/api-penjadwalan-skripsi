"""Revisi metodologi final; memakai dataset/domain Agustus yang sudah ada.

Tidak menyediakan kategori wawancara atau formula normalisasi default sekolah.
"""
from collections import Counter,defaultdict
from itertools import combinations
import hashlib
import json
import math
from final_foundation import Calendar, Event, freeze, thaw

COMPONENTS=('SC1','SC2','SC3')

def category(rules,subject,class_id):
    return rules['difficulty']['by_grade'][class_id.split('-')[0]][subject]

def difficulty_key(rules,subject):
    return rules['difficulty'].get('subject_families',{}).get(subject,subject)

def friday_limit(rules,class_id):
    """Periode terakhir yang boleh dipakai hari Jumat untuk kelas tersebut.

    Aturan sekolah: XI/XII tidak boleh belajar pada JP 8-10 hari Jumat
    (batas 7); X tidak boleh melewati JP 9 (batas 9). Berlaku per
    penempatan event (periode akhir = start + JP - 1), bukan total JP.
    """
    grade=class_id.split('-')[0]
    limits=rules.get('friday_jp_limits') or {}
    if grade not in limits:
        return 10**9
    return limits[grade]

class ConfigBlocked(ValueError):pass

def stable_hash(value):
    return hashlib.sha256(json.dumps(thaw(value),sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def raw_bounds(data,rules):
    days=data['calendar']['days'];events=data['events']
    sc1=len(data['teachers'])*sum(max(0,sum(s['kind']=='kbm' for s in d['segments'])-2) for d in days)
    groups=Counter((c,e['subject_id']) for e in events for c in e['class_ids'])
    sc2=sum(n*(n-1)//2 for n in groups.values())
    high=defaultdict(set)
    for e in events:
        for c in e['class_ids']:
            if category(rules,e['subject_id'],c)=='HIGH':high[c].add(difficulty_key(rules,e['subject_id']))
    sc3=len(days)*sum(max(0,len(s)-rules['difficulty']['daily_limit']) for s in high.values())
    return dict(zip(COMPONENTS,[sc1,sc2,sc3]))

def readiness(data,rules):
    missing=[]
    for k in ['rules_version','weights_version','normalization_version']:
        if not rules.get(k):missing.append(k)
    difficulty=rules.get('difficulty',{})
    if not difficulty.get('version'):missing.append('difficulty.version')
    if not difficulty.get('source'):missing.append('difficulty.source (interview/config provenance)')
    combinations_needed={(c.split('-')[0],e['subject_id']) for e in data['events'] for c in e['class_ids']}
    for grade,subject in sorted(combinations_needed):
        if difficulty.get('by_grade',{}).get(grade,{}).get(subject) not in ('HIGH','MEDIUM','LOW','NON_HIGH'):
            missing.append(f'difficulty.by_grade.{grade}.{subject}')
    if type(difficulty.get('daily_limit')) is not int or difficulty['daily_limit']<0:missing.append('difficulty.daily_limit')
    has_friday=any(d.get('id')=='jumat' for d in data.get('calendar',{}).get('days',[]))
    limits=rules.get('friday_jp_limits')
    if has_friday:
        if not isinstance(limits,dict) or set(limits)!= {'X','XI','XII'} or any(type(limits[g]) is not int or limits[g]<0 for g in limits):
            missing.append('friday_jp_limits X/XI/XII last allowed Friday period (HC7: no learning past that period on jumat)')
    w=rules.get('weights',{})
    if set(w)!=set(COMPONENTS) or any(type(w[k]) not in (int,float) or not math.isfinite(w[k]) or w[k]<0 for k in COMPONENTS if k in w):missing.append('weights SC1/SC2/SC3 finite and nonnegative')
    elif not math.isclose(sum(w.values()),1,rel_tol=0,abs_tol=1e-12):missing.append('weights sum must equal 1 (no implicit rescaling)')
    if rules.get('normalization',{}).get('method')!='divide_by_upper_bound':missing.append('normalization formula missing/unsupported; no formula assumed')
    if not rules.get('normalization',{}).get('source'):missing.append('normalization.source (proposal formula reference)')
    if not rules.get('reviewed_for_dataset'):missing.append('reviewed_for_dataset')
    if data.get('status','').endswith('pending_semantic_validation') and not rules.get('assignment_semantics_decision'):
        missing.append('assignment_semantics_decision for multi-teacher/grouped events')
    if rules.get('additional_constraints') not in ({},None):missing.append('additional constraints unsupported; must implement explicitly')
    if not missing:
        lower=raw_bounds(data,rules)
        for k in COMPONENTS:
            v=rules['normalization'].get('upper_bounds',{}).get(k)
            if type(v) not in (int,float) or not math.isfinite(v) or v<lower[k]:missing.append(f'normalization.upper_bounds.{k} must cover proven bound {lower[k]}')
    return missing

def combine(h,raw,rules):
    normalized={}
    for k in COMPONENTS:
        upper=rules['normalization']['upper_bounds'][k]
        if raw[k]<0 or raw[k]>upper:raise ValueError('Raw penalty exceeds configured bound; do not silently clip')
        normalized[k]=raw[k]/upper if upper else 0.0
    S=sum(rules['weights'][k]*normalized[k] for k in COMPONENTS)
    if not 0<=S<=1+1e-12:raise ValueError('Preference score out of bounds')
    H=sum(h.values())
    return {'HC':h,'H':H,'raw':raw,'normalized':normalized,'S':S,'F':2*H+S,'feasible':H==0,
            'hard':{**h,'total':H},'soft_raw':raw.copy(),'soft_normalized':normalized.copy()}

class FinalModel:
    # Public canonical facts cannot be replaced or changed through nested aliases.
    data=property(lambda self:self._data)
    rules=property(lambda self:self._rules)
    events=property(lambda self:self._events)
    event_registry=property(lambda self:self._event_registry)
    calendar=property(lambda self:self._calendar)

    def __init__(self,data,rules):
        registry={e['id']:Event.from_source(e) for e in data['events']}
        if len(registry)!=len(data['events']):raise ValueError('Duplicate canonical event')
        calendar=Calendar.from_source(data['calendar'])
        teacher_ids={t['source_code'] for t in data['teachers']}
        if any(not set(e.teacher_ids)<=teacher_ids for e in registry.values()):raise ValueError('Unknown canonical teacher')
        problems=readiness(data,rules)
        if problems:raise ConfigBlocked('; '.join(problems))
        self._data=freeze(data);self._rules=freeze(rules)
        self._events=freeze({e['id']:e for e in self.data['events']})
        self._event_registry=freeze(registry);self._calendar=calendar
        self.days=freeze({d['id']:d for d in self.data['calendar']['days']})
        self.domains={eid:list(calendar.valid_starts(e)) for eid,e in registry.items()}
        if not all(self.domains.values()):raise ValueError('Empty domain')
        self.allowed={eid:set(v) for eid,v in self.domains.items()}
        self.active={d:set(s['period'] for s in day['segments'] if s['kind']=='kbm') for d,day in self.days.items()}
        self.ids=sorted(self.events)
        def sig(e):return (e['time']['jp'],e['time']['kbm_minutes'],tuple((b['after_jp'],b['minutes']) for b in e['time']['breaks']))
        self.swap_pairs=[(a,b) for a,b in combinations(self.ids,2) if sig(self.events[a])==sig(self.events[b])]
        self.dataset_hash=stable_hash(data);self.rules_hash=stable_hash(rules)

    def score(self,x,partial=False):
        if (not partial and set(x)!=set(self.events)) or not set(x)<=set(self.events):raise ValueError('Invalid event set')
        if any(not isinstance(pos,(list,tuple)) or len(pos)!=2 or not isinstance(pos[0],str) or type(pos[1]) is not int
               or tuple(pos) not in self.allowed[eid] for eid,pos in x.items()):raise ValueError('Placement outside domain')
        teachers=defaultdict(list);classes=defaultdict(list);busy=defaultdict(set);meetings=Counter();high=defaultdict(set)
        classperiods=defaultdict(set)
        hc7=0
        for eid,(day,start) in x.items():
            e=self.events[eid];periods=range(start,start+e['time']['jp'])
            for t in e['teacher_codes']:
                busy[t,day].update(periods)
                for p in periods:teachers[t,day,p].append(eid)
            for c in e['class_ids']:
                meetings[c,e['subject_id'],day]+=1
                if category(self.rules,e['subject_id'],c)=='HIGH':high[c,day].add(difficulty_key(self.rules,e['subject_id']))
                classperiods[c,day].update(periods)
                for p in periods:classes[c,day,p].append(eid)
            if day=='jumat':
                last=start+e['time']['jp']-1
                if any(last>friday_limit(self.rules,c) for c in e['class_ids']):
                    hc7+=1
        pairs=[{tuple(sorted(p)) for ids in resource.values() for p in combinations(ids,2)} for resource in [teachers,classes]]
        h={f'HC{i}':0 for i in range(1,9)}
        hc8=sum(sum((p<max(ps) if self.rules.get('class_start_first_kbm',False) else min(ps)<p<max(ps)) and p not in ps for p in self.active[d]) for (_,d),ps in classperiods.items() if ps)
        h.update(HC1=len(pairs[0]),HC2=len(pairs[1]),HC7=hc7,HC8=hc8)
        raw={'SC1':sum(sum(min(ps)<p<max(ps) and p not in ps for p in self.active[d]) for (_,d),ps in busy.items()),
             'SC2':sum(n*(n-1)//2 for n in meetings.values()),
             'SC3':sum(max(0,len(subjects)-self.rules['difficulty']['daily_limit']) for subjects in high.values())}
        return combine(h,raw,self.rules)

    def timetable(self,x):
        if not set(x)<=set(self.events):raise ValueError('Unknown event IDs in schedule')
        if any(not isinstance(p,(tuple,list)) or len(p)!=2 or not isinstance(p[0],str) or type(p[1]) is not int for p in x.values()):
            raise ValueError('Schedule must contain only (day, start_period) placements')
        rows=[]
        for eid in self.ids:
            if eid not in x:continue
            e=self.events[eid];day,start=x[eid]
            rows.append({'event_id':eid,'day':day,'start_period':start,'end_period':start+e['time']['jp']-1,
                'subject_id':e['subject_id'],'class_ids':e['class_ids'],'teacher_codes':e['teacher_codes'],
                'jp':e['time']['jp'],'kbm_minutes':e['time']['kbm_minutes'],'breaks':e['time']['breaks'],
                'source_metadata':e['source_evidence']})
        return thaw(rows)

    def evaluate(self,placements):
        """Auditable public evaluator; accepts placements, including invalid ones."""
        from final_validator import validate_placements
        return validate_placements(self,placements)
