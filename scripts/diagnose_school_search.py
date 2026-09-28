"""Bounded baseline repair and read-only diagnosis; never an SA main run."""
import argparse
import copy
import math
from pathlib import Path

from export_august import read_json
from final_experiment import atomic_json, atomic_text, load_frozen
from final_impact import change, csv_table, metrics, detailed
from final_rules import stable_hash
from final_sa import score_equal
from final_validator import validate
from model_august import ROOT


def repair_baseline(model):
    baseline = {eid:(e['reference']['day'],e['reference']['start_period']) for eid,e in model.events.items()}
    before = copy.deepcopy(baseline)
    score = validate(model, model.timetable(baseline))
    if score['validation_status'] != 'VALID_TIMETABLE': raise ValueError('Baseline is structurally invalid')
    involved = {eid for h in ('HC1','HC2') for item in score['details'][h] for eid in item['event_ids']}
    tested = []; feasible = []; seen = {stable_hash(baseline)}
    def check(candidate, operator, event_ids):
        key = stable_hash(candidate)
        if key in seen: return
        seen.add(key)
        quality = model.score(candidate)
        record = {'operator':operator, 'event_ids':event_ids, 'placement_hash':key,
                  'changed_event_count':sum(candidate[e]!=baseline[e] for e in baseline),
                  'H':quality['H'], 'S':quality['S'], 'F':quality['F']}
        tested.append(record)
        if quality['feasible']:
            checked = validate(model, model.timetable(candidate))
            if not checked['feasible'] or not score_equal(checked, quality):
                raise ValueError('Repair fast score and independent validation disagree')
            feasible.append({**record, 'placement':candidate, 'quality':checked})
    if score['feasible']:
        feasible.append({'operator':'baseline_already_feasible','event_ids':[], 'placement_hash':stable_hash(baseline),
                         'changed_event_count':0,'H':0,'S':score['S'],'F':score['F'],'placement':baseline.copy(),'quality':score})
    for eid in sorted(involved):
        for position in model.domains[eid]:
            check({**baseline,eid:tuple(position)}, 'move', [eid])
    for a,b in model.swap_pairs:
        if not {a,b}&involved or baseline[a]==baseline[b]: continue
        if baseline[b] in model.allowed[a] and baseline[a] in model.allowed[b]:
            candidate=baseline.copy();candidate[a],candidate[b]=baseline[b],baseline[a]
            check(candidate,'swap',[a,b])
    feasible.sort(key=lambda r:(r['changed_event_count'],r['S'],r['placement_hash']))
    if baseline != before: raise ValueError('Baseline mutated during repair')
    return {'purpose':'BASELINE_REPAIR_DIAGNOSTIC_NOT_SA', 'baseline':baseline,'baseline_quality':score,
            'search_scope':'All legal one-event moves and compatible two-event swaps involving a baseline collision event; no SA or parameter tuning.',
            'ranking':'Fewest changed events, then smallest S, then deterministic placement hash.',
            'tested_candidates':tested, 'feasible_candidate_count':len(feasible),
            'selected':feasible[0] if feasible else None}


def diagnose_run(model, result):
    if result['dataset_hash']!=model.dataset_hash or result['rules_hash']!=model.rules_hash:
        raise ValueError('Pilot input identity mismatch')
    cfg=result['configuration']; rows=[dict(zip(result['search_log']['columns'],v)) for v in result['search_log']['rows']]
    anomalies=[]; draws=[]; accepted_worse=0; improvements=0; best=result['initial_quality']['F']; stale=0
    for i,r in enumerate(rows):
        expected_t=result['T0']*cfg['alpha']**(i//cfg['L'])
        if not math.isclose(r['temperature'],expected_t,rel_tol=1e-12): anomalies.append('cooling mismatch')
        probability=1.0 if r['delta']<=0 else math.exp(-r['delta']/r['temperature'])
        if not math.isclose(r['acceptance_probability'],probability,rel_tol=1e-12,abs_tol=1e-15):anomalies.append('Metropolis probability mismatch')
        if r['delta']>0:
            draw=r['random_draw']
            if not isinstance(draw,(int,float)) or not 0<=draw<1 or r['accepted']!=(draw<probability):anomalies.append('Metropolis draw/decision mismatch')
            draws.append(draw);accepted_worse+=int(r['accepted'])
        elif not r['accepted']:anomalies.append('Nonpositive delta rejected')
        stale+=1
        if r['current_F']<best:
            best=r['current_F'];stale=0;improvements+=1
        if not math.isclose(r['best_F'],best,abs_tol=1e-12):anomalies.append('Best tracking mismatch')
    positive=[r[2] for r in result['calibration']['sample_log']['rows'] if r[2]>0]
    expected_t0=-(sum(positive)/len(positive))/math.log(.8) if positive else 1.0
    if not math.isclose(result['T0'],expected_t0,rel_tol=1e-12):anomalies.append('Calibration formula mismatch')
    sizes=[r['domain_size'] for r in result['construction_trace']]
    if sizes!=sorted(sizes):anomalies.append('Constructor domain order mismatch')
    if result['stop_reason']=='STAGNATION' and stale!=cfg['stagnation']:anomalies.append('Stagnation counter mismatch')
    if stale!=result['stagnation_search_evaluations']:anomalies.append('Recorded stagnation mismatch')
    for state,quality in [('initial','initial_quality'),('best','best_quality'),('current','current_quality')]:
        independent=validate(model,model.timetable(result[state]))
        if not score_equal(independent,result[quality]):anomalies.append(state+' final validation mismatch')
    # Diagnostic scale only; not a proposed or applied parameter change.
    threshold=-2/math.log(.05)
    cooling_to_threshold=max(0,math.ceil(math.log(threshold/result['T0'])/math.log(cfg['alpha'])))
    blocks=[]
    for start in range(0,len(rows),cfg['L']):
        block=rows[start:start+cfg['L']]
        blocks.append({'seed':result['seed'],'temperature_block':start//cfg['L']+1,
                       'search_start':start+1,'search_end':start+len(block),'temperature':block[0]['temperature'],
                       'min_current_H':min(r['current_H'] for r in block),
                       'max_current_H':max(r['current_H'] for r in block),
                       'best_H':block[-1]['best_H'],'best_F':block[-1]['best_F'],
                       'accepted_worse':sum(r['accepted'] and r['delta']>0 for r in block)})
    return {'seed':result['seed'],'configuration':cfg,'initial_H':result['initial_quality']['H'],
            'best_H':result['best_quality']['H'],'final_current_H':result['current_quality']['H'],
            'min_search_current_H':min((r['current_H'] for r in rows),default=None),
            'max_search_current_H':max((r['current_H'] for r in rows),default=None),
            'best_improvements':improvements,'T0':result['T0'],'final_temperature':result['final_temperature'],
            'search_evaluations':len(rows),'stop_reason':result['stop_reason'],
            'constructor_hard_fallbacks':sum(r['hard_conflict_fallback'] for r in result['construction_trace']),
            'unique_uphill_draws':len(set(draws)),'uphill_draws':len(draws),
            'accepted_worse':accepted_worse,'accepted_worse_rate':accepted_worse/len(draws) if draws else None,
            'accept_probability_deltaF_2_at_stop':math.exp(-2/result['final_temperature']),
            'diagnostic_only_temperature_for_deltaF_2_acceptance_5_percent':threshold,
            'search_evaluations_to_that_temperature_if_no_earlier_stop':cooling_to_threshold*cfg['L'],
            'anomalies':sorted(set(anomalies)),'temperature_blocks':blocks}


def clock_range(model,eid,position):
    day,start=position; event=model.events[eid]
    slots={s['period']:s for s in model.days[day]['segments'] if s['period'] is not None}
    fmt=lambda minutes:f'{minutes//60:02d}:{minutes%60:02d}'
    return {'day':day,'start_period':start,'end_period':start+event['time']['jp']-1,
            'start_time':fmt(slots[start]['start_minute']),
            'end_time':fmt(slots[start+event['time']['jp']-1]['end_minute'])}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot',default=str(ROOT/'results/final-pilot/school-user-approved-v1'))
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv);pilot=Path(args.pilot);output=Path(args.output)
    if output.exists():raise ValueError('Use a new diagnostic output directory')
    model,frozen,_,_=load_frozen(pilot/'FROZEN_EXPERIMENT_CONFIG.json')
    repair=repair_baseline(model)
    diagnoses=[diagnose_run(model,read_json(pilot/f'seed-{seed}/run.json')) for seed in frozen['protocol']['pilot_seeds']]
    if any(r['anomalies'] for r in diagnoses):raise ValueError('Pilot correctness discrepancy: inspect diagnosis before proceeding')
    output.mkdir(parents=True)
    atomic_json(output/'repair-search.json',repair)
    atomic_json(output/'sa-diagnosis.json',diagnoses)
    atomic_text(output/'temperature-blocks.csv',csv_table([b for r in diagnoses for b in r['temperature_blocks']]))
    summary=[{k:v for k,v in r.items() if k not in ('temperature_blocks','configuration','anomalies')} for r in diagnoses]
    atomic_text(output/'pilot-diagnosis.csv',csv_table(summary))
    lines=['# Perbaikan baseline dan diagnosis SA', '',
           'Data sekolah asli; evaluator, constraint, bobot, kategori, domain, dan parameter frozen tidak diubah.',
           'Kandidat perbaikan ini **bukan hasil SA, bukan main experiment, dan bukan seleksi rekomendasi A/B/C**.', '',
           f"Pencarian terbatas memeriksa {len(repair['tested_candidates'])} kandidat; {repair['feasible_candidate_count']} feasible menurut validator independen.", '']
    if repair['selected']:
        selected=repair['selected']; rows=model.timetable(selected['placement'])
        # Revalidate the exact export immediately before publishing it.
        verified=validate(model,rows)
        if not verified['feasible']:raise ValueError('Selected export failed final validation')
        edits=[{'event_id':eid,'subject_id':model.events[eid]['subject_id'],
                'class_ids':list(model.events[eid]['class_ids']),'teacher_ids':list(model.events[eid]['teacher_codes']),
                'before':clock_range(model,eid,repair['baseline'][eid]),'after':clock_range(model,eid,selected['placement'][eid])}
               for eid in model.ids if selected['placement'][eid]!=repair['baseline'][eid]]
        artifact={'purpose':repair['purpose'],'dataset_hash':model.dataset_hash,'rules_hash':model.rules_hash,
                  'dataset_version':model.data.get('id'),'rules_version':model.rules['rules_version'],
                  'frozen_input_hash':frozen['snapshot_hash'],'placement':selected['placement'],'quality':verified,'changes':edits}
        atomic_json(output/'repair-candidate.json',artifact)
        atomic_json(output/'repaired-timetable.json',rows)
        atomic_json(output/'independent-validation.json',verified)
        comparison=[{'metric':k,**change(v,metrics(verified)[k])} for k,v in metrics(repair['baseline_quality']).items()]
        atomic_text(output/'baseline-repair-comparison.csv',csv_table(comparison))
        atomic_json(output/'impact-details.json',{'baseline':detailed(model,model.timetable(repair['baseline']),repair['baseline_quality']),
                                                 'repair':detailed(model,rows,verified)})
        lines+=['## Kandidat valid', '']
        for e in edits:
            a,b=e['before'],e['after']
            lines.append(f"- {e['event_id']}: {e['subject_id']} {', '.join(e['class_ids'])}, guru {', '.join(e['teacher_ids'])}: {a['day']} JP{a['start_period']}–{a['end_period']} ({a['start_time']}–{a['end_time']}) → {b['day']} JP{b['start_period']}–{b['end_period']} ({b['start_time']}–{b['end_time']}).")
        lines+=['', '| Metrik | Baseline | Perbaikan |','|---|---:|---:|']
        lines += [f"| {r['metric']} | {r['baseline_raw_value']:.9g} | {r['recommendation_raw_value']:.9g} |" for r in comparison]
        lines+=['', '**HC1–HC6 semuanya nol setelah perbaikan.** Sumber asli tidak ditimpa. Validitas ini berlaku terhadap model dan asumsi penelitian saat ini, bukan konfirmasi penerapan sekolah.', '']
    else:lines+=['Tidak ditemukan perbaikan feasible dalam ruang pencarian terbatas ini; bukan bukti bahwa masalah tidak mempunyai solusi.', '']
    lines+=['## Diagnosis pilot', '', '| Seed | Initial H | Best H | Current H akhir | T0 | T akhir | Perbaikan best | P menerima ΔF=2 di akhir |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in diagnoses:
        lines.append(f"| {r['seed']} | {r['initial_H']} | {r['best_H']} | {r['final_current_H']} | {r['T0']:.4f} | {r['final_temperature']:.4f} | {r['best_improvements']} | {r['accept_probability_deltaF_2_at_stop']:.2%} |")
    lines+=['', 'Pemeriksaan ulang rumus kalibrasi, temperatur setiap langkah, keputusan Metropolis, variasi bilangan acak, urutan domain konstruksi, tracking best, hitungan stagnasi, dan validasi initial/best/current tidak menemukan ketidaksesuaian pada lima log ini. Ini audit terarah, bukan bukti bahwa seluruh kemungkinan bug telah dieliminasi.', '',
            'Konstruksi greedy dimulai dari jadwal kosong dan tidak memanfaatkan baseline yang hampir feasible. Sesuai definisi, penempatan yang buntu memakai hard-conflict fallback; hasilnya 60–76 pelanggaran pada pilot. Kandidat perbaikan membuktikan bahwa H=0 dapat dicapai pada model ini, sehingga kegagalan pilot tidak dapat dijelaskan sebagai ketidaklayakan dataset.', '',
            'Pada C2, L=513 dan alpha=0.95: 20.000 search evaluations baru menyelesaikan 38 penurunan temperatur. Tidak ada best improvement sehingga stagnasi menghentikan run ketika temperatur masih cukup tinggi untuk sering menerima kenaikan F. Bukti ini konsisten dengan pencarian yang belum sempat memasuki temperatur rendah; tidak membuktikan bahwa memperpanjang run pasti menghasilkan feasible.', '',
            'Kolom probabilitas ΔF=2 adalah ilustrasi skala penalti, bukan klaim semua hard violation menaikkan F tepat 2 karena soft juga bisa berubah. Ambang probabilitas 5% dalam JSON hanya alat diagnosis; tidak diterapkan sebagai parameter pencarian.', '',
            '## Tindak lanjut metodologi', '',
            'Perbaikan baseline dapat ditinjau sekolah sebagai kandidat terpisah. Jika akan digunakan sebagai initial SA, definisi constructive initialization berubah dan harus dinyatakan sebagai versi/metode baru, diuji pilot, lalu dibekukan lagi. Demikian pula perubahan cooling/stagnation. Tidak ada perubahan seperti itu dilakukan di tahap diagnosis ini; tidak ada 180 run yang diluncurkan.', '',
            'Tidak ada klaim tentang prestasi, kelelahan siswa, atau percepatan kerja sekolah.']
    atomic_text(output/'REPORT.md','\n'.join(lines)+'\n')
    print(f"Diagnostic complete: {repair['feasible_candidate_count']} feasible repairs; {len(diagnoses)} pilot logs audited. Output: {output.resolve()}")


if __name__=='__main__':main()
