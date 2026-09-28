"""Generate a coded, static school-review package without editing schedules."""
import argparse
from collections import Counter
import copy
import html
import json
from pathlib import Path

from export_august import read_json
from final_experiment import atomic_json, atomic_text, digest, load_frozen
from final_foundation import thaw
from final_impact import build_report, csv_table as source_csv_table, metrics
from final_rules import FinalModel, stable_hash
from final_validator import validate
from sa_feasible_recommendations import groups, distance, search, select
from diagnose_school_search import repair_baseline
from model_august import ROOT

COHORT=ROOT/'results/sa-feasible-recommendations/v1'
PILOT=ROOT/'results/final-pilot/school-user-approved-v1'
LABELS=['BASELINE','A','B','C']
REVIEW_FIELDS=['reviewer_role','reviewer_code','clarity','schedule_suitability',
               'high_subject_distribution_suitability','usefulness_for_decision_making',
               'final_choice','reasons','unmodeled_rules','required_changes']


def csv_table(rows):
    # Atomic text output on Windows performs newline translation itself.
    return source_csv_table(rows).replace('\r\n','\n')
CSS='''body{font:14px Arial,sans-serif;color:#182b43;margin:26px;line-height:1.4}h1{font-size:24px}h2{font-size:19px}a{color:#244c9b}table{width:100%;border-collapse:collapse;margin:12px 0 24px}th{background:#253d61;color:white}th,td{padding:8px;border:1px solid #d2dae4;vertical-align:top}td p{margin:2px 0}.break{background:#eef0f3;color:#515b68}.fixed{background:#e8def6}.conflict{background:#ffe0dc;color:#952a23}.lesson{background:#eff5ff}.empty{background:white}.small{font-size:11px;color:#4f5d71}.legend span{display:inline-block;padding:5px 12px;margin:3px}.week{table-layout:fixed}.week td{height:36px;font-size:11px}.week th:first-child{width:52px}.resource{break-after:page}.nav a{margin-right:10px;display:inline-block}.input{height:35px;background:#fff7db}textarea{width:98%;height:70px}@page{size:A4 landscape;margin:10mm}@media print{body{margin:0;font-size:11px}.nav,.print-hide{display:none}h1{font-size:18px}h2{font-size:16px}th{-webkit-print-color-adjust:exact;print-color-adjust:exact}td{-webkit-print-color-adjust:exact;print-color-adjust:exact}.week td{font-size:9px;height:29px;padding:4px}.resource{break-inside:avoid}.resource:last-child{break-after:auto}}'''


def page(title,body):
    return '<!doctype html><html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>'+CSS+'</style></head><body>'+body+'</body></html>'


def teacher_codes(model):
    return {t['source_code']:t['id'] if 'id' in t else f'G{i:03d}' for i,t in enumerate(model.data['teachers'],1)}


def redact_score(score,codes):
    value=copy.deepcopy(score)
    for item in value['details']['SC1']:item['teacher_id']=codes[item['teacher_id']]
    for item in value['details']['HC1']:item['shared_ids']=[codes[t] for t in item['shared_ids']]
    return value


def grid(model,x,kind,resource,codes):
    field='class_ids' if kind=='kelas' else 'teacher_codes'
    events=[(eid,model.events[eid],pos) for eid,pos in x.items() if resource in model.events[eid][field]]
    days=list(model.days);columns=[];flat=[]
    for day in days:
        cells=[]
        for index,s in enumerate(model.days[day]['segments']):
            matches=[(eid,e) for eid,e,pos in events if s['kind']=='kbm' and pos[0]==day and pos[1]<=s['period']<pos[1]+e['time']['jp']]
            state=s['kind'] if s['kind']!='kbm' else 'conflict' if len(matches)>1 else 'lesson' if matches else 'empty'
            slot='JP'+str(s['period']) if s['period'] is not None else 'JEDA'
            clock=lambda n:f'{n//60:02d}:{n%60:02d}'
            actual=f"{clock(s['start_minute'])}–{clock(s['end_minute'])}"
            if s['kind']=='fixed':activity=s['source_label']+' [KEGIATAN TETAP]'
            elif s['kind']=='break':activity=s['source_label']+' [ISTIRAHAT]'
            elif not matches:activity='Kosong KBM'
            else:activity='\n'.join(e['subject_id']+' / '+(','.join(codes[t] for t in e['teacher_codes']) if kind=='kelas' else ','.join(e['class_ids'])) for _,e in matches)
            if state=='conflict':activity='BENTROK\n'+activity
            text=slot+' · '+actual+'\n'+activity
            cells.append({'text':text,'state':state})
            flat.append({'resource_type':kind,'resource_id':resource if kind=='kelas' else codes[resource],
                         'day':day,'segment_index':index+1,'period':s['period'],'start_time':clock(s['start_minute']),
                         'end_time':clock(s['end_minute']),'segment_kind':s['kind'],'occupancy_state':state,
                         'calendar_activity':s['source_label'],'event_ids':[eid for eid,_ in matches],
                         'subjects':[e['subject_id'] for _,e in matches],
                         'teacher_ids':sorted({codes[t] for _,e in matches for t in e['teacher_codes']}),
                         'class_ids':sorted({c for _,e in matches for c in e['class_ids']})})
        columns.append(cells)
    rows=[]
    for i in range(max(map(len,columns))):
        rows.append([{'text':str(i+1),'state':'label'},*[col[i] if i<len(col) else {'text':'—','state':'empty'} for col in columns]])
    return {'resource_id':resource if kind=='kelas' else codes[resource], 'headers':['Urutan',*[model.days[d].get('name',d.title()) for d in days]],'rows':rows},flat


def schedule_html(label,kind,grids):
    body=f'<a class="print-hide" href="../index.html">Kembali ke ringkasan</a><h1>{html.escape(label)} — jadwal per {kind}</h1>'
    body+='<p>Setiap sel mencantumkan JP dan waktu sebenarnya. Baris menunjukkan urutan segmen kalender masing-masing hari.</p><p class="small">Kegiatan tetap adalah kalender sekolah; tampilan pada jadwal guru tidak menyatakan penugasan individual untuk menghadirinya. Kosong KBM bukan bukti guru bebas dari tugas lain.</p>'
    body+='<div class="legend"><span class="lesson">Pelajaran</span><span class="break">Istirahat</span><span class="fixed">Kegiatan tetap</span><span class="conflict">Bentrok sumber</span></div><p class="nav">'
    body+=' '.join(f'<a href="#{html.escape(g["resource_id"])}">{html.escape(g["resource_id"])}</a>' for g in grids)+'</p>'
    for g in grids:
        body+=f'<section class="resource" id="{html.escape(g["resource_id"])}"><h2>{html.escape(label)} / {html.escape(g["resource_id"])}</h2><table class="week"><tr>'
        body+=''.join('<th>'+html.escape(h)+'</th>' for h in g['headers'])+'</tr>'
        for row in g['rows']:
            body+='<tr>'+''.join(f'<td class="{c["state"]}">'+html.escape(c['text']).replace('\n','<br>')+'</td>' for c in row)+'</tr>'
        body+='</table></section>'
    return page(f'{label} jadwal {kind}',body)


def load_data(recompute=None):
    model,frozen,_,_=load_frozen(PILOT/'FROZEN_EXPERIMENT_CONFIG.json')
    manifest=read_json(COHORT/'method-snapshot.json')
    if digest(ROOT/'scripts/sa_feasible_recommendations.py')!=manifest['code_hash']:raise ValueError('SA source changed')
    selection=read_json(COHORT/'selection.json');runs={};reproduction={'mode':'VALIDATED_SAVED_SA_RESULTS'}
    if recompute:
        from reproduce_school_inputs import reproduce
        data=reproduce(Path(recompute)/'raw',model.dataset_hash)
        model=FinalModel(data,thaw(model.rules));repair=repair_baseline(model)['selected']
        if repair is None or stable_hash(repair['placement'])!=manifest['initial_hash']:raise ValueError('Regenerated repair differs')
        recreated=[]
        for seed in read_json(COHORT/'progress.json')['completed_seeds']:
            print(f'Regenerate SA seed {seed} from PDF-derived data',flush=True)
            result=search(model,repair['placement'],manifest['config'],seed)
            old=read_json(COHORT/f'seed-{seed}/result.json')
            for key in ('best_feasible','best_feasible_quality','initial_hash','search_log','T0','stop_reason'):
                if stable_hash(result[key])!=stable_hash(old[key]):raise ValueError('Seed reproduction differs: '+key)
            recreated.append(result)
            atomic_json(Path(recompute)/f'seed-{seed}.json',result)
        if stable_hash(select(model,recreated,repair['placement'],manifest['config']))!=stable_hash(selection):raise ValueError('Regenerated selection differs')
        reproduction={'mode':'RAW_PDF_AND_SA_REGENERATED','dataset_hash_identical':True,'initial_identical':True,
                      'seeds':[r['seed'] for r in recreated],'search_logs_and_selected_placements_identical':True}
    schedules={'BASELINE':{eid:(e['reference']['day'],e['reference']['start_period']) for eid,e in model.events.items()}}
    sources={'BASELINE':{'source':'PDF jadwal kelas dan guru berlaku 3 Agustus 2026','run_id':None,'seed':None,'config':None}}
    for label,item in zip('ABC',selection['recommendations']):
        seed=item['seed'];run=read_json(COHORT/f'seed-{seed}/result.json');runs[label]=run
        rec=read_json(COHORT/f'recommendation-{label}.json')
        if run['dataset_hash']!=model.dataset_hash or run['rules_hash']!=model.rules_hash:raise ValueError('Run input mismatch')
        if stable_hash(run['best_feasible'])!=stable_hash(item['solution']):raise ValueError('Selection changed')
        if rec['timetable']!=model.timetable(run['best_feasible']):raise ValueError('Export differs from SA')
        schedules[label]={eid:tuple(p) for eid,p in item['solution'].items()}
        sources[label]={'source':'SA feasible warm start','run_id':f'sa-feasible-warm-start-v1-seed-{seed}',
                        'seed':seed,'config':run['configuration'],'evaluations':run['evaluations'],'stop_reason':run['stop_reason']}
    return model,schedules,sources,manifest,reproduction


def build(output,recompute=None):
    output=Path(output)
    if output.exists():raise ValueError('New output directory required; reviewer responses must not be overwritten')
    model,schedules,sources,manifest,reproduction=load_data(recompute)
    codes=teacher_codes(model)
    recs=[{'label':label,'dataset_hash':model.dataset_hash,'rules_hash':model.rules_hash,'timetable':model.timetable(x)} for label,x in schedules.items() if label!='BASELINE']
    impact=build_report(model,recs)
    for label,q in impact['evaluations'].items():
        if label!='BASELINE' and not q['feasible']:raise ValueError('Invalid recommendation')
    output.mkdir(parents=True)
    metadata={'dataset_version':model.data['id'],'dataset_hash':model.dataset_hash,'source_snapshot_version':'2026-08-03-work-v1',
              'rules_version':model.rules['rules_version'],'rules_hash':model.rules_hash,
              'difficulty_version':model.rules['difficulty']['version'],'difficulty_source':model.rules['difficulty']['source'],
              'weights':thaw(model.rules['weights']),'daily_limit':model.rules['difficulty']['daily_limit'],
              'normalization':thaw(model.rules['normalization']),'method_version':manifest['config']['method_version'],
              'source_runs':sources,'calendar_effective_date':'2026-08-03','teacher_identifiers':'Kode Gxxx konsisten; nama guru tidak disertakan.',
              'scope':'Rekomendasi penelitian versi SA warm start feasible; bukan 180 run main experiment.',
              'assumptions':'Model kelompok AGM dan difficulty mengikuti keputusan penelitian yang disetujui pengguna; bukan bukti wawancara atau konfirmasi sekolah.',
              'ranking':'Urutan algoritmik berdasarkan S pada model, bukan pernyataan terbaik untuk sekolah.'}
    atomic_json(output/'metadata.json',metadata);atomic_json(output/'reproduction.json',reproduction)
    grouped=groups(model);distances={a:{b:distance(grouped,schedules[a],schedules[b]) for b in schedules} for a in schedules}
    values={label:{**metrics(q),'feasible':q['feasible'],'n_events':len(model.ids),'jp':sum(e['time']['jp'] for e in model.events.values())} for label,q in impact['evaluations'].items()}
    comparison=[{'metric':key,**{label:values[label][key] for label in schedules}} for key in values['BASELINE']]
    atomic_text(output/'comparison.csv',csv_table(comparison))
    changes=impact['comparisons']['metrics'];atomic_text(output/'comparison-changes.csv',csv_table(changes))
    atomic_json(output/'distances.json',distances)
    atomic_json(output/'hc-breakdown.json',{label:redact_score(q,codes)['details']|{'hard':q['hard']} for label,q in impact['evaluations'].items()})
    # Keep detailed research indicators free of personal names and source excerpts.
    detail=copy.deepcopy(impact['details'])
    for tables in detail.values():
        for name,rows in tables.items():
            if name.startswith('sc1_'):
                for row in rows:row['teacher_id']=codes[row['teacher_id']]
    atomic_json(output/'impact-details.json',detail)
    workbook={'metadata':metadata,'metrics':values,'distances':distances,'grids':{},'changes':changes,'teacher_distribution':impact['teacher_gap_change_distribution']}
    all_codes=[t['source_code'] for t in model.data['teachers']];classes=sorted({c for e in model.events.values() for c in e['class_ids']})
    for label,x in schedules.items():
        folder=output/label;folder.mkdir()
        public_rows=[]
        for row in model.timetable(x):
            public_rows.append({k:([codes[t] for t in v] if k=='teacher_codes' else v) for k,v in row.items() if k!='source_metadata'})
        atomic_json(folder/'timetable.json',public_rows)
        atomic_json(folder/'quality.json',redact_score(impact['evaluations'][label],codes))
        atomic_json(folder/'metadata.json',{**metadata,'source_run':sources[label]})
        workbook['grids'][label]={}
        for kind,resources in [('kelas',classes),('guru',all_codes)]:
            grids=[];flat=[]
            for resource in resources:
                g,records=grid(model,x,kind,resource,codes);grids.append(g);flat.extend(records)
            workbook['grids'][label][kind]=grids
            atomic_text(folder/f'jadwal-{kind}.html',schedule_html(label,kind,grids))
            atomic_text(folder/f'jadwal-{kind}.csv',csv_table(flat))
            # Coverage proof: every teaching event appears for its complete JP span.
            actual=Counter(eid for r in flat for eid in r['event_ids'])
            expected={eid:e['time']['jp']*len(e['class_ids'] if kind=='kelas' else e['teacher_codes']) for eid,e in model.events.items()}
            if dict(actual)!=expected:raise ValueError('Schedule view coverage mismatch')
    reviewer={'reviewer_role':None,'reviewer_code':None,'ratings':[{'schedule':label,**{k:None for k in REVIEW_FIELDS[2:6]}} for label in schedules],
              'final_choice':None,'reasons':None,'unmodeled_rules':None,'required_changes':None}
    atomic_json(output/'school-review-input.json',reviewer)
    atomic_json(output/'school-review-schema.json',{'ratings':'1,2,3,4 or N/A; blank means not reviewed',
               'clarity':'1 sulit dipahami; 2 kurang jelas; 3 jelas; 4 sangat jelas',
               'schedule_suitability':'1 tidak sesuai; 2 kurang sesuai; 3 sesuai; 4 sangat sesuai',
               'high_subject_distribution_suitability':'1 tidak sesuai; 2 kurang sesuai; 3 sesuai; 4 sangat sesuai',
               'usefulness_for_decision_making':'1 tidak membantu; 2 kurang membantu; 3 membantu; 4 sangat membantu',
               'final_choice':['baseline','A','B','C','none'],'unfilled_values':None,
               'note':'Skala untuk formulir peninjauan ini, bukan data jawaban atau instrumen tervalidasi yang diklaim sudah diisi.'})
    atomic_text(output/'school-review-ratings.csv',csv_table([{'reviewer_role':'','reviewer_code':'',**{k:('' if v is None else v) for k,v in r.items()}} for r in reviewer['ratings']]))
    atomic_text(output/'school-review-decision.csv',csv_table([{k:'' for k in ['reviewer_role','reviewer_code','final_choice','reasons','unmodeled_rules','required_changes']}]))
    body='<h1>Formulir peninjauan jadwal</h1><p>SMA Negeri 8 Malang · kode guru tanpa nama</p><p>Peran peninjau: ____________________ &nbsp; Kode peninjau: ____________________</p><p>Isi 1–4 atau N/A jika belum dapat menilai. Kosong berarti belum dinilai. Kejelasan: sulit → sangat jelas; kesesuaian: tidak sesuai → sangat sesuai; kegunaan: tidak membantu → sangat membantu.</p><table><tr><th>Jadwal</th><th>Kejelasan</th><th>Kesesuaian jadwal</th><th>Kesesuaian distribusi HIGH</th><th>Kegunaan untuk keputusan</th></tr>'
    body+=''.join('<tr><th>'+label+'</th>'+('<td class="input"></td>'*4)+'</tr>' for label in schedules)+'</table><p>Pilihan akhir (pilih satu): □ baseline &nbsp; □ A &nbsp; □ B &nbsp; □ C &nbsp; □ none</p>'
    for text in ['Alasan pilihan','Aturan yang belum dimodelkan','Perubahan yang diperlukan']:body+='<h3>'+text+'</h3><div style="height:60px;border-bottom:1px solid #ccc"></div>'
    atomic_text(output/'school-review-form.html',page('Formulir peninjauan',body))
    body='<h1>Peninjauan jadwal SMA Negeri 8 Malang</h1><p>Baseline 3 Agustus 2026 dan tiga rekomendasi SA. Pilihan akhir diserahkan kepada peninjau sekolah.</p><p><a href="Review-SMAN8.xlsx">Buka Excel</a> · <a href="school-review-form.html">Formulir cetak</a> · <a href="school-review-input.json">Formulir JSON kosong</a></p><h2>Perbandingan kualitas</h2><table><tr><th>Metrik</th>'+''.join('<th>'+label+'</th>' for label in schedules)+'</tr>'
    for key in ['H','feasible','SC1','SC2','SC3','normalized_SC1','normalized_SC2','normalized_SC3','S','F']:
        body+='<tr><th>'+key+'</th>'+''.join('<td>'+str(round(values[label][key],9) if isinstance(values[label][key],float) else values[label][key])+'</td>' for label in schedules)+'</tr>'
    body+='</table><p>H adalah total pelanggaran hard constraints; nilai lebih rendah lebih baik. S adalah skor soft ternormalisasi berbobot. Peringkat berdasarkan S hanya berlaku pada model ini.</p><p>Baseline memiliki satu bentrok guru. Semua rekomendasi memiliki HC1–HC7=0. Total S yang lebih kecil tidak berarti setiap guru/kelas membaik.</p><h2>Jadwal lengkap</h2><table><tr><th>Jadwal</th><th>Per kelas</th><th>Per guru</th></tr>'
    for label in schedules:body+=f'<tr><th>{label}</th><td><a href="{label}/jadwal-kelas.html">27 kelas</a></td><td><a href="{label}/jadwal-guru.html">58 kode guru</a></td></tr>'
    body+='</table><h2>Perubahan dan trade-off</h2>'
    facts={}
    for label in schedules:
        if label=='BASELINE':continue
        q=values[label];base=values['BASELINE'];counts=impact['teacher_gap_change_distribution'][label]
        text=f"S {q['S']:.6f}; SC1 {base['SC1']} menjadi {q['SC1']}; SC2 {base['SC2']} menjadi {q['SC2']}; SC3 {base['SC3']} menjadi {q['SC3']}. Guru dengan gap berkurang {counts['DECREASED']}, tetap {counts['UNCHANGED']}, bertambah {counts['INCREASED']}."
        facts[label]=text;body+='<h3>'+label+'</h3><p>'+text+'</p>'
    body+='<p>SC2 meningkat dari baseline nol: persentase perubahan N/A. Tidak dibuat klaim prestasi, kelelahan siswa, atau percepatan waktu kerja sekolah.</p><h2>Jarak antarrekomendasi</h2><table><tr><th>Pasangan</th><th>Penempatan berbeda</th><th>Persentase</th></tr>'
    for a,b in [('A','B'),('A','C'),('B','C')]:body+=f'<tr><td>{a}–{b}</td><td>{distances[a][b]}</td><td>{100*distances[a][b]/len(model.ids):.2f}%</td></tr>'
    body+='</table><p>Jarak memperhitungkan event identik sebagai multiset, bukan hanya pertukaran ID.</p><h2>Provenansi dan batas penggunaan</h2><p>'+html.escape(metadata['scope']+' '+metadata['assumptions'])+'</p><p><a href="metadata.json">Metadata dataset, rules, difficulty, weights, run/config/seed</a> · <a href="hc-breakdown.json">Bukti HC</a> · <a href="comparison-changes.csv">Tabel selisih</a> · <a href="REGENERATE.md">Regenerasi</a></p>'
    atomic_text(output/'index.html',page('Paket peninjauan SMA Negeri 8 Malang',body))
    workbook['facts']=facts
    atomic_json(output/'workbook-data.json',workbook)
    atomic_text(output/'REGENERATE.md', '# Regenerasi paket\n\nTidak perlu mengedit timetable. Dari workspace penelitian, jalankan builder Python ke direktori baru, lalu builder Excel JS dengan input workbook-data.json.\n\n```powershell\npython scripts/build_school_review.py --output OUTPUT_BARU\nnode scripts/build_school_review_workbook.mjs OUTPUT_BARU\n```\n\nUntuk mengulang dari PDF asli: tambahkan `--recompute PRIVATE_WORKDIR_BARU` pada perintah Python. Program mengekstrak dua PDF sumber, membangun kalender, merekonsiliasi event, membangun ulang repair initial, menjalankan seed 2000–2002 dan memeriksa kesamaan hash dataset, initial, trajectory SA, serta hasil seleksi. Jalankan builder Excel setelahnya. Dependencies: runtime Python dan Node workspace yang sama, pdfplumber, @oai/artifact-tool. Gunakan direktori keluaran baru agar jawaban peninjau tidak tertimpa.\n\nPDF dan data bernama tetap berada di workspace penelitian dan tidak dibundel pada paket sekolah. Paket menggunakan kode Gxxx. Kode tetap memungkinkan pihak sekolah mengaitkan jadwal dengan pengampu; ini pseudonimisasi, bukan anonimitas absolut.\n\nHalaman HTML dapat dibuka offline atau dicetak landscape. Formulir kosong tersedia dalam Excel/HTML/CSV/JSON; tidak ada jawaban peninjau yang diisi oleh program.\n')
    atomic_json(output/'validation.json',{'status':'PASS','fresh_final_validation':{label:q['hard'] for label,q in impact['evaluations'].items()},
                'event_counts':{label:len(x) for label,x in schedules.items()},'teacher_views':len(all_codes),'class_views':len(classes),
                'all_view_jp_coverage':'PASS','teacher_names_exported':False,'reproduction':reproduction,
                'source_method_snapshot_hash':digest(COHORT/'method-snapshot.json')})
    print('School review package generated:',output.resolve(),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);parser.add_argument('--recompute')
    args=parser.parse_args();build(args.output,args.recompute)


if __name__=='__main__':main()
