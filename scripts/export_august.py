"""Ekspor jadwal kelas/guru dan verifikasi ulang solusi dari run SA Agustus."""
from collections import Counter
from pathlib import Path
import hashlib
import json
from anneal_august import load_problem, SCORE_KEYS
from model_august import ROOT, SNAPSHOT, evaluate

KINDS=['best','best_feasible','final','sa_initial']
DAYS=['senin','selasa','rabu','kamis','jumat']

def read_json(path):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError(f'Duplicate JSON key: {key}')
            result[key]=value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'),object_pairs_hook=unique)

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def validate_run(run_dir,kind='best'):
    if kind not in KINDS:raise ValueError('Unknown solution kind')
    folder=Path(run_dir).resolve();run=read_json(folder/'run.json')
    for path,expected in run['input_sha256'].items():
        if digest(ROOT/path)!=expected:raise ValueError(f'Run input or code changed: {path}')
    problem,_=load_problem()
    solution_path=folder/f'{kind}-solution.json'
    if not solution_path.is_file():raise ValueError(f'Solution not available: {kind}')
    solution=read_json(solution_path)
    score=evaluate(problem.events,problem.calendar,problem.domains,solution,problem.M)
    expected=run[kind+'_score']
    if expected is None or any(score[k]!=expected[k] for k in SCORE_KEYS):raise ValueError('Recomputed score differs from run metadata')
    stored=read_json(folder/'verification.json')[kind]
    if stored is None or any(score[k]!=stored[k] for k in SCORE_KEYS):raise ValueError('Recomputed score differs from stored verification')
    data=read_json(SNAPSHOT/'data/reconciliation/2026-08-03/candidate-dataset.json')
    report={'status':'verified_current_solution','run_dir':str(folder),'solution_kind':kind,
        'seed':run['config']['seed'],'score':{k:score[k] for k in ['HC1','HC2','HC3','HC4','HC5','HC6','H','SC1','SC2','S','F','feasible']},
        'dataset_version':'2026-08-03-work-v1','model_version':'2026-08-03-model-v1',
        'school_confirmed':False,'pending_source_issue_ids':[i['id'] for i in data['issues'] if i['status']=='open'],
        'hashes':{p.name:digest(p) for p in [folder/'run.json',solution_path,folder/'verification.json']}}
    return problem,data,solution,report

def materialize(problem,data,solution,report):
    """Times are recomputed at destination; source start/end must not leak into export."""
    by_day={d['id']:{s['period']:s for s in d['segments'] if s['period'] is not None} for d in problem.calendar['days']}
    rows=[]
    for e in problem.events:
        p=solution[e['id']];start=p['start_period'];end=start+e['time']['jp']-1
        chosen=[by_day[p['day']][n] for n in range(start,end+1)]
        rows.append({'event_id':e['id'],'class_ids':e['class_ids'],'teacher_codes':e['teacher_codes'],
            'subject_id':e['subject_id'],'day':p['day'],'start_period':start,'end_period':end,
            'start':chosen[0]['start'],'end':chosen[-1]['end'],
            'jp':len(chosen),'kbm_minutes':sum(s['duration_minutes'] for s in chosen),
            'intervals':[{'period':s['period'],'start':s['start'],'end':s['end']} for s in chosen],
            'breaks':e['time']['breaks'],'source_placement':e['reference'],
            'changed':p['day']!=e['reference']['day'] or start!=e['reference']['start_period'],
            'source_block_ids':[e['source_evidence']['class_block']['id']]+[b['id'] for b in e['source_evidence']['teacher_blocks']]})
    def order(r):return (DAYS.index(r['day']),r['start_period'],r['event_id'])
    def totals(rs):return {'meetings':len(rs),'jp':sum(r['jp'] for r in rs),'kbm_minutes':sum(r['kbm_minutes'] for r in rs)}
    classes=[]
    for c in data['classes']:
        selected=sorted([r for r in rows if c['id'] in r['class_ids']],key=order)
        fixed=[{'activity':b['fixed_activity'],'day':b['day'],'period':b['start_period'],
                'start':b['time']['start'],'end':b['time']['end'],'minutes':b['time']['fixed_minutes']}
               for b in data['fixed_activity_source_records'] if c['id'] in b['class_ids']]
        classes.append({'id':c['id'],'lessons':selected,'fixed_activities':fixed,'totals':totals(selected)})
    teachers=[]
    for t in sorted(data['teachers'],key=lambda t:int(t['source_code'])):
        selected=sorted([r for r in rows if t['source_code'] in r['teacher_codes']],key=order)
        teachers.append({'code':t['source_code'],'name':t['display_name'],'lessons':selected,'totals':totals(selected)})
    return {'metadata':report,'calendar':problem.calendar,'events':rows,'classes':classes,'teachers':teachers}

def audit_export(data,problem):
    errors=[];expected={e['id']:e for e in problem.events}
    actual={r['event_id']:r for r in data['events']}
    if len(actual)!=len(data['events']) or set(actual)!=set(expected):errors.append('event coverage')
    for eid,r in actual.items():
        e=expected.get(eid)
        if not e:continue
        for k in ['class_ids','teacher_codes','subject_id']:
            if r[k]!=e[k]:errors.append(f'assignment {eid} {k}')
        if r['jp']!=e['time']['jp'] or r['kbm_minutes']!=e['time']['kbm_minutes'] or r['breaks']!=e['time']['breaks']:errors.append(f'duration {eid}')
        position={'day':r['day'],'start_period':r['start_period']}
        if position not in problem.domains[eid]:errors.append(f'domain {eid}')
        if r['end_period']!=r['start_period']+e['time']['jp']-1:errors.append(f'end period {eid}')
        slots={s['period']:s for d in problem.calendar['days'] if d['id']==r['day'] for s in d['segments'] if s['kind']=='kbm'}
        selected=[slots.get(p) for p in range(r['start_period'],r['end_period']+1)]
        if not selected or any(s is None for s in selected):errors.append(f'calendar span {eid}')
        elif (r['start']!=selected[0]['start'] or r['end']!=selected[-1]['end'] or
              r['intervals']!=[{'period':s['period'],'start':s['start'],'end':s['end']} for s in selected]):errors.append(f'clock {eid}')
    for field,resource,ids in [('classes','id','class_ids'),('teachers','code','teacher_codes')]:
        seen=Counter()
        for view in data[field]:
            expected_ids={eid for eid,e in expected.items() if view[resource] in e[ids]}
            if Counter(r['event_id'] for r in view['lessons'])!=Counter({eid:1 for eid in expected_ids}):errors.append(f'{field} coverage {view[resource]}')
            for r in view['lessons']:
                seen[r['event_id']]+=1
                if r!=actual[r['event_id']]:errors.append('view differs from event')
            if view['totals']!={'meetings':len(view['lessons']),'jp':sum(r['jp'] for r in view['lessons']),
                                'kbm_minutes':sum(r['kbm_minutes'] for r in view['lessons'])}:errors.append('view totals')
        if seen!=Counter({eid:len(e[ids]) for eid,e in expected.items()}):errors.append(f'{field} multiplicity')
    if errors:raise ValueError('; '.join(errors))
    return {'status':'passed','unique_events':len(actual),'class_views':len(data['classes']),
        'teacher_views':len(data['teachers']),'teacher_lesson_rows':sum(len(t['lessons']) for t in data['teachers']),
        'class_fixed_records':sum(len(c['fixed_activities']) for c in data['classes']),
        'changed_events':sum(r['changed'] for r in actual.values())}

def escape(value):return str(value).replace('|','\\|').replace('\n',' ')

def render_schedule(data,kind):
    m=data['metadata'];sc=m['score'];name='Kelas' if kind=='classes' else 'Guru'
    lines=[f'# Jadwal {name} — SA seed {m["seed"]}', '',
        f"Run: {m['run_dir']}; solusi: {m['solution_kind']}; model: {m['model_version']}.", '',
        f"H={sc['H']}; SC1={sc['SC1']}; SC2={sc['SC2']}; S={sc['S']}. Status: {'layak menurut model kerja' if sc['feasible'] else 'belum layak menurut model kerja'}; belum disahkan sekolah.", '',
        'Rentang jam mencakup istirahat bila blok melintasinya; menit KBM mengecualikan istirahat. Tanda * menunjukkan perubahan posisi dari sumber.', '',
        'Kalender tetap: Senin 06:45–07:45 UPCR; Jumat 06:45–07:20 4K. Pada tampilan guru, ini informasi kalender dan bukan penugasan guru yang diasumsikan.', '']
    names={t['code']:t['name'] for t in data['teachers']}
    for item in data[kind]:
        title=item['id'] if kind=='classes' else f"{item['code']} — {item['name']}"
        totals=item['totals']
        lines += [f'## {escape(title)}','',f"{totals['meetings']} pertemuan; {totals['jp']} JP; {totals['kbm_minutes']} menit KBM.",'']
        if kind=='classes':
            lines += [f"Kegiatan tetap: {', '.join(f['activity']+' '+f['day']+' '+f['start']+'–'+f['end'] for f in item['fixed_activities'])}.",'']
        if not item['lessons']:
            lines+=['Tidak ada penugasan pelajaran pada versi sumber ini.',''];continue
        resource='Pengampu (kode)' if kind=='classes' else 'Kelas'
        lines += [f'| Hari | Periode | Waktu | Mapel | {resource} | JP | Menit KBM | Jeda internal | Event |',
                  '|---|---|---|---|---|---:|---:|---|---|']
        for r in item['lessons']:
            other=', '.join(names[c]+' ('+c+')' for c in r['teacher_codes']) if kind=='classes' else ', '.join(r['class_ids'])
            gap='; '.join('setelah JP '+str(b['after_jp'])+': '+str(b['minutes'])+' menit' for b in r['breaks']) or '—'
            lines.append('| '+' | '.join(escape(x) for x in [r['day'],f"{r['start_period']}–{r['end_period']}",r['start']+'–'+r['end'],r['subject_id']+(' *' if r['changed'] else ''),other,r['jp'],r['kbm_minutes'],gap,r['event_id']])+' |')
        lines.append('')
    return '\n'.join(lines)+'\n'

def export_run(run_dir,output,kind='best'):
    output=Path(output).resolve()
    if output.exists():raise ValueError('Output already exists; use a new export directory')
    problem,dataset,solution,report=validate_run(run_dir,kind)
    data=materialize(problem,dataset,solution,report)
    validation=audit_export(data,problem)
    # Validate everything in memory before writing, and verify JSON views after serialization.
    raw=json.dumps(data,ensure_ascii=False,indent=2)+'\n'
    audit_export(json.loads(raw),problem)
    payloads={'schedule.json':raw,'jadwal-kelas.md':render_schedule(data,'classes'),'jadwal-guru.md':render_schedule(data,'teachers'),
              'validation.json':json.dumps({'solution':report,'export':validation},ensure_ascii=False,indent=2)+'\n'}
    s=report['score']
    payloads['README.md']=f"# Ekspor jadwal SA — seed {report['seed']}\n\nSolusi: {kind}. H={s['H']}, SC1={s['SC1']}, SC2={s['SC2']}, S={s['S']}. Belum disahkan sekolah.\n\n- [Jadwal 27 kelas](jadwal-kelas.md)\n- [Jadwal 58 guru](jadwal-guru.md)\n- [Data terstruktur](schedule.json)\n- [Verifikasi](validation.json)\n\n{validation['changed_events']} event berubah dari acuan. Kegiatan AGM beberapa pengampu mengikuti asumsi model kerja; persoalan sumber tetap tersimpan pada metadata.\n"
    output.mkdir(parents=True)
    manifest={'run':report,'exporter_sha256':digest(Path(__file__)),'files':[]}
    for name,text in payloads.items():
        p=output/name;p.write_text(text,encoding='utf-8')
        manifest['files'].append({'name':name,'sha256':digest(p)})
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    audit_export(read_json(output/'schedule.json'),problem)
    return validation
