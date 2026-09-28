"""Ekstraksi blok per sumber; tidak menggabungkan dua dokumen menjadi event final."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import re
import pdfplumber
from build_august_calendar import block_signature

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'data/blocks/2026-08-03'
DAYS = ['senin','selasa','rabu','kamis','jumat']

def inside(box, x, y):
    return box[0] < x < box[2] and box[1] < y < box[3]

def main():
    ep = ROOT/'data/entities/2026-08-03/entities.json'
    cp = ROOT/'data/calendar/2026-08-03/calendar.json'
    entities = json.loads(ep.read_text(encoding='utf-8'))
    calendar = json.loads(cp.read_text(encoding='utf-8'))
    occurrences = {}
    for category in ['subjects','fixed_activities']:
        for entity in entities[category]:
            for occurrence in entity['occurrences']:
                occurrences.setdefault((occurrence['source'],occurrence['page']),[]).append((entity['id'],category,occurrence))
    results, issues, pages = [], [], []
    for source in entities['sources']:
        kind = source['id']; path = ROOT/source['frozen_path']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==source['sha256']
        with pdfplumber.open(path) as doc:
            for n,page in enumerate(doc.pages,1):
                table = max(page.find_tables(),key=lambda t:len(t.cells))
                cells = table.cells
                # Day-label cells determine different row heights in class/teacher layouts.
                day_cells = sorted([b for b in cells if b[0]<20 and 70<b[2]<80 and b[1]>150],key=lambda b:b[1])
                assert len(day_cells)==5,(kind,n,day_cells)
                header = sorted([b for b in cells if 100<b[1]<120 and b[0]>70],key=lambda b:b[0])
                assert len(header)==10,(kind,n,len(header))
                edges=[b[0] for b in header]+[header[-1][2]]
                occupied_cells=set()
                for label,category,occurrence in sorted(occurrences.get((kind,n),[]),key=lambda o:(o[2]['bbox_pdf_points'][1],o[2]['bbox_pdf_points'][0])):
                    box=occurrence['bbox_pdf_points']; x=(box[0]+box[2])/2; y=(box[1]+box[3])/2
                    matches=[b for b in cells if inside(b,x,y)]
                    if len(matches)!=1:
                        issues.append({'type':'cell_not_unique','source':kind,'page':n,'label':label,'bbox':box}); continue
                    cell=matches[0]
                    if cell in occupied_cells:
                        issues.append({'type':'multiple_labels_in_cell','source':kind,'page':n,'bbox':cell})
                    occupied_cells.add(cell)
                    day_index=next(i for i,b in enumerate(day_cells) if b[1]<=y<=b[3])
                    day=DAYS[day_index]
                    left=min(range(11),key=lambda i:abs(edges[i]-cell[0]))
                    right=min(range(11),key=lambda i:abs(edges[i]-cell[2]))
                    assert abs(edges[left]-cell[0])<1 and abs(edges[right]-cell[2])<1
                    start,end=left+1,right
                    raw=page.crop(cell).extract_text() or ''
                    record={'id':f'{kind}-{n:02d}-{len(occupied_cells):02d}',
                        'source':kind,'page':n,'bbox_pdf_points':list(cell),'raw_text':raw,
                        'label_ref':occurrence,'day':day,'start_period':start,'end_period':end,
                        'subject_id':label if category=='subjects' else None,
                        'fixed_activity':label if category=='fixed_activities' else None,
                        'stacked_cell':abs(cell[1]-day_cells[day_index][1])>1 or abs(cell[3]-day_cells[day_index][3])>1,
                        'status':'extracted_not_reconciled'}
                    if kind=='kelas':
                        record['class_ids']=[entities['classes'][n-1]['id']]
                        codes=re.findall(r'\((\d+)\)',raw)
                        for group in re.findall(r'\d+(?:\s*/\s*\d+)+',raw):
                            codes+=re.findall(r'\d+',group)
                        record['teacher_codes']=list(dict.fromkeys(codes))
                    else:
                        record['teacher_codes']=[str(n)]
                        record['class_ids']=list(dict.fromkeys(re.findall(r'X(?:II|I)?-[1-9](?!\d)',raw)))
                    if category=='subjects':
                        if not record['class_ids'] or not record['teacher_codes']:
                            issues.append({'type':'missing_identity','record':record['id'],'raw':raw})
                        try:
                            record['time']=block_signature(calendar,day,start,end-start+1)
                            slots={s['period']:s for d in calendar['days'] if d['id']==day for s in d['segments'] if s['period'] is not None}
                            record['time'].update({'start':slots[start]['start'],'end':slots[end]['end'],
                                'kbm_intervals':[{'period':p,'start':slots[p]['start'],'end':slots[p]['end']} for p in range(start,end+1)]})
                        except ValueError as error:
                            issues.append({'type':'invalid_calendar_span','record':record['id'],'detail':str(error)})
                    else:
                        slots=[s for d in calendar['days'] if d['id']==day for s in d['segments'] if s['period']==start]
                        assert start==end and len(slots)==1 and slots[0]['source_label']==label
                        record['time']={'jp':0,'kbm_minutes':0,'start':slots[0]['start'],'end':slots[0]['end'],'fixed_minutes':slots[0]['duration_minutes']}
                    results.append(record)
                pages.append({'source':kind,'page':n,'labels_expected':len(occurrences.get((kind,n),[])),
                    'cells_with_labels':len(occupied_cells), 'day_bands':[list(b) for b in day_cells]})
    OUT.mkdir(parents=True,exist_ok=True)
    summary={}
    for kind in ['kelas','guru']:
        lessons=[r for r in results if r['source']==kind and r['subject_id']]
        summary[kind]={'lesson_blocks':len(lessons),'jp':sum(r.get('time',{}).get('jp',0) for r in lessons),
            'kbm_minutes':sum(r.get('time',{}).get('kbm_minutes',0) for r in lessons),
            'fixed_blocks':sum(r['source']==kind and r['fixed_activity'] is not None for r in results)}
    data={'schema_version':1,'status':'source_blocks_not_final_events','sources':entities['sources'],
        'input_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ep,cp]},
        'blocks':results,'page_checks':pages,'issues':issues,'summary':summary}
    (OUT/'blocks.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# Blok jadwal hasil ekstraksi','','Dua sumber disajikan terpisah; bukan event final. JP guru dapat menghitung kembali kegiatan kelas yang sama untuk setiap pengampu.','',
        '| ID | Kelas | Guru | Label | Hari | Periode | JP | Menit KBM | Jeda setelah JP (menit) |','|---|---|---|---|---|---|---:|---:|---|']
    for r in results:
        time=r.get('time',{})
        lines.append(f"| {r['id']} | {', '.join(r['class_ids'])} | {', '.join(r['teacher_codes'])} | {r['subject_id'] or r['fixed_activity']} | {r['day']} | {r['start_period']}–{r['end_period']} | {time.get('jp','?')} | {time.get('kbm_minutes','?')} | {', '.join(str(b['after_jp'])+' ('+str(b['minutes'])+')' for b in time.get('breaks',[])) or '—'} |")
    (OUT/'blok-jadwal.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'summary':summary,'issues':issues,'stacked':[r for r in results if r['stacked_cell']]},ensure_ascii=False,indent=2))
    assert not issues, f'{len(issues)} masalah ekstraksi; lihat blocks.json'
    assert len(results)==sum(p['labels_expected'] for p in pages)
    assert json.loads((OUT/'blocks.json').read_text(encoding='utf-8'))==data
    teacher_codes={t['source_code'] for t in entities['teachers']}
    class_ids={c['id'] for c in entities['classes']}
    assert all(set(r['teacher_codes'])<=teacher_codes and set(r['class_ids'])<=class_ids for r in results)
    def find(source,page,subject,day,start):
        found=[r for r in results if (r['source'],r['page'],r['subject_id'],r['day'],r['start_period'])==(source,page,subject,day,start)]
        assert len(found)==1
        return found[0]
    # Hand-read source cases check geometry, multi-teacher preservation and unequal minutes.
    assert find('kelas',5,'AGM','senin',2)['teacher_codes']==['5','52','53']
    friday=find('kelas',1,'BIG','jumat',7)
    assert friday['end_period']==9 and friday['time']['kbm_minutes']==115
    assert friday['time']['breaks']==[{'after_jp':1,'minutes':90}]
    assert find('guru',39,'PJOK','rabu',5)['class_ids']==['XI-1']
    assert find('guru',39,'PJOK','rabu',7)['class_ids']==['XII-9']
    assert find('guru',45,'MAT','jumat',4)['end_period']==5
    assert find('guru',45,'MAT','jumat',6)['end_period']==7
    recap=[]
    for p in pages:
        subset=[r for r in results if r['source']==p['source'] and r['page']==p['page'] and r['subject_id']]
        recap.append({'source':p['source'],'page':p['page'],'lesson_blocks':len(subset),
            'jp':sum(r['time']['jp'] for r in subset),'kbm_minutes':sum(r['time']['kbm_minutes'] for r in subset)})
    report={'status':'passed_extraction_checks','pages':len(pages),'blocks':len(results),
        'summary':summary,'page_recap':recap,'extraction_errors':issues,
        'checks':['source hashes','one table cell per known label','all known labels represented',
                  'period edge alignment','valid calendar span and fixed activity location',
                  'known identity references','JSON read-back','six visually read geometry/duration cases'],
        'source_findings':[{'type':'overlap_requires_reconciliation','teacher_code':'39',
            'day':'rabu','period':7,'time':'12:15–13:00','class_ids':['XI-1','XII-9'],
            'source_pages':{'guru':[39],'kelas':[10,27]}},
            {'type':'inventory_correction','teacher_code':'45',
             'detail':'Jumat MAT XI-1 periode 4–5 dan XI-3 periode 6–7 adalah sel biasa bersebelahan, bukan bertingkat.'}],
        'limits':'No final event reconciliation or comprehensive HC/SC validation.'}
    (OUT/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__': main()
