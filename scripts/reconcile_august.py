"""Rekonsiliasi dokumen; kecocokan sumber tidak menyatakan jadwal layak."""
from pathlib import Path
from collections import defaultdict, Counter
from itertools import combinations
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'data/reconciliation/2026-08-03'

def key(block, teacher):
    return (tuple(sorted(block['class_ids'])), teacher, block['subject_id'],
            block['day'], block['start_period'], block['end_period'])

def reconcile(blocks):
    teachers = [b for b in blocks if b['source']=='guru' and b['subject_id']]
    classes = [b for b in blocks if b['source']=='kelas' and b['subject_id']]
    index = defaultdict(list)
    for b in teachers:
        for t in b['teacher_codes']:
            index[key(b,t)].append(b)
    matches, errors, used = [], [], Counter()
    for b in classes:
        matched = []
        for t in b['teacher_codes']:
            candidates = index[key(b,t)]
            if len(candidates)!=1:
                errors.append({'type':'missing_match' if not candidates else 'ambiguous_match',
                    'class_block':b['id'],'teacher_code':t,'candidate_ids':[c['id'] for c in candidates]})
                continue
            c=candidates[0]
            differences={field:{'kelas':b['time'].get(field),'guru':c['time'].get(field)}
                for field in set(b['time'])|set(c['time']) if b['time'].get(field)!=c['time'].get(field)}
            if differences:
                errors.append({'type':'time_mismatch','class_block':b['id'],'teacher_block':c['id'],'differences':differences})
                continue
            used[c['id']]+=1
            matched.append({'teacher_code':t,'teacher_block_id':c['id']})
        matches.append({'class_block_id':b['id'],'teacher_matches':matched,
                        'status':'matched' if len(matched)==len(b['teacher_codes']) else 'unresolved'})
    for b in teachers:
        if used[b['id']]!=1:
            errors.append({'type':'unmatched_teacher_block' if not used[b['id']] else 'teacher_block_reused',
                           'teacher_block_id':b['id'],'match_count':used[b['id']]})
    return matches, errors

def overlap_findings(events):
    findings=[]
    for a,b in combinations(events,2):
        if a['reference']['day']!=b['reference']['day']: continue
        resources={'teacher_codes':sorted(set(a['teacher_codes'])&set(b['teacher_codes'])),
                   'class_ids':sorted(set(a['class_ids'])&set(b['class_ids']))}
        if not any(resources.values()): continue
        intervals=[]
        for x in a['time']['kbm_intervals']:
            for y in b['time']['kbm_intervals']:
                start,end=max(x['start'],y['start']),min(x['end'],y['end'])
                if start<end: intervals.append({'start':start,'end':end})
        if intervals:
            findings.append({'event_ids':[a['id'],b['id']],'day':a['reference']['day'],
                             **resources,'kbm_overlap_intervals':intervals,
                             'interpretation':'overlap_under_recorded_resource_assignment'})
    return findings

def recap(events,field):
    groups=defaultdict(list)
    for event in events:
        for resource in event[field]: groups[resource].append(event)
    return [{'id':resource,'meetings':len(items),'jp':sum(e['time']['jp'] for e in items),
             'kbm_minutes':sum(e['time']['kbm_minutes'] for e in items)}
            for resource,items in sorted(groups.items())]

def main():
    paths=[ROOT/'data/blocks/2026-08-03/blocks.json',ROOT/'data/entities/2026-08-03/entities.json',
           ROOT/'data/calendar/2026-08-03/calendar.json']
    data,entities,calendar=[json.loads(p.read_text(encoding='utf-8')) for p in paths]
    for relative,digest in data['input_sha256'].items():
        assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==digest
    for source in data['sources']:
        assert hashlib.sha256((ROOT/source['frozen_path']).read_bytes()).hexdigest()==source['sha256']
    blocks=data['blocks']; lookup={b['id']:b for b in blocks}
    assert len(lookup)==len(blocks) and not data['issues']
    matches,errors=reconcile(blocks)
    events=[]
    for match in matches:
        b=lookup[match['class_block_id']]
        events.append({'id':'E-'+b['id'],'class_ids':b['class_ids'],'teacher_codes':b['teacher_codes'],
            'subject_id':b['subject_id'],'time':b['time'],
            'reference':{'day':b['day'],'start_period':b['start_period'],'end_period':b['end_period']},
            'source_evidence':{'class_block':b,'teacher_blocks':[lookup[m['teacher_block_id']] for m in match['teacher_matches']]},
            'reconciliation_status':match['status'],
            'assignment_interpretation':'single_teacher' if len(b['teacher_codes'])==1 else 'multiple_recorded_teachers_group_semantics_unconfirmed'})
    overlaps=overlap_findings(events)
    multi=[e for e in events if len(e['teacher_codes'])>1]
    fixed=[b for b in blocks if b['source']=='kelas' and b['fixed_activity']]
    totals={'candidate_events':len(events),'matched_class_blocks':sum(m['status']=='matched' for m in matches),
        'matched_teacher_links':sum(len(m['teacher_matches']) for m in matches),
        'mismatches':len(errors),'fixed_class_records':len(fixed),
        'jp_class_basis':sum(e['time']['jp'] for e in events),
        'minutes_class_basis':sum(e['time']['kbm_minutes'] for e in events),
        'jp_teacher_basis':sum(e['time']['jp']*len(e['teacher_codes']) for e in events),
        'minutes_teacher_basis':sum(e['time']['kbm_minutes']*len(e['teacher_codes']) for e in events),
        'multiple_teacher_events':len(multi),'overlapping_event_pairs':len(overlaps)}
    issues=[{'id':'REC-GROUP-'+e['id'],'type':'group_semantics_unconfirmed','event_ids':[e['id']],
             'question':'Apakah kode AGM menunjukkan kelompok siswa berbeda atau pengajaran bersama, dan apakah semua pengampu harus tetap serentak?',
             'status':'open','preservation':'Satu blok kelas; semua kode dipertahankan; JP kelas dihitung sekali.'} for e in multi]
    for i,finding in enumerate(overlaps,1):
        issues.append({'id':f'REC-OVERLAP-{i:02d}','type':'source_overlap','status':'open',**finding,
            'question':'Apakah kedua penugasan benar berlangsung bersamaan, atau ada revisi resmi hari/periode/guru?',
            'preservation':'Penempatan sumber tidak diubah; belum dinyatakan layak.'})
    issues.append({'id':'REC-EMPTY-01','type':'empty_teacher_schedule','teacher_code':'1',
        'status':'recorded_nonblocking','question':'Apakah halaman guru 1 memang tanpa penugasan pada versi ini?',
        'preservation':'Identitas tetap ada; tidak dibuat event.'})
    report={'status':'document_reconciliation_passed' if not errors else 'document_reconciliation_has_mismatches',
            'summary':totals,'matches':matches,'mismatches':errors,'source_overlaps':overlaps,
            'class_recap':recap(events,'class_ids'),'teacher_recap':recap(events,'teacher_codes')}
    # Include zero-load teachers explicitly in recap.
    present={r['id'] for r in report['teacher_recap']}
    report['teacher_recap'] += [{'id':t['source_code'],'meetings':0,'jp':0,'kbm_minutes':0}
                               for t in entities['teachers'] if t['source_code'] not in present]
    groups=defaultdict(list)
    for e in events:
        for c in e['class_ids']: groups[(c,e['subject_id'])].append(e)
    report['class_subject_recap']=[{'class_id':c,'subject_id':s,'meeting_count':len(items),
        'event_ids':[e['id'] for e in items],'block_jp':[e['time']['jp'] for e in items],
        'jp':sum(e['time']['jp'] for e in items),'kbm_minutes':sum(e['time']['kbm_minutes'] for e in items)}
        for (c,s),items in sorted(groups.items())]
    candidate={'schema_version':1,'id':'sman8malang-2026-08-03-candidate',
        'status':'reconciled_candidate_pending_semantic_validation' if not errors else 'incomplete_candidate',
        'school_confirmed':False,'reference_feasible':False if overlaps else None,
        'input_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        'sources':data['sources'],'calendar':calendar,
        'teachers':[{k:t[k] for k in ['id','source_code','display_name','source_ref']} for t in entities['teachers']],
        'classes':[{k:c[k] for k in ['id','label','source_ref']} for c in entities['classes']],
        'subjects':[{k:s[k] for k in ['id','label','expanded_name']} for s in entities['subjects']],
        'events':events,'fixed_activity_source_records':fixed,'issues':issues,
        'model_note':'Recorded teacher sets are preserved; grouped-student semantics and final HC model are not asserted.'}
    OUT.mkdir(parents=True,exist_ok=True)
    for filename,value in [('reconciliation.json',report),('candidate-dataset.json',candidate),('issues.json',issues)]:
        target=OUT/filename
        target.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        assert json.loads(target.read_text(encoding='utf-8'))==value
    lines=['# Pasangan blok kelas–guru','','Cocok berarti catatan dokumen konsisten, bukan jadwal telah layak.','',
           '| Blok kelas | Blok guru pasangan | Status |','|---|---|---|']
    lines += [f"| {m['class_block_id']} | {', '.join(x['teacher_block_id'] for x in m['teacher_matches'])} | {m['status']} |" for m in matches]
    (OUT/'pasangan-blok.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    lines=['# Rekap hasil rekonsiliasi','','JP/menit kelas dan guru memakai basis penghitungan berbeda; keduanya tidak dijumlahkan.','']
    for heading,rows in [('Kelas',report['class_recap']),('Guru',report['teacher_recap'])]:
        lines += [f'## {heading}','','| ID | Pertemuan | JP | Menit KBM |','|---|---:|---:|---:|']
        lines += [f"| {r['id']} | {r['meetings']} | {r['jp']} | {r['kbm_minutes']} |" for r in rows]
        lines.append('')
    lines+=['## Kelas–mata pelajaran','','| Kelas | Mapel | Pertemuan | Pembagian JP blok | JP | Menit KBM |','|---|---|---:|---|---:|---:|']
    lines += [f"| {r['class_id']} | {r['subject_id']} | {r['meeting_count']} | {' + '.join(map(str,r['block_jp']))} | {r['jp']} | {r['kbm_minutes']} |" for r in report['class_subject_recap']]
    (OUT/'rekap.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    assert not errors, 'Lihat mismatches pada reconciliation.json'
    assert totals['jp_class_basis']==data['summary']['kelas']['jp']
    assert totals['minutes_class_basis']==data['summary']['kelas']['kbm_minutes']
    assert totals['jp_teacher_basis']==data['summary']['guru']['jp']
    assert totals['minutes_teacher_basis']==data['summary']['guru']['kbm_minutes']
    print(json.dumps({'summary':totals,'overlaps':overlaps},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
