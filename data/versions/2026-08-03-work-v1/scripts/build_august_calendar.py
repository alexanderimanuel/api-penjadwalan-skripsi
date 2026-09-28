"""Transkripsi panel waktu PDF Agustus yang diperiksa visual, bukan OCR otomatis.

Jalankan dari direktori mana pun dengan Python 3. Tidak mengubah optimizer lama.
"""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/calendar/2026-08-03'

# Nomor periode, mulai, selesai, label sumber. None berarti jeda tanpa nomor JP.
PANELS = {
    'senin': [
        (1, '06:45', '07:45', 'UPCR'),
        (2, '07:45', '08:30', 'KBM'), (3, '08:30', '09:15', 'KBM'),
        (4, '09:15', '10:00', 'KBM'), (None, '10:00', '10:15', 'Istirahat'),
        (5, '10:15', '11:00', 'KBM'), (6, '11:00', '11:45', 'KBM'),
        (None, '11:45', '12:30', 'Istirahat'), (7, '12:30', '13:10', 'KBM'),
        (8, '13:10', '13:50', 'KBM'), (9, '13:50', '14:30', 'KBM'),
        (10, '14:30', '15:10', 'KBM'),
    ],
    'selasa_kamis': [
        (1, '06:45', '07:30', 'KBM'), (2, '07:30', '08:15', 'KBM'),
        (3, '08:15', '09:00', 'KBM'), (4, '09:00', '09:45', 'KBM'),
        (None, '09:45', '10:00', 'Istirahat'), (5, '10:00', '10:45', 'KBM'),
        (6, '10:45', '11:30', 'KBM'), (None, '11:30', '12:15', 'Istirahat'),
        (7, '12:15', '13:00', 'KBM'), (8, '13:00', '13:45', 'KBM'),
        (9, '13:45', '14:30', 'KBM'), (10, '14:30', '15:15', 'KBM'),
    ],
    'jumat': [
        (1, '06:45', '07:20', '4K'), (2, '07:20', '08:00', 'KBM'),
        (3, '08:00', '08:40', 'KBM'), (4, '08:40', '09:20', 'KBM'),
        (None, '09:20', '09:35', 'Istirahat'), (5, '09:35', '10:15', 'KBM'),
        (6, '10:15', '10:55', 'KBM'), (7, '10:55', '11:30', 'KBM'),
        (None, '11:30', '13:00', 'Istirahat'), (8, '13:00', '13:40', 'KBM'),
        (9, '13:40', '14:20', 'KBM'),
    ],
}
DAYS = [('senin', 'Senin', 'senin'), ('selasa', 'Selasa', 'selasa_kamis'),
        ('rabu', 'Rabu', 'selasa_kamis'), ('kamis', 'Kamis', 'selasa_kamis'),
        ('jumat', 'Jumat', 'jumat')]

def minute(time):
    hour, mins = map(int, time.split(':'))
    assert 0 <= hour < 24 and 0 <= mins < 60
    return hour * 60 + mins

def summarize(segments):
    return {
        'numbered_periods': sum(s['period'] is not None for s in segments),
        'kbm_jp': sum(s['kbm_jp'] for s in segments),
        'kbm_minutes': sum(s['duration_minutes'] for s in segments if s['kind'] == 'kbm'),
        'break_minutes': sum(s['duration_minutes'] for s in segments if s['kind'] == 'break'),
        'fixed_minutes': sum(s['duration_minutes'] for s in segments if s['kind'] == 'fixed'),
        'elapsed_minutes': segments[-1]['end_minute'] - segments[0]['start_minute'],
    }

def block_signature(calendar, day_id, start_period, jp):
    """Deskripsi blok calon; belum menentukan kesetaraan domain HC4 final."""
    if not isinstance(jp, int) or jp < 1:
        raise ValueError('JP harus bilangan bulat positif')
    day = next(d for d in calendar['days'] if d['id'] == day_id)
    slots = {s['period']: s for s in day['segments'] if s['period'] is not None}
    requested = list(range(start_period, start_period + jp))
    if any(p not in slots or slots[p]['kind'] != 'kbm' for p in requested):
        raise ValueError('Blok di luar slot KBM atau menempati kegiatan tetap')
    selected = [slots[p] for p in requested]
    breaks = []
    for index, (left, right) in enumerate(zip(selected, selected[1:]), 1):
        if right['start_minute'] > left['end_minute']:
            breaks.append({'after_jp': index, 'minutes': right['start_minute']-left['end_minute']})
    return {'jp': jp, 'kbm_minutes': sum(s['duration_minutes'] for s in selected),
            'period_minutes': [s['duration_minutes'] for s in selected],
            'breaks': breaks, 'elapsed_minutes': selected[-1]['end_minute']-selected[0]['start_minute']}

def build():
    inventory_path = ROOT / 'data/inventory/2026-08-03/register.json'
    inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
    evidence = []
    expected_panel_hashes = None
    for source in inventory['files']:
        path = ROOT / source['frozen_path']
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == source['sha256'], 'Versi sumber berubah'
        for page in source['pages']:
            hashes = sorted(i['decoded_sha256'] for i in page['images'])
            expected_panel_hashes = expected_panel_hashes or hashes
            assert hashes == expected_panel_hashes and len(hashes) == 3
        evidence.append({'id': source['id'], 'path': source['frozen_path'], 'sha256': digest,
                         'reference_page': 1, 'equivalent_panel_pages': list(range(1, source['page_count']+1))})
    first = inventory['files'][0]['pages'][0]
    images_by_top = sorted(first['images'], key=lambda i: i['bbox'][1])
    panels = {key: {'reference_source': 'kelas', 'page': 1, 'bbox_pdf_points': image['bbox'],
                    'decoded_image_sha256': image['decoded_sha256']}
              for key, image in zip(PANELS, images_by_top)}
    result = {
        'schema_version': 1, 'calendar_id': 'sman8malang-2026-08-03',
        'effective_from': '2026-08-03', 'academic_year': '2026/2027',
        'status': 'transcribed_and_checked_against_pdf_not_school_confirmed',
        'method': 'Manual transcription checked visually against rendered source panel; arithmetic validated by script',
        'time_basis': 'School local clock; minutes since midnight; intervals [start,end)',
        'scope': 'Weekly source calendar, not actual class occupancy or annual holiday calendar',
        'sources': evidence, 'panels': panels,
        'four_k_legend': {'1': 'Kepenasehatan', '2': 'Kerohanian', '3': 'Kebersihan', '4': 'Kesehatan'},
        'four_k_fifth_friday': None,
        'four_k_legend_note': 'Label Jumat 1–4 ditranskripsi; pemetaan tanggal dan Jumat kelima belum dikonfirmasi',
        'days': [],
    }
    for day_id, name, panel in DAYS:
        segments = []
        for row, (period, start, end, label) in enumerate(PANELS[panel], 1):
            kind = 'kbm' if label == 'KBM' else 'break' if label == 'Istirahat' else 'fixed'
            segments.append({'id': f'{day_id}-{row:02d}', 'period': period,
                'start': start, 'end': end, 'start_minute': minute(start), 'end_minute': minute(end),
                'duration_minutes': minute(end)-minute(start), 'kind': kind, 'source_label': label,
                'kbm_jp': int(kind == 'kbm'), 'lesson_allowed': kind == 'kbm',
                'source_ref': {'panel': panel, 'body_row': row}})
        assert all(s['duration_minutes'] > 0 for s in segments)
        assert all(a['end_minute'] == b['start_minute'] for a, b in zip(segments, segments[1:]))
        periods = [s['period'] for s in segments if s['period'] is not None]
        assert periods == list(range(1, max(periods)+1))
        summary = summarize(segments)
        assert summary['elapsed_minutes'] == summary['kbm_minutes']+summary['break_minutes']+summary['fixed_minutes']
        result['days'].append({'id': day_id, 'name': name, 'panel': panel, 'segments': segments, 'summary': summary})
    result['weekly_summary'] = {key: sum(d['summary'][key] for d in result['days']) for key in result['days'][0]['summary']}
    return result

def validate_examples(cal):
    # Independent hand-calculated boundary cases protect HC4/HC5 preparation.
    assert cal['weekly_summary']['kbm_jp'] == 47
    assert cal['weekly_summary']['kbm_minutes'] == 2050
    assert block_signature(cal, 'senin', 2, 2)['kbm_minutes'] == 90
    assert block_signature(cal, 'jumat', 2, 2)['kbm_minutes'] == 80
    friday = block_signature(cal, 'jumat', 6, 3)
    assert friday == {'jp': 3, 'kbm_minutes': 115, 'period_minutes': [40,35,40],
                      'breaks': [{'after_jp': 2, 'minutes': 90}], 'elapsed_minutes': 205}
    assert block_signature(cal, 'senin', 4, 2)['breaks'] == [{'after_jp': 1, 'minutes': 15}]
    assert block_signature(cal, 'senin', 6, 2)['breaks'] == [{'after_jp': 1, 'minutes': 45}]
    for day, start, jp in [('senin',1,1), ('jumat',1,1), ('jumat',9,2), ('selasa',10,2)]:
        try:
            block_signature(cal, day, start, jp)
        except ValueError:
            continue
        raise AssertionError('Invalid block was accepted')

def main():
    cal = build()
    validate_examples(cal)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / 'calendar.json'
    target.write_text(json.dumps(cal, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    assert json.loads(target.read_text(encoding='utf-8')) == cal
    lines = ['# Kalender sumber — berlaku 3 Agustus 2026', '',
             'Transkripsi panel PDF, bukan rekap beban aktual kelas. Istirahat dan kegiatan tetap bernilai 0 JP KBM.', '']
    for day in cal['days']:
        lines.extend([f"## {day['name']}", '', '| Periode | Mulai | Selesai | Menit | Kegiatan | JP KBM |', '|---|---|---|---:|---|---:|'])
        for s in day['segments']:
            lines.append(f"| {s['period'] if s['period'] is not None else '—'} | {s['start']} | {s['end']} | {s['duration_minutes']} | {s['source_label']} | {s['kbm_jp']} |")
        lines.extend(['', f"Total kalender: {day['summary']['kbm_jp']} JP KBM, {day['summary']['kbm_minutes']} menit KBM.", ''])
    (OUT / 'calendar.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    report = {'status': 'passed', 'source_files_hash_verified': len(cal['sources']),
              'panel_pages_checked_against_inventory': sum(len(s['equivalent_panel_pages']) for s in cal['sources']),
              'checks': ['source SHA-256', 'three matching panel hashes on every inventoried page',
                         'positive duration', 'continuous non-overlapping segments', 'consecutive period numbers',
                         'daily time accounting', 'weekly totals', 'HC4 duration and break examples',
                         'HC5 fixed-slot and out-of-calendar rejection', 'JSON read-back'],
              'weekly_summary': cal['weekly_summary'],
              'limits': 'Calendar-only checks; no timetable event reconciliation or constraint evaluation yet'}
    (OUT / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
