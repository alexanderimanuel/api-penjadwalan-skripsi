"""Independent baseline/recommendation evaluation and descriptive impact tables.

All schedules use one immutable dataset/rules pair. Missing recommendations are
reported as unavailable, never substituted with infeasible search states.
"""
import argparse
from collections import Counter, defaultdict
import csv
import io
import json
from pathlib import Path

from export_august import read_json
from final_experiment import atomic_json, atomic_text, load_frozen
from final_foundation import thaw
from final_rules import FinalModel, category, stable_hash
from final_validator import validate
from model_august import ROOT


def change(baseline, recommendation):
    difference = recommendation-baseline
    return {'baseline_raw_value': baseline, 'recommendation_raw_value': recommendation,
            'difference': difference, 'absolute_difference': abs(difference),
            'percentage_change': 100*difference/baseline if baseline != 0 else None,
            'percentage_status': 'APPLICABLE' if baseline != 0 else 'N/A_BASELINE_ZERO',
            'direction': 'DECREASED' if difference < 0 else 'INCREASED' if difference > 0 else 'UNCHANGED'}


def detailed(model, rows, score):
    days = [d['id'] for d in model.data['calendar']['days']]
    teachers = sorted(t['source_code'] for t in model.data['teachers'])
    classes = sorted({c for e in model.events.values() for c in e['class_ids']})
    class_subjects = sorted({(c, e['subject_id']) for e in model.events.values() for c in e['class_ids']})
    teacher_day = {(t, d): [] for t in teachers for d in days}
    for item in score['details']['SC1']:
        teacher_day[item['teacher_id'], item['day']] = item['empty_kbm_periods']
    sc1_day = [{'teacher_id': t, 'day': d, 'gap_count': len(gaps), 'gap_periods': gaps}
               for (t, d), gaps in teacher_day.items()]
    sc1_teacher = [{'teacher_id': t, 'gap_count': sum(len(teacher_day[t,d]) for d in days)} for t in teachers]
    meetings = {(r['class_id'], r['subject_id'], r['day']): r for r in score['details']['SC2']}
    sc2_day = [{'class_id': c, 'subject_id': s, 'day': d,
                'meeting_count': meetings.get((c,s,d), {}).get('N', 0),
                'penalty': meetings.get((c,s,d), {}).get('penalty', 0)}
               for c,s in class_subjects for d in days]
    sc2_subject = [{'class_id': c, 'subject_id': s,
                    'penalty': sum(r['penalty'] for r in sc2_day if r['class_id']==c and r['subject_id']==s)}
                   for c,s in class_subjects]
    sc2_class_day = [{'class_id': c, 'day': d,
                      'penalty': sum(r['penalty'] for r in sc2_day if r['class_id']==c and r['day']==d)}
                     for c in classes for d in days]
    high = {(r['class_id'], r['day']): r for r in score['details']['SC3']}
    jp = Counter()
    for row in rows:
        for c in row['class_ids']:
            if category(model.rules, row['subject_id'], c) == 'HIGH':
                jp[c, row['day']] += row['jp']
    sc3 = []
    for c in classes:
        for d in days:
            item = high.get((c,d), {})
            sc3.append({'class_id': c, 'day': d, 'high_families': item.get('high_families', []),
                        'high_distinct_subjects': item.get('K', 0),
                        'daily_limit': model.rules['difficulty']['daily_limit'],
                        'excess': item.get('penalty', 0), 'high_subject_jp': jp[c,d]})
    # Independently obtained details must reconcile with evaluator totals.
    actual = {'SC1': sum(r['gap_count'] for r in sc1_teacher),
              'SC2': sum(r['penalty'] for r in sc2_subject), 'SC3': sum(r['excess'] for r in sc3)}
    if actual != score['raw']: raise ValueError('Detail totals differ from final evaluator')
    hc7_by_class = Counter()
    for r in score['details'].get('HC7', []):
        for c in r['class_ids']:
            hc7_by_class[c] += r['penalty']
    friday_jp = Counter()
    friday_last = {}
    for row in rows:
        if row['day'] == 'jumat':
            for c in row['class_ids']:
                friday_jp[c] += row['jp']
                friday_last[c] = max(friday_last.get(c, 0), row['end_period'])
    from final_rules import friday_limit as _friday_limit
    hc7_class_day = []
    for c in classes:
        limit = _friday_limit(model.rules, c)
        hc7_class_day.append({'class_id': c, 'day': 'jumat', 'friday_jp': friday_jp[c],
                              'last_period': friday_last.get(c, 0),
                              'limit': limit, 'violations': hc7_by_class[c]})
    # Dataset events are single-class, so per-class violation rows sum to HC7.
    if sum(r['violations'] for r in hc7_class_day) != score['HC'].get('HC7', 0):
        raise ValueError('Friday detail total differs from final evaluator')
    hc8_map = {(r['class_id'], r['day']): r for r in score['details'].get('HC8', [])}
    hc8_class_day = []
    for c in classes:
        for d in days:
            item = hc8_map.get((c, d), {})
            hc8_class_day.append({'class_id': c, 'day': d,
                                  'empty_kbm_periods': item.get('empty_kbm_periods', []),
                                  'hole_count': item.get('penalty', 0)})
    if sum(r['hole_count'] for r in hc8_class_day) != score['HC'].get('HC8', 0):
        raise ValueError('Class-hole detail total differs from final evaluator')
    return {'sc1_teacher_day': sc1_day, 'sc1_teacher': sc1_teacher,
            'sc2_class_subject_day': sc2_day, 'sc2_class_subject': sc2_subject,
            'sc2_class_day': sc2_class_day, 'sc3_class_day': sc3,
            'hc7_class_day': hc7_class_day, 'hc8_class_day': hc8_class_day}


def metrics(score):
    return {**score['HC'], 'H': score['H'], **score['raw'],
            **{'normalized_'+k: v for k,v in score['normalized'].items()}, 'S': score['S'], 'F': score['F']}


def compare_rows(before, after, keys, fields, label):
    index = {tuple(r[k] for k in keys): r for r in after}
    result = []
    for row in before:
        other = index[tuple(row[k] for k in keys)]
        for field in fields:
            result.append({'recommendation': label, **{k: row[k] for k in keys},
                           'metric': field, **change(row[field], other[field])})
    return result


def build_report(model, recommendations, availability=None):
    if len(recommendations) > 3: raise ValueError('At most three labeled recommendations')
    labels = [r['label'] for r in recommendations]
    if len(set(labels)) != len(labels) or not set(labels) <= {'A','B','C'}:
        raise ValueError('Recommendation labels must be unique A/B/C')
    baseline = {eid: (e['reference']['day'], e['reference']['start_period']) for eid,e in model.events.items()}
    baseline_rows = model.timetable(baseline)
    # Fresh direct validator calls, not stored pilot/search evaluation results.
    baseline_score = validate(model, baseline_rows)
    if baseline_score['validation_status'] != 'VALID_TIMETABLE':
        raise ValueError('Baseline has structural violations; soft impact cannot be reported')
    scores = {'BASELINE': baseline_score}
    schedules = {'BASELINE': baseline_rows}
    provenance = {}
    for rec in recommendations:
        if rec.get('dataset_hash') != model.dataset_hash or rec.get('rules_hash') != model.rules_hash:
            raise ValueError('Recommendation dataset/rules mismatch: '+rec['label'])
        rows = rec['timetable']
        score = validate(model, rows)
        if not score['feasible']: raise ValueError('Recommendation not independently feasible: '+rec['label'])
        scores[rec['label']] = score; schedules[rec['label']] = rows
        provenance[rec['label']] = rec.get('source', {})
    details = {label: detailed(model, schedules[label], score) for label, score in scores.items()}
    tables = {'metrics': [], 'feasibility': [], 'sc1_teacher_change': [], 'sc1_teacher_day_change': [],
              'sc2_class_subject_change': [], 'sc2_class_subject_day_change': [],
              'sc2_class_day_change': [], 'sc3_class_day_change': [], 'hc7_class_day_change': [],
              'hc8_class_day_change': []}
    distributions = {}; most_affected = {}
    for label in labels:
        before, after = metrics(baseline_score), metrics(scores[label])
        for metric, value in before.items():
            tables['metrics'].append({'recommendation': label, 'metric': metric,
                                     **change(value, after[metric])})
        tables['feasibility'].append({'recommendation': label, 'baseline_feasible': baseline_score['feasible'],
                                      'recommendation_feasible': scores[label]['feasible'],
                                      'percentage_change': None, 'percentage_status': 'N/A_BOOLEAN_STATUS'})
        specs = [('sc1_teacher', ['teacher_id'], ['gap_count']),
                 ('sc1_teacher_day', ['teacher_id','day'], ['gap_count']),
                 ('sc2_class_subject', ['class_id','subject_id'], ['penalty']),
                 ('sc2_class_subject_day', ['class_id','subject_id','day'], ['penalty']),
                 ('sc2_class_day', ['class_id','day'], ['penalty']),
                 ('sc3_class_day', ['class_id','day'], ['high_distinct_subjects','excess','high_subject_jp']),
                 ('hc7_class_day', ['class_id','day'], ['friday_jp','last_period','violations']),
                 ('hc8_class_day', ['class_id','day'], ['hole_count'])]
        for name, keys, fields in specs:
            tables[name+'_change'].extend(compare_rows(details['BASELINE'][name], details[label][name], keys, fields, label))
        counts = Counter(r['direction'] for r in tables['sc1_teacher_change'] if r['recommendation']==label)
        distributions[label] = {k: counts[k] for k in ('DECREASED','UNCHANGED','INCREASED')}
        affected = [r for r in tables['sc2_class_day_change'] if r['recommendation']==label and r['absolute_difference'] > 0]
        most_affected[label] = sorted(affected, key=lambda r: (-r['absolute_difference'], r['class_id'], r['day']))[:10]
    return {'schema_version': 1, 'status': 'COMPARISON_AVAILABLE' if labels else 'BASELINE_ONLY_NO_FEASIBLE_RECOMMENDATION',
            'baseline_effective_date': '2026-08-03', 'dataset_version': model.data.get('dataset_version', model.data.get('version', model.data.get('id'))),
            'dataset_hash': model.dataset_hash, 'rules_version': model.rules['rules_version'], 'rules_hash': model.rules_hash,
            'difficulty_version': model.rules['difficulty']['version'],
            'high_subject_daily_limit': model.rules['difficulty']['daily_limit'],
            'friday_jp_limits': thaw(model.rules.get('friday_jp_limits', {})),
            'normalization': thaw(model.rules['normalization']), 'weights': thaw(model.rules['weights']),
            'evaluator': 'final_validator.validate (fresh direct full timetable checks)',
            'evaluator_hash': stable_hash((ROOT/'scripts/final_validator.py').read_text(encoding='utf-8')),
            'recommendation_labels': labels, 'unavailable_labels': sorted({'A','B','C'}-set(labels)),
            'recommendation_sources': provenance, 'availability': availability or {},
            'evaluations': scores, 'details': details, 'comparisons': tables,
            'teacher_gap_change_distribution': distributions, 'sc2_most_affected_class_days': most_affected,
            'interpretation': {'difference': 'recommendation minus baseline',
                'absolute_difference': 'absolute magnitude of difference',
                'percentage_change': '100 * (recommendation - baseline) / baseline; null and N/A when baseline=0',
                'high_distinct_subjects': 'Distinct difficulty families, consistent with SC3; IDs remain separate for SC2.',
                'high_subject_jp': 'Sum of canonical HIGH-subject event JP per class/day; descriptive only, not a new penalty.',
                'limits': 'Descriptive timetable metrics only. Lower total score does not establish improvement for every teacher/class, achievement, fatigue, or school working time.'}}


def csv_table(rows, fields=None):
    fields = list(rows[0]) if rows else fields or ['status']
    out = io.StringIO(newline=''); writer = csv.DictWriter(out, fieldnames=fields); writer.writeheader()
    for row in rows:
        writer.writerow({k: 'N/A' if v is None else json.dumps(v, ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()})
    return out.getvalue()


def summary_markdown(report):
    score = report['evaluations']['BASELINE']
    lines = ['# Evaluasi baseline dan dampak rekomendasi', '',
             'Baseline: jadwal sekolah berlaku **3 Agustus 2026**. Semua jadwal dinilai ulang dengan final validator yang sama sebelum laporan dibuat.', '',
             f"Status: **{report['status']}**. Dataset `{report['dataset_version']}`; rules `{report['rules_version']}`.", '',
             '| Metrik baseline | Nilai |', '|---|---:|']
    lines += [f'| {k} | {v:.8g} |' for k,v in metrics(score).items()]
    lines += [f"| Feasible | {score['feasible']} |", '',
              'Rincian HC beserta event yang bentrok tersedia dalam `report.json` dan `hard-violations.json`. Baseline yang bentrok tetap dilaporkan apa adanya.', '']
    base_detail = report['details']['BASELINE']
    top_teachers = sorted(base_detail['sc1_teacher'], key=lambda r: (-r['gap_count'], r['teacher_id']))[:10]
    lines += ['## Rincian baseline', '',
              f"SC1: total {score['raw']['SC1']} slot KBM kosong internal. Guru dengan gap terbanyak:", '',
              '| Kode guru | Gap |', '|---|---:|']
    lines += [f"| {r['teacher_id']} | {r['gap_count']} |" for r in top_teachers]
    positive_sc2 = [r for r in base_detail['sc2_class_subject'] if r['penalty'] > 0]
    lines += ['', f"SC2: {len(positive_sc2)} pasangan kelas–mapel memiliki penalti positif; total penalti {score['raw']['SC2']}.",
              f"SC3: {sum(r['excess'] > 0 for r in base_detail['sc3_class_day'])} kelas/hari melampaui daily limit; total excess {score['raw']['SC3']}.", '',
              '| Kelas | Hari | HIGH berbeda | Excess | JP HIGH |', '|---|---|---:|---:|---:|']
    lines += [f"| {r['class_id']} | {r['day']} | {r['high_distinct_subjects']} | {r['excess']} | {r['high_subject_jp']} |"
              for r in sorted(base_detail['sc3_class_day'], key=lambda r: (-r['excess'],r['class_id'],r['day']))[:10]]
    lines += ['']
    if not report['recommendation_labels']:
        lines += ['**Rekomendasi A/B/C belum tersedia.** Tidak ada nilai rekomendasi, selisih, atau persentase yang dibuat-buat. Tabel perbandingan hanya memiliki header sampai jadwal feasible tersedia.', '']
    for label in report['recommendation_labels']:
        lines += [f'## Rekomendasi {label}', '', '| Metrik | Baseline | Rekomendasi | Selisih R−B | Besar selisih | Perubahan % |', '|---|---:|---:|---:|---:|---:|']
        for r in report['comparisons']['metrics']:
            if r['recommendation'] != label: continue
            pct = 'N/A' if r['percentage_change'] is None else f"{r['percentage_change']:.3f}%"
            lines.append(f"| {r['metric']} | {r['baseline_raw_value']:.8g} | {r['recommendation_raw_value']:.8g} | {r['difference']:+.8g} | {r['absolute_difference']:.8g} | {pct} |")
        count = report['teacher_gap_change_distribution'][label]
        lines += ['', f"SC1: guru dengan gap berkurang **{count['DECREASED']}**, tetap **{count['UNCHANGED']}**, bertambah **{count['INCREASED']}**.",
                  f"Hard constraints: baseline H={score['H']}; rekomendasi H={report['evaluations'][label]['H']} (feasible).", '',
                  'SC2 — class/day dengan perubahan penalti terbesar:', '']
        affected = report['sc2_most_affected_class_days'][label]
        if affected:
            lines += [f"- {r['class_id']} / {r['day']}: {r['baseline_raw_value']} → {r['recommendation_raw_value']} (selisih {r['difference']:+g})." for r in affected]
        else: lines += ['Tidak ada perubahan penalti SC2 per class/day.']
        lines += ['', 'SC3: tabel per kelas/hari memuat keluarga HIGH, jumlah HIGH berbeda, excess di atas daily limit, dan total JP HIGH. JP HIGH hanya informasi tambahan.', '']
    lines += ['## Cara memakai tabel', '',
              '`schedule-metrics.csv` dan `comparison-metrics.csv` siap menjadi sumber Tabel Bab IV. File detail dan perubahan SC1/SC2/SC3 dapat digunakan untuk grafik serta peninjauan sekolah. Semua guru dan semua hari kelas tercakup, termasuk nilai nol.', '',
              'Selisih bertanda adalah rekomendasi dikurangi baseline; besar selisih selalu nonnegatif. Persentase menggunakan selisih bertanda. Baseline nol menghasilkan N/A, bukan 0% atau pembagian nol.', '',
              '**Batas interpretasi:** total skor yang lebih kecil tidak berarti setiap guru atau kelas membaik. Evaluasi ini tidak mengukur prestasi siswa, kelelahan siswa, atau waktu kerja sekolah. Tidak dibuat klaim peningkatan pada hal-hal tersebut.', '']
    return '\n'.join(lines)


def write_report(model, recommendations, output, availability=None):
    output = Path(output)
    if output.exists(): raise ValueError('Use a new report output directory to preserve previous evidence')
    report = build_report(model, recommendations, availability)
    output.mkdir(parents=True)
    atomic_json(output/'report.json', report)
    atomic_json(output/'dataset-snapshot.json', thaw(model.data))
    atomic_json(output/'rules-snapshot.json', thaw(model.rules))
    atomic_json(output/'hard-violations.json', {k: {h:v['details'][h] for h in v['HC']} for k,v in report['evaluations'].items()})
    schedule_metrics = [{'schedule': label, **metrics(score), 'feasible': score['feasible']} for label,score in report['evaluations'].items()]
    atomic_text(output/'schedule-metrics.csv', csv_table(schedule_metrics))
    distributions = [{'recommendation': label, **counts} for label,counts in report['teacher_gap_change_distribution'].items()]
    atomic_text(output/'teacher-gap-change-distribution.csv', csv_table(distributions, ['recommendation','DECREASED','UNCHANGED','INCREASED']))
    affected = [row for rows in report['sc2_most_affected_class_days'].values() for row in rows]
    atomic_text(output/'sc2-most-affected-class-days.csv', csv_table(affected, ['recommendation','class_id','day','metric',*change(0,0)]))
    for name in report['details']['BASELINE']:
        rows = [{'schedule': label, **r} for label,detail in report['details'].items() for r in detail[name]]
        atomic_text(output/(name+'.csv'), csv_table(rows))
    fields = {'metrics': ['recommendation','metric'], 'feasibility': ['recommendation','baseline_feasible','recommendation_feasible','percentage_change','percentage_status'],
              'sc1_teacher_change': ['recommendation','teacher_id','metric'], 'sc1_teacher_day_change': ['recommendation','teacher_id','day','metric'],
              'sc2_class_subject_change': ['recommendation','class_id','subject_id','metric'],
              'sc2_class_subject_day_change': ['recommendation','class_id','subject_id','day','metric'],
              'sc2_class_day_change': ['recommendation','class_id','day','metric'], 'sc3_class_day_change': ['recommendation','class_id','day','metric'],
              'hc7_class_day_change': ['recommendation','class_id','day','metric'],
              'hc8_class_day_change': ['recommendation','class_id','day','metric']}
    value_fields = list(change(0,0))
    for name, rows in report['comparisons'].items():
        atomic_text(output/('comparison-'+name+'.csv'), csv_table(rows, fields[name]+([] if name=='feasibility' else value_fields)))
    atomic_text(output/'SUMMARY.md', summary_markdown(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot', default=str(ROOT/'results/final-pilot/school-user-approved-v1'))
    parser.add_argument('--recommendation', action='append', default=[], help='A=path/to/result.json (validated selected run)')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv); pilot = Path(args.pilot)
    # Reporting is allowed even when main readiness is false, but all frozen
    # input, code and pilot-evidence hashes must still match.
    model, _, _, _ = load_frozen(pilot/'FROZEN_EXPERIMENT_CONFIG.json')
    recommendations = []
    for spec in args.recommendation:
        label,path = spec.split('=',1); path = Path(path); result = read_json(path)
        if result.get('best_feasible') is None: raise ValueError('No best feasible in '+str(path))
        export = path.parent/'best-feasible-timetable.json'
        rows = read_json(export) if export.exists() else model.timetable(result['best_feasible'])
        if rows != model.timetable(result['best_feasible']): raise ValueError('Export does not match selected placement')
        recommendations.append({'label': label, 'dataset_hash': result['dataset_hash'], 'rules_hash': result['rules_hash'],
                                'timetable': rows, 'source': {'path': str(path.resolve()), 'seed': result['seed'], 'configuration': result['configuration']}})
    availability = {}
    if (pilot/'summary.json').exists():
        summary = read_json(pilot/'summary.json')
        availability = {'source': str((pilot/'summary.json').resolve()), 'pilot_runs': len(summary['runs']),
                        'pilot_feasible_runs': sum(r['best_feasible'] for r in summary['runs']),
                        'note': 'Pilot outcomes are not main experiment results or automatic A/B/C selection.'}
    report = write_report(model, recommendations, args.output, availability)
    print(json.dumps({'status': report['status'], 'baseline': metrics(report['evaluations']['BASELINE']),
                      'baseline_feasible': report['evaluations']['BASELINE']['feasible'], 'recommendations': report['recommendation_labels'],
                      'output': str(Path(args.output).resolve())}, indent=2))


if __name__ == '__main__': main()
