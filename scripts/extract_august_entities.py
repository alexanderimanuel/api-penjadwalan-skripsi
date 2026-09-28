"""Kamus entitas dari dua PDF Agustus; occurrence bukan event hasil rekonsiliasi."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import re
import unicodedata
import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/entities/2026-08-03'
CLASS = re.compile(r'X(?:II|I)?-[1-9](?!\d)')

def normalize(text):
    return ' '.join(unicodedata.normalize('NFC', text).split())

def ref(kind, page, words, raw=None):
    return {'source': kind, 'page': page, 'raw': raw or ' '.join(w['text'] for w in words),
            'bbox_pdf_points': [min(w['x0'] for w in words), min(w['top'] for w in words),
                                max(w['x1'] for w in words), max(w['bottom'] for w in words)]}

def labels(words):
    ordered = sorted(words, key=lambda w: (round(w['top'], 1), w['x0']))
    output = []
    i = 0
    while i < len(ordered):
        group = [ordered[i]]
        if i+1 < len(ordered):
            a, b = ordered[i:i+2]
            if ((a['text'], b['text']) in [('MAT','P'),('BIG','L'),('IND','Lanjut')]
                    and abs(a['top']-b['top']) < 1 and 0 <= b['x0']-a['x1'] < 15):
                group.append(b)
                i += 1
        output.append(group)
        i += 1
    return output

def main():
    inventory = json.loads((ROOT/'data/inventory/2026-08-03/register.json').read_text(encoding='utf-8'))
    teachers, classes, subjects, activities = {}, {}, {}, {}
    teacher_refs, class_refs, issues = [], [], []
    sources = []
    for source in inventory['files']:
        kind = source['id']
        path = ROOT/source['frozen_path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256']
        sources.append({k: source[k] for k in ['id','frozen_path','sha256']})
        with pdfplumber.open(path) as pdf:
            for n, page in enumerate(pdf.pages,1):
                heading = normalize(page.crop((0,48,792,86)).extract_text())
                heading_ref = {'source': kind, 'page': n, 'raw': heading, 'bbox_pdf_points': [0,48,792,86]}
                if kind == 'guru':
                    match = re.fullmatch(r'(.+?)\s*\((\d+)\)', heading)
                    assert match, heading
                    name, code = match.groups()
                    assert code not in teachers
                    teachers[code] = {'id': f'G{int(code):03d}', 'source_code': code,
                        'display_name': normalize(name), 'heading_raw': heading,
                        'source_ref': heading_ref, 'subject_labels_observed': [],
                        'class_document_code_mentions': []}
                else:
                    assert CLASS.fullmatch(heading) and heading not in classes
                    classes[heading] = {'id': heading, 'label': heading, 'source_ref': heading_ref,
                                        'teacher_document_mentions': []}
                words = page.crop((76,158,642,586)).extract_words(extra_attrs=['fontname','size'])
                if kind == 'kelas':
                    assert all(w['fontname'] in ['CIDFont+F3','CIDFont+F4'] for w in words)
                    subject_words = [w for w in words if w['fontname']=='CIDFont+F3']
                    small = [w for w in words if w['fontname']=='CIDFont+F4']
                    for w in small:
                        for m in re.finditer(r'\((\d+)\)',w['text']):
                            teacher_refs.append((m.group(1), ref(kind,n,[w]), heading))
                    # Slash groups keep all codes together; not a new teacher ID.
                    numeric = sorted([w for w in small if re.fullmatch(r'\d+|/',w['text'])], key=lambda w:(round(w['top'],1),w['x0']))
                    groups = []
                    for w in numeric:
                        if groups and abs(groups[-1][-1]['top']-w['top'])<1 and w['x0']-groups[-1][-1]['x1']<20:
                            groups[-1].append(w)
                        else:
                            groups.append([w])
                    for group in groups:
                        raw = ' '.join(w['text'] for w in group)
                        if '/' in raw:
                            assert re.fullmatch(r'\d+(?:\s*/\s*\d+)+',raw), raw
                            codes = re.findall(r'\d+',raw)
                            evidence = ref(kind,n,group)
                            issues.append({'type':'multiple_teacher_codes','class_id':heading,'codes':codes,
                                'source_ref':evidence,'status':'requires_event_reconciliation'})
                            for c in codes:
                                teacher_refs.append((c,evidence,heading))
                else:
                    assert all(w['fontname'] in ['CIDFont+F1','CIDFont+F4'] for w in words)
                    subject_words = [w for w in words if w['fontname']=='CIDFont+F1']
                    for w in words:
                        if w['fontname']=='CIDFont+F4':
                            matches = list(CLASS.finditer(w['text']))
                            if not matches:
                                issues.append({'type':'unparsed_class_token','source_ref':ref(kind,n,[w])})
                            for m in matches:
                                class_refs.append((m.group(),ref(kind,n,[w]),code))
                    teachers[code]['has_schedule_labels'] = bool(subject_words)
                for group in labels(subject_words):
                    raw = ' '.join(w['text'] for w in group)
                    label = normalize(raw)
                    assert label not in ['P','L','IND','Lanjut'], f'Unmerged label: {label}'
                    target = activities if label in ['UPCR','4K'] else subjects
                    target.setdefault(label, {'id': label, 'label': label, 'expanded_name': None, 'occurrences': []})
                    target[label]['occurrences'].append(ref(kind,n,group))
                    if kind == 'guru' and label not in teachers[code]['subject_labels_observed']:
                        teachers[code]['subject_labels_observed'].append(label)
    unknown_teachers, unknown_classes = [], []
    for code, evidence, class_id in teacher_refs:
        if code not in teachers:
            unknown_teachers.append({'code':code,'source_ref':evidence})
        else:
            teachers[code]['class_document_code_mentions'].append({'class_id':class_id,**evidence})
    for class_id, evidence, code in class_refs:
        if class_id not in classes:
            unknown_classes.append({'class_id':class_id,'source_ref':evidence})
        else:
            classes[class_id]['teacher_document_mentions'].append({'teacher_code':code,**evidence})
    assert set(teachers)=={str(n) for n in range(1,59)}
    assert set(classes)=={f'{level}-{n}' for level in ['X','XI','XII'] for n in range(1,10)}
    assert not unknown_teachers and not unknown_classes
    for item in subjects.values():
        item['occurrence_counts'] = dict(Counter(o['source'] for o in item['occurrences']))
        assert set(item['occurrence_counts']) == {'kelas','guru'}
    result = {'schema_version':1,'version':'2026-08-03','status':'entity_dictionary_not_reconciled_events',
        'sources':sources,'normalization_policy':[
            'Unicode NFC dan spasi dirapikan; ejaan, gelar dan kapitalisasi tidak dikoreksi tanpa bukti.',
            'Kode guru bersifat lokal untuk versi Agustus; heading PDF guru menjadi label tampilan.',
            'MAT P, BIG L, IND Lanjut dipertahankan; tidak disatukan dengan MAT, BIG, BIN.',
            'Nama panjang mata pelajaran belum diisi jika tidak dinyatakan sumber.',
            'Kemunculan teks dan relasi pada halaman bukan event atau penugasan final.'],
        'teachers': sorted(teachers.values(),key=lambda t:int(t['source_code'])),
        'classes':list(classes.values()),'subjects':sorted(subjects.values(),key=lambda s:s['id']),
        'fixed_activities':list(activities.values()),'issues':issues}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'entities.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# Kamus entitas Agustus 2026','','Identitas dan kemunculan teks; belum menjadi dataset event hasil rekonsiliasi.','','## Guru','','| ID | Kode | Nama sesuai heading | Label mapel teramati | Halaman guru |','|---|---|---|---|---:|']
    for t in result['teachers']:
        lines.append(f"| {t['id']} | {t['source_code']} | {t['display_name']} | {', '.join(t['subject_labels_observed']) or 'Tidak ada isi jadwal'} | {t['source_ref']['page']} |")
    lines+=['','## Kelas','','| Kelas | Halaman kelas |','|---|---:|']
    lines += [f"| {c['id']} | {c['source_ref']['page']} |" for c in result['classes']]
    lines+=['','## Label mata pelajaran','','Jumlah berikut adalah kemunculan label, bukan JP dan bukan hasil rekonsiliasi event.','','| Label | PDF kelas | PDF guru |','|---|---:|---:|']
    for s in result['subjects']:
        lines.append(f"| {s['label']} | {s['occurrence_counts']['kelas']} | {s['occurrence_counts']['guru']} |")
    (OUT/'kamus-entitas.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    report={'status':'passed','teachers':len(teachers),'classes':len(classes),'subjects':len(subjects),
        'teacher_code_mentions_in_class_pdf':len(teacher_refs),'class_mentions_in_teacher_pdf':len(class_refs),
        'multiple_teacher_groups':len([i for i in issues if i['type']=='multiple_teacher_codes']),
        'unknown_teacher_codes':unknown_teachers,'unknown_class_ids':unknown_classes,
        'unparsed_class_tokens':[i for i in issues if i['type']=='unparsed_class_token'],
        'subject_occurrences':{s['id']:s['occurrence_counts'] for s in result['subjects']},
        'limits':'Identitas saja; kecocokan event, pertemuan, JP, menit dan HC belum dievaluasi.'}
    assert not report['unparsed_class_tokens']
    assert json.loads((OUT/'entities.json').read_text(encoding='utf-8'))==result
    (OUT/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
