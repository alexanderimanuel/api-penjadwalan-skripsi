"""Independent delivery audit of corrected warm-start recommendations."""
import hashlib
import json
import sys
from collections import Counter,defaultdict
from pathlib import Path
from itertools import combinations
from export_august import read_json
from final_experiment import atomic_json,atomic_text
from final_validator import validate
from school_experiment import select_school_inputs
from sa_feasible_recommendations import groups,distance

ROOT=Path(__file__).resolve().parents[1]
def main():
    output=ROOT/'results/sa-feasible-hc7hc8/v2'
    model,_=select_school_inputs(read_json(ROOT/'config/school-experiment.hc7hc8.v2.json'))
    reports=[];positions={}
    for label in 'ABC':
        artifact=read_json(output/f'recommendation-{label}.json');rows=artifact['timetable']
        assert artifact['dataset_hash']==model.dataset_hash and artifact['rules_hash']==model.rules_hash
        q=validate(model,rows);assert q['feasible'],q['HC']
        assert Counter(r['event_id'] for r in rows)==Counter(model.ids)
        used=defaultdict(set)
        for row in rows:
            for c in row['class_ids']:
                span=set(range(row['start_period'],row['end_period']+1))
                used[c,row['day']].update(span)
                if row['day']=='jumat':assert max(span)<=(9 if c.split('-')[0]=='X' else 7)
        occupancy=[]
        for (c,day),periods in sorted(used.items()):
            teaching=sorted(s['period'] for s in model.days[day]['segments'] if s['kind']=='kbm')
            assert min(periods)==teaching[0],(c,day,'late start')
            assert periods=={p for p in teaching if p<=max(periods)},(c,day,'empty teaching slot')
            occupancy.append({'class':c,'day':day,'first':min(periods),'last':max(periods),'occupied':sorted(periods)})
        assert len(used)==27*5,'A school class/day is missing entirely'
        positions[label]={r['event_id']:(r['day'],r['start_period']) for r in rows}
        reports.append({'label':label,'seed':artifact['seed'],'HC':q['HC'],'S':q['S'],'raw':q['raw'],'class_days_checked':len(used),'class_day_occupancy':occupancy})
    pairwise=[{'a':a,'b':b,'distance':distance(groups(model),positions[a],positions[b])} for a,b in combinations('ABC',2)]
    assert all(p['distance']>=26 for p in pairwise)
    command='python scripts/run_hc7_warmstart.py --protocol config/school-experiment.hc7hc8.v2.json --config config/sa-feasible-hc7hc8.v2.json --repair results/final-diagnostics/baseline-hc7hc8-v2/repair-candidate.json --output results/sa-feasible-hc7hc8/v2'
    files=['scripts/final_rules.py','scripts/final_validator.py','scripts/sa_feasible_recommendations.py','scripts/run_hc7_warmstart.py','config/final-school.hc7hc8.v2.json','config/sa-feasible-hc7hc8.v2.json','config/school-experiment.hc7hc8.v2.json']
    atomic_json(output/'delivery-audit.json',{'recommendations':reports,'pairwise_distance':pairwise,'command':command,'python':sys.version,'file_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files}})
    lines=['# Rekomendasi warm-start dengan HC7 dan HC8 yang dikoreksi','','Semua 513 event dipertahankan. Setiap rekomendasi telah divalidasi ulang untuk HC1-HC8 dan diperiksa langsung pada 135 kombinasi kelas/hari.','','- HC7: Jumat kelas X berakhir maksimal JP9; XI/XII maksimal JP7.','- HC8: setiap kelas mulai pada slot KBM pertama, tanpa slot KBM kosong sampai akhir pelajaran; istirahat/kegiatan tetap dikecualikan.','- SC1-SC3, bobot, kategori difficulty, dan normalisasi tidak diubah.','','| Rekomendasi | Seed | H | S | SC1 | SC2 | SC3 |','|---|---:|---:|---:|---:|---:|---:|']
    for r in reports:lines.append(f"| {r['label']} | {r['seed']} | 0 | {r['S']:.9f} | {r['raw']['SC1']} | {r['raw']['SC2']} | {r['raw']['SC3']} |")
    lines+=['','Jarak antarrekomendasi: '+', '.join(f"{p['a']}-{p['b']} = {p['distance']}" for p in pairwise)+'.','','Ini hasil SA warm-start terpisah, bukan hasil 180 run konstruktif. Hasil lama tidak membuktikan kelayakan terhadap HC7/HC8 yang sekarang. Jadwal awal diperoleh melalui perbaikan terbatas baseline, bukan dianggap keluaran SA.','','Pengujian: 4 tes HC8 baru dan 5 tes warm-start lulus. Suite scripts lengkap: 172 tes, 1 failure dan 6 errors; rincian di analysis/hc7hc8-test-results.txt. Kegagalan terkait fixture lama HC8 serta guard kode frozen lama; tidak dilewati untuk mengklaim semua tes lulus.','','## Reproduksi','',command,'','Audit ulang: python scripts/audit_hc7hc8_delivery.py']
    atomic_text(output/'README.md','\n'.join(lines)+'\n')
    print(json.dumps({'recommendations':[{k:v for k,v in r.items() if k!='class_day_occupancy'} for r in reports],'distances':pairwise},indent=2))
if __name__=='__main__':main()
