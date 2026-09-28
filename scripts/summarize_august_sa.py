"""Laporan pilot tahap 9 dari hasil yang telah diverifikasi, bukan eksperimen utama."""
from pathlib import Path
import hashlib
import json
from model_august import ROOT,SNAPSHOT

BASE=ROOT/'results/august_sa_stage09'

def main():
    dataset=json.loads((SNAPSHOT/'data/reconciliation/2026-08-03/candidate-dataset.json').read_text(encoding='utf-8'))
    events={e['id']:e for e in dataset['events']}
    original={eid:{k:e['reference'][k] for k in ['day','start_period']} for eid,e in events.items()}
    source_score=json.loads((ROOT/'data/model/2026-08-03-model-v1/reference-score.json').read_text(encoding='utf-8'))
    rows=[]
    for seed in [0,1]:
        folder=BASE/f'verified-seed{seed}'
        run=json.loads((folder/'run.json').read_text(encoding='utf-8'))
        if hashlib.sha256((ROOT/'scripts/anneal_august.py').read_bytes()).hexdigest()!=run['input_sha256']['scripts/anneal_august.py']:
            raise ValueError('Pilot does not match current SA code')
        verification=json.loads((folder/'verification.json').read_text(encoding='utf-8'))
        best=json.loads((folder/'best-solution.json').read_text(encoding='utf-8'))
        initial=json.loads((folder/'sa_initial-solution.json').read_text(encoding='utf-8'))
        assert run['verified_by_reference_evaluator'] and verification['best']['feasible']
        changes=[]
        lines=[f'# Perubahan solusi terbaik — seed {seed}', '',
               'Solusi komputasional model-v1; belum rekomendasi operasional yang disahkan sekolah. JP, menit, pola jeda dan penugasan tetap.', '',
               '| Event | Kelas | Mapel | Guru | Jadwal sumber | Solusi terbaik |',
               '|---|---|---|---|---|---|']
        for eid,p in best.items():
            if p==original[eid]:continue
            e=events[eid];changes.append({'event_id':eid,'original':original[eid],'best':p})
            def label(pos):return f"{pos['day']} {pos['start_period']}–{pos['start_period']+e['time']['jp']-1}"
            lines.append(f"| {eid} | {', '.join(e['class_ids'])} | {e['subject_id']} | {', '.join(e['teacher_codes'])} | {label(original[eid])} | {label(p)} |")
        (folder/'changes.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        rows.append({'seed':seed,'score':run['best_score'],'changed_events':len(changes),
            'initial_changed_events':sum(initial[eid]!=original[eid] for eid in initial),
            'initial_score':run['sa_initial_score'],'iterations':run['iterations'],
            'sa_evaluations':run['counters']['sa_evaluations'],'repair_evaluations':run['initialization']['evaluations'],
            'search_seconds':run['search_seconds'],'verification':'passed','changes':changes})
    result={'scope':'Two implementation pilots; not main experiments; parallel runtime not a speed benchmark',
            'reference_score':{k:source_score[k] for k in ['H','SC1','SC2','S']},'runs':rows}
    (BASE/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# Pilot implementasi SA Agustus — tahap 9','',
        'Seluruh hasil menggunakan asumsi model-v1. Kedua run dijalankan bersamaan; waktunya bukan tolok ukur performa serial.','',
        '| Tahap / seed | H | SC1 | SC2 | S | Event berubah dari sumber |','|---|---:|---:|---:|---:|---:|',
        '| Jadwal sumber | 1 | 246 | 0 | 246 | 0 |',
        '| Setelah repair awal | 0 | 245 | 0 | 245 | 2 |']
    for r in rows:
        s=r['score'];lines.append(f"| SA seed {r['seed']} | {s['H']} | {s['SC1']} | {s['SC2']} | {s['S']} | {r['changed_events']} |")
    lines+=['','Repair awal merupakan tahap deterministik terpisah. Keberhasilan memperoleh H=0 terjadi sebelum iterasi SA dan tidak diklaim sebagai hasil SA sendiri.',
             '', 'Seed 1 memiliki S lebih kecil dengan bobot 1:1, tetapi SC2 lebih besar. Tidak ada klaim optimalitas atau rekomendasi parameter dari dua run ini.',
             '', 'Setiap folder verified-seed menyimpan solusi awal SA, terbaik, akhir, terbaik layak, jejak pencarian, verifikasi evaluator, metadata, dan daftar perubahan.']
    (BASE/'summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Pilot summary and changes verified and written.')

if __name__=='__main__':main()
