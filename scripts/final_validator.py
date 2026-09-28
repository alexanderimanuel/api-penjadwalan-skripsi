"""Reference evaluation from canonical facts and actual clock intervals.
Never reads search scores, allowed domains, occupancy caches or neighbor state.
HC1/HC2 count unordered row pairs; HC4/HC5/HC6 count offending rows.
HC3 counts missing/extra event occurrences.
HC7 counts Friday rows past the grade last allowed period (X<=9, XI/XII<=7).
HC8 counts empty KBM periods strictly inside each class-day span (no holes).
"""
from collections import Counter, defaultdict
from collections.abc import Mapping
from itertools import combinations
from final_rules import combine, category, difficulty_key, friday_limit
from final_foundation import thaw


def _resource_ids(value):
    return isinstance(value, (list, tuple)) and bool(value) and all(isinstance(v, str) and v for v in value) and len(set(value)) == len(value)


def validate_placements(model, placements):
    """Schedule: ID -> (day, start_period), or placement rows to audit duplicates.
    Use validate() for full exported rows. Extra placement fields are rejected.
    """
    if isinstance(placements, Mapping):
        entries = []
        for eid, pos in placements.items():
            if isinstance(pos, (tuple, list)) and len(pos) == 2:
                entries.append({'event_id': eid, 'day': pos[0], 'start_period': pos[1]})
            else:
                entries.append({'event_id': eid, 'invalid_placement': thaw(pos)})
    else:
        entries = list(placements)
    rows = []
    for p in entries:
        if not isinstance(p, Mapping):
            rows.append({})
            continue
        eid = p.get('event_id')
        e = model.events.get(eid) if isinstance(eid, str) else None
        if e is None:
            rows.append(dict(p))
            continue
        start = p.get('start_period')
        row = {'event_id': eid, 'day': p.get('day'), 'start_period': start,
               'end_period': start + e['time']['jp'] - 1 if type(start) is int else None,
               'subject_id': e['subject_id'], 'class_ids': thaw(e['class_ids']),
               'teacher_codes': thaw(e['teacher_codes']), 'jp': e['time']['jp'],
               'kbm_minutes': e['time']['kbm_minutes'], 'breaks': thaw(e['time']['breaks']),
               'source_metadata': thaw(e['source_evidence'])}
        if set(p) - {'event_id', 'day', 'start_period'}:
            row['_unexpected_placement_fields'] = sorted(set(p) - {'event_id', 'day', 'start_period'}, key=str)
        rows.append(row)
    return validate(model, rows)


def validate(model, rows):
    events = model.events
    rows = [r if isinstance(r, Mapping) else {} for r in rows]
    h = {f'HC{i}': 0 for i in range(1, 9)}
    details = {k: [] for k in [*h, 'SC1', 'SC2', 'SC3']}
    ids = Counter(r.get('event_id') for r in rows if isinstance(r.get('event_id'), str))
    for eid in events:
        difference = abs(ids[eid] - 1)
        if difference:
            h['HC3'] += difference
            details['HC3'].append({'event_id': eid, 'expected': 1, 'observed': ids[eid], 'penalty': difference})
    occupancy = []
    busy = defaultdict(set)
    cbusy = defaultdict(set)
    meetings = defaultdict(list)
    high = defaultdict(set)
    for i, r in enumerate(rows):
        eid = r.get('event_id')
        e = events.get(eid) if isinstance(eid, str) else None
        if e is None:
            h['HC3'] += 1
            details['HC3'].append({'row': i, 'event_id': thaw(eid), 'problem': 'unknown/malformed event ID', 'penalty': 1})
            continue
        expected = {'class_ids': e['class_ids'], 'teacher_codes': e['teacher_codes'],
                    'subject_id': e['subject_id'], 'jp': e['time']['jp'],
                    'kbm_minutes': e['time']['kbm_minutes'], 'breaks': e['time']['breaks'],
                    'source_metadata': e['source_evidence']}
        changed = [k for k, v in expected.items() if r.get(k) != v]
        if r.get('_unexpected_placement_fields'):
            changed.extend(r['_unexpected_placement_fields'])
        if changed:
            h['HC6'] += 1
            details['HC6'].append({'row': i, 'event_id': eid, 'fields': changed,
                'expected': {k: thaw(expected[k]) for k in changed if k in expected},
                'observed': {k: thaw(r.get(k)) for k in changed}})

        problems = []
        start, end = r.get('start_period'), r.get('end_period')
        calendar_day = next((d for d in model.data['calendar']['days'] if d['id'] == r.get('day')), None)
        span = []
        crossed = []
        breaks = []
        if calendar_day is None:
            problems.append('unknown calendar day')
        elif type(start) is not int or type(end) is not int or start > end:
            problems.append('invalid start/end period')
        else:
            slots = {s['period']: s for s in calendar_day['segments'] if s['period'] is not None}
            span = [slots[p] for p in sorted(slots) if start <= p <= end]
            if len(span) != end-start+1:
                problems.append('span outside calendar or missing period')
            if span:
                a, b = span[0]['start_minute'], span[-1]['end_minute']
                crossed = [s for s in calendar_day['segments'] if s['start_minute'] < b and s['end_minute'] > a]
                breaks = [{'after_jp': j, 'minutes': b['start_minute']-a['end_minute']}
                          for j, (a, b) in enumerate(zip(span, span[1:]), 1) if b['start_minute'] > a['end_minute']]
        fixed = [s for s in crossed if s['kind'] == 'fixed']
        if fixed:
            h['HC5'] += 1
            details['HC5'].append({'row': i, 'event_id': eid, 'day': r.get('day'), 'segments': thaw(fixed)})
        actual_minutes = sum(s['end_minute']-s['start_minute'] for s in span if s['kind'] == 'kbm')
        if not span or any(s['kind'] != 'kbm' for s in span) or fixed:
            problems.append('event must occupy teaching slots and cannot cross fixed activities')
        if type(r.get('jp')) is not int or len(span) != r.get('jp') or r.get('jp') != e['time']['jp']:
            problems.append('JP mismatch')
        if type(r.get('kbm_minutes')) is not int or actual_minutes != r.get('kbm_minutes') or r.get('kbm_minutes') != e['time']['kbm_minutes']:
            problems.append('teaching minutes mismatch')
        if breaks != r.get('breaks') or r.get('breaks') != e['time']['breaks']:
            problems.append('break pattern mismatch')
        if problems:
            h['HC4'] += 1
            details['HC4'].append({'row': i, 'event_id': eid, 'day': r.get('day'),
                'start_period': start, 'end_period': end, 'reasons': problems,
                'actual_jp': len(span), 'actual_minutes': actual_minutes, 'actual_breaks': breaks})
        resources_valid = _resource_ids(r.get('teacher_codes')) and _resource_ids(r.get('class_ids'))
        if resources_valid and span:
            occupied = [(s['start_minute'], s['end_minute']) for s in span if s['kind'] == 'kbm']
            occupancy.append((i, r, occupied))
        if not problems and not changed and resources_valid:
            for t in r['teacher_codes']:
                busy[t, r['day']].update(s['period'] for s in span)
            for c in r['class_ids']:
                meetings[c, r['subject_id'], r['day']].append(eid)
                cbusy[c, r['day']].update(s['period'] for s in span)
                if category(model.rules, r['subject_id'], c) == 'HIGH':
                    high[c, r['day']].add(difficulty_key(model.rules, r['subject_id']))
            if r['day'] == 'jumat':
                last = r['end_period']
                over = [c for c in r['class_ids'] if last > friday_limit(model.rules, c)]
                if over:
                    h['HC7'] += 1
                    details['HC7'].append({'row': i, 'event_id': eid, 'day': 'jumat',
                                           'start_period': start, 'end_period': last,
                                           'class_ids': over, 'penalty': 1})
    for (i, a, aa), (j, b, bb) in combinations(occupancy, 2):
        if a['day'] != b['day']:
            continue
        overlaps = sorted({(max(x, u), min(y, v)) for x, y in aa for u, v in bb if max(x, u) < min(y, v)})
        if not overlaps:
            continue
        for code, field in [('HC1', 'teacher_codes'), ('HC2', 'class_ids')]:
            shared = sorted(set(a[field]) & set(b[field]))
            if shared:
                h[code] += 1
                details[code].append({'rows': [i, j], 'event_ids': [a['event_id'], b['event_id']],
                    'day': a['day'], 'shared_ids': shared, 'overlap_minutes': [list(v) for v in overlaps]})
    if any(h[k] for k in ['HC3', 'HC4', 'HC5', 'HC6']):
        # Undefined soft scores must not make structurally invalid payloads competitive.
        return {'HC': h, 'H': sum(h.values()), 'hard': {**h, 'total': sum(h.values())},
                'feasible': False, 'raw': None, 'normalized': None, 'soft_raw': None, 'soft_normalized': None,
                'S': None, 'F': None, 'validation_status': 'INVALID_TIMETABLE', 'details': details,
                'score_unavailable_reason': 'Structural/immutable violation: soft bounds require the canonical complete event set.'}
    for (teacher, day), used in sorted(busy.items()):
        calendar_day = next(d for d in model.data['calendar']['days'] if d['id'] == day)
        gaps = [s['period'] for s in calendar_day['segments'] if s['kind'] == 'kbm'
                and min(used) < s['period'] < max(used) and s['period'] not in used]
        if gaps:
            details['SC1'].append({'teacher_id': teacher, 'day': day, 'empty_kbm_periods': gaps, 'penalty': len(gaps)})
    for (c, day), used in sorted(cbusy.items()):
        if not used:
            continue
        calendar_day = next(d for d in model.data['calendar']['days'] if d['id'] == day)
        holes = [s['period'] for s in calendar_day['segments'] if s['kind'] == 'kbm'
                 and (s['period'] < max(used) if model.rules.get('class_start_first_kbm', False) else min(used) < s['period'] < max(used)) and s['period'] not in used]
        if holes:
            h['HC8'] += len(holes)
            details['HC8'].append({'class_id': c, 'day': day, 'empty_kbm_periods': holes, 'penalty': len(holes)})
    for (c, subject, day), event_ids in sorted(meetings.items()):
        n = len(event_ids)
        details['SC2'].append({'class_id': c, 'subject_id': subject, 'day': day, 'event_ids': event_ids,
                               'N': n, 'penalty': n*(n-1)//2})
    for (c, day), families in sorted(high.items()):
        limit = model.rules['difficulty']['daily_limit']
        details['SC3'].append({'class_id': c, 'day': day, 'high_families': sorted(families),
                               'K': len(families), 'daily_limit': limit, 'penalty': max(0, len(families)-limit)})
    raw = {code: sum(r['penalty'] for r in details[code]) for code in ('SC1', 'SC2', 'SC3')}
    raw = {code: sum(r['penalty'] for r in details[code]) for code in ('SC1', 'SC2', 'SC3')}
    return {**combine(h, raw, model.rules), 'validation_status': 'VALID_TIMETABLE', 'details': details,
            'normalization': thaw(model.rules['normalization']), 'weights': thaw(model.rules['weights'])}
