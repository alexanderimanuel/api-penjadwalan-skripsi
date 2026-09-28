"""Audit independen calon dataset; snapshot versi kerja bukan pengesahan sekolah."""
from collections import Counter
from itertools import combinations
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]
VERSION='2026-08-03-work-v1'
OUT=ROOT/'data/versions'/VERSION

def mins(value):
    h,m=map(int,value.split(':'))
    if not (0<=h<24 and 0<=m<60): raise ValueError(value)
    return h*60+m

def audit(candidate,blocks,calendar):
    errors=[]
    hc={f'HC{i}':[] for i in range(1,7)}
    def error(code,detail): errors.append({'code':code,'detail':detail})
    events=candidate['events']
    source={b['id']:b for b in blocks['blocks']}
    expected={b['id'] for b in source.values() if b['source']=='kelas' and b['subject_id']}
    ids=Counter(e['id'] for e in events)
    if any(n!=1 for n in ids.values()): error('duplicate_event_id',dict(ids))
    used=Counter(e['source_evidence']['class_block']['id'] for e in events)
    if set(used)!=expected or any(n!=1 for n in used.values()):
        hc['HC3'].append({'missing':sorted(expected-set(used)),'extra':sorted(set(used)-expected),
                          'repeated':[k for k,v in used.items() if v!=1]})
        error('event_coverage',hc['HC3'][-1])
    if candidate['calendar']!=calendar: error('calendar_changed','Embedded calendar differs from input')
    teacher_set={t['source_code'] for t in candidate['teachers']}
    class_set={c['id'] for c in candidate['classes']}
    subjects={s['id'] for s in candidate['subjects']}
    teacher_used=Counter()
    intervals={}
    for e in events:
        eid=e['id']; evidence=e['source_evidence']; bid=evidence['class_block']['id']
        original=source.get(bid)
        if original is None: error('unknown_source_block',eid);continue
        if evidence['class_block']!=original: error('class_evidence_changed',eid)
        for field in ['class_ids','teacher_codes','subject_id']:
            if e[field]!=original[field]:
                hc['HC6'].append({'event_id':eid,'field':field});error('assignment_changed',{'event_id':eid,'field':field})
        if (not e['class_ids'] or not e['teacher_codes'] or not set(e['class_ids'])<=class_set
                or not set(e['teacher_codes'])<=teacher_set or e['subject_id'] not in subjects): error('unknown_identity',eid)
        if len(set(e['teacher_codes']))!=len(e['teacher_codes']): error('duplicate_teacher',eid)
        ref=e['reference']
        if any(ref[k]!=original[k] for k in ['day','start_period','end_period']): error('reference_changed',eid)
        codes=[]
        for tb in evidence['teacher_blocks']:
            teacher_used[tb['id']]+=1
            if source.get(tb['id'])!=tb: error('teacher_evidence_changed',eid)
            codes+=tb['teacher_codes']
            if (tb['class_ids']!=e['class_ids'] or tb['subject_id']!=e['subject_id']
                    or any(tb[k]!=ref[k] for k in ['day','start_period','end_period']) or tb['time']!=e['time']): error('teacher_pair_mismatch',eid)
        if Counter(codes)!=Counter(e['teacher_codes']): error('teacher_set_mismatch',eid)
        try:
            day=next(d for d in calendar['days'] if d['id']==ref['day'])
            slots={s['period']:s for s in day['segments'] if s['period'] is not None}
            periods=list(range(ref['start_period'],ref['end_period']+1))
            if not periods: raise ValueError('empty span')
            selected=[slots[p] for p in periods]
            if any(s['kind']!='kbm' for s in selected):
                hc['HC5'].append(eid);raise ValueError('fixed activity used')
            calculated={'jp':len(selected),'kbm_minutes':sum(mins(s['end'])-mins(s['start']) for s in selected),
                'period_minutes':[mins(s['end'])-mins(s['start']) for s in selected],
                'breaks':[{'after_jp':i,'minutes':mins(b['start'])-mins(a['end'])}
                          for i,(a,b) in enumerate(zip(selected,selected[1:]),1) if a['end']!=b['start']],
                'elapsed_minutes':mins(selected[-1]['end'])-mins(selected[0]['start']),
                'start':selected[0]['start'],'end':selected[-1]['end'],
                'kbm_intervals':[{'period':s['period'],'start':s['start'],'end':s['end']} for s in selected]}
            if calculated!=e['time'] or e['time']!=original['time']: raise ValueError('duration or break differs')
            intervals[eid]=[(mins(s['start']),mins(s['end'])) for s in selected]
        except (ValueError,KeyError,StopIteration) as ex:
            hc['HC4'].append({'event_id':eid,'detail':str(ex)});error('calendar_or_duration',eid)
    expected_teachers={b['id'] for b in source.values() if b['source']=='guru' and b['subject_id']}
    if set(teacher_used)!=expected_teachers or any(v!=1 for v in teacher_used.values()): error('teacher_block_coverage','Missing, extra or reused teacher block')
    expected_fixed=[b for b in blocks['blocks'] if b['source']=='kelas' and b['fixed_activity']]
    if candidate['fixed_activity_source_records']!=expected_fixed: error('fixed_records_changed','Fixed source records differ')
    for f in expected_fixed:
        day=next(d for d in calendar['days'] if d['id']==f['day'])
        s=next((s for s in day['segments'] if s['period']==f['start_period']),None)
        if not s or s['source_label']!=f['fixed_activity'] or f['start_period']!=f['end_period']:
            error('fixed_calendar_mismatch',f['id'])
    for a,b in combinations(events,2):
        if a['reference']['day']!=b['reference']['day']: continue
        overlap=sorted(set((max(x,u),min(y,v)) for x,y in intervals.get(a['id'],[]) for u,v in intervals.get(b['id'],[]) if max(x,u)<min(y,v)))
        if not overlap: continue
        for code,field in [('HC1','teacher_codes'),('HC2','class_ids')]:
            shared=sorted(set(a[field])&set(b[field]))
            if shared: hc[code].append({'event_ids':[a['id'],b['id']],field:shared,
                'day':a['reference']['day'],'overlap_minutes_since_midnight':overlap})
    return {'integrity_passed':not errors,'integrity_errors':errors,
        'reference_hard_audit':hc,'reference_feasible_under_recorded_assignments':not any(hc.values()),
        'counts':{'events':len(events),'teacher_evidence_links':sum(teacher_used.values()),'fixed_records':len(expected_fixed)},
        'scope':'Source-reference integrity and preliminary HC audit; not the final optimization evaluator.'}

def freeze(files,destination):
    """Preflight all existing bytes; never replace an existing different version."""
    for relative,data in files.items():
        target=destination/relative
        if target.exists() and target.read_bytes()!=data:
            raise ValueError(f'Versi sudah ada dengan isi berbeda: {relative}; gunakan versi baru')
    for relative,data in files.items():
        target=destination/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists(): target.write_bytes(data)
        if target.read_bytes()!=data: raise ValueError(f'Gagal verifikasi: {relative}')

def main():
    relative=['data/reconciliation/2026-08-03/candidate-dataset.json',
              'data/blocks/2026-08-03/blocks.json','data/calendar/2026-08-03/calendar.json',
              'data/entities/2026-08-03/entities.json','data/reconciliation/2026-08-03/issues.json',
              'data/reconciliation/2026-08-03/reconciliation.json']
    loaded=[json.loads((ROOT/p).read_text(encoding='utf-8')) for p in relative]
    candidate,blocks,calendar,entities,issues,reconciliation=loaded
    for obj in [candidate,blocks]:
        for path,digest in obj['input_sha256'].items():
            if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=digest: raise ValueError(f'Input changed: {path}')
    for src in candidate['sources']:
        if hashlib.sha256((ROOT/src['frozen_path']).read_bytes()).hexdigest()!=src['sha256']: raise ValueError('PDF changed')
        relative.append(src['frozen_path'])
    report=audit(candidate,blocks,calendar)
    if candidate['issues']!=issues: raise ValueError('Issues differ')
    if len(report['reference_hard_audit']['HC1'])!=len(reconciliation['source_overlaps']): raise ValueError('Overlap audits differ')
    if not report['integrity_passed']: raise ValueError(json.dumps(report['integrity_errors']))
    report['version']=VERSION
    report['release_status']='working_snapshot_not_final_school_validated_dataset'
    report['open_issues']=[i['id'] for i in issues if i['status']=='open']
    report['decisions']={'source_placements':'unchanged','multi_teacher_blocks':'one class event with all recorded teachers; semantics pending',
        'empty_teacher':'retain zero-load identity','reference_overlap':'retain and disclose; no invented correction',
        'final_dataset_release':'pending semantic decisions and source clarification; no school approval inferred'}
    relative+=['scripts/validate_august_dataset.py','scripts/test_august_dataset.py','scripts/reconcile_august.py',
               'scripts/extract_august_blocks.py','scripts/build_august_calendar.py','scripts/extract_august_entities.py']
    files={p:(ROOT/p).read_bytes() for p in relative}
    files['validation.json']=(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    manifest={'version':VERSION,'status':report['release_status'],'school_confirmed':False,
        'reference_feasible':report['reference_feasible_under_recorded_assignments'],
        'dataset_path':relative[0],'files':[{'path':p,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for p,raw in sorted(files.items())],
        'version_policy':'Existing different bytes are rejected; corrections require a new version. No physical write protection is claimed.'}
    files['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    freeze(files,OUT)
    print(json.dumps({'version':VERSION,'integrity_passed':report['integrity_passed'],
        'hc_pair_or_record_counts':{k:len(v) for k,v in report['reference_hard_audit'].items()},
        'open_issues':len(report['open_issues']),'snapshot_files':len(files)},indent=2))

if __name__=='__main__': main()
