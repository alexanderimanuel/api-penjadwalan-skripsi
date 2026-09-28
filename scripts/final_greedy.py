"""Randomized greedy construction with occupancy forward-check and bounded backtracking.

Builds a complete placement from an empty schedule using only event domains
(raw input data); the school's existing timetable is never read. One restart
orders events most-constrained-first, places each event at a restricted
candidate list (top-R by additional H then F from partial scoring), keeps
resource occupancy for a cheap forward check, and unplaces a bounded number
of conflicting events when stuck. Best of RESTARTS restarts (by H then F) wins.
Deterministic per (method_tag, seed, restart).
"""
import random
from final_rules import stable_hash

RESTARTS = 5
TOP_R = 3
MAX_UNPLACE_TOTAL = 3000
EVAL_CAP = 25000


def periods_of(model, eid, position):
    day, start = position
    return day, set(range(start, start + model.events[eid]['time']['jp']))


def build_once(model, rng, stats):
    unplaced = set(model.ids)
    x = {}
    teacher_busy = {}
    class_busy = {}
    order = list(model.ids)
    order.sort(key=lambda e: len(model.domains[e]))
    trace = []
    unplaces = 0
    tried = {}

    def occupy(eid, day, periods, add=True):
        e = model.events[eid]
        for t in e['teacher_codes']:
            s = teacher_busy.setdefault((t, day), set())
            if add:
                s.update(periods)
            else:
                s.difference_update(periods)
        for c in e['class_ids']:
            s = class_busy.setdefault((c, day), set())
            if add:
                s.update(periods)
            else:
                s.difference_update(periods)

    def overlaps(eid, day, periods):
        e = model.events[eid]
        return any(periods & teacher_busy.get((t, day), set()) for t in e['teacher_codes']) or \
            any(periods & class_busy.get((c, day), set()) for c in e['class_ids'])

    def affected(eid):
        e = model.events[eid]
        teachers = set(e['teacher_codes'])
        classes = set(e['class_ids'])
        return [o for o in unplaced if o != eid and (
            teachers & set(model.events[o]['teacher_codes']) or classes & set(model.events[o]['class_ids']))]

    def forward_ok(placed_eid):
        for o in affected(placed_eid):
            if o not in unplaced:
                continue
            if not any(not overlaps(o, *periods_of(model, o, p)) for p in model.domains[o]):
                return False, o
        return True, None

    while unplaced:
        eid = next(e for e in order if e in unplaced)
        base = model.score(x, partial=True) if x else {'H': 0, 'F': 0.0}
        stats['evals'] += 1
        options = []
        for position in model.domains[eid]:
            if position in tried.get(eid, ()):
                continue
            candidate = dict(x)
            candidate[eid] = position
            score = model.score(candidate, partial=True)
            stats['evals'] += 1
            options.append(((score['H'] - base['H'], score['F'] - base['F']), position))
            if stats['evals'] >= EVAL_CAP:
                break
        if not options:
            tried.pop(eid, None)
            options = []
            for position in model.domains[eid]:
                candidate = dict(x)
                candidate[eid] = position
                score = model.score(candidate, partial=True)
                stats['evals'] += 1
                options.append(((score['H'] - base['H'], score['F'] - base['F']), position))
        options.sort(key=lambda o: o[0])
        keys = sorted({k for k, _ in options})
        shortlist = [p for k, p in options if k in keys[:TOP_R]]
        rng.shuffle(shortlist)
        placed = False
        for position in shortlist:
            day, ps = periods_of(model, eid, position)
            x[eid] = tuple(position)
            occupy(eid, day, ps, True)
            ok, blocker = forward_ok(eid)
            if ok:
                unplaced.discard(eid)
                tried.pop(eid, None)
                trace.append({'event_id': eid, 'placement': tuple(position)})
                placed = True
                break
            occupy(eid, day, ps, False)
            del x[eid]
        if placed or stats['evals'] >= EVAL_CAP:
            if not placed:
                key, position = options[0]
                day, ps = periods_of(model, eid, position)
                x[eid] = tuple(position)
                occupy(eid, day, ps, True)
                unplaced.discard(eid)
                trace.append({'event_id': eid, 'placement': tuple(position), 'forced': True})
            continue
        tried.setdefault(eid, set()).add(shortlist[0] if shortlist else options[0][1])
        if unplaces < MAX_UNPLACE_TOTAL:
            day, ps = periods_of(model, eid, options[0][1])
            mine = model.events[eid]
            conflicts = []
            for o in x:
                if o == eid:
                    continue
                oe = model.events[o]
                if not (set(oe['teacher_codes']) & set(mine['teacher_codes'])
                        or set(oe['class_ids']) & set(mine['class_ids'])):
                    continue
                od, ops = periods_of(model, o, x[o])
                if od == day and (ps & ops):
                    conflicts.append(o)
            for o in conflicts[:2]:
                od, ops = periods_of(model, o, x[o])
                occupy(o, od, ops, False)
                del x[o]
                unplaced.add(o)
                unplaces += 1
                if unplaces >= MAX_UNPLACE_TOTAL:
                    break
        else:
            key, position = options[0]
            day, ps = periods_of(model, eid, position)
            x[eid] = tuple(position)
            occupy(eid, day, ps, True)
            unplaced.discard(eid)
            trace.append({'event_id': eid, 'placement': tuple(position), 'forced': True})
        if stats['evals'] >= EVAL_CAP * 2:
            for rest in list(unplaced):
                position = None
                best = None
                restbase = model.score(x, partial=True) if x else {'H': 0, 'F': 0.0}
                stats['evals'] += 1
                for p in model.domains[rest]:
                    candidate = dict(x)
                    candidate[rest] = p
                    score = model.score(candidate, partial=True)
                    stats['evals'] += 1
                    k = (score['H'] - restbase['H'], score['F'] - restbase['F'])
                    if best is None or k < best:
                        best = k
                        position = p
                day, ps = periods_of(model, rest, position)
                x[rest] = tuple(position)
                occupy(rest, day, ps, True)
                unplaced.discard(rest)
                trace.append({'event_id': rest, 'placement': tuple(position), 'forced': True})
    stats['unplaces'] += unplaces
    return x, trace


def build(model, seed, method_tag='final-gst-v1', restarts=RESTARTS):
    """Best-of-restarts greedy construction. Returns (placement, summary)."""
    best = None
    summary = {'restarts': [], 'evals': 0, 'unplaces': 0}
    for r in range(restarts):
        rng = random.Random(int(stable_hash([method_tag, 'greedy', seed, r])[:16], 16))
        stats = {'evals': 0, 'unplaces': 0}
        x, trace = build_once(model, rng, stats)
        score = model.score(x)
        summary['evals'] += stats['evals']
        summary['unplaces'] += stats['unplaces']
        key = (score['H'], score['F'])
        summary['restarts'].append({'restart': r, 'H': score['H'], 'F': score['F'], 'steps': len(trace)})
        if best is None or key < best[0]:
            best = (key, x, trace, r)
        if summary['evals'] >= EVAL_CAP:
            break
    _, x, trace, winner = best
    summary['winner'] = winner
    summary['trace'] = [{'event_id': eid, 'placement': tuple(p)} for eid, p in x.items()]
    return {eid: tuple(p) for eid, p in x.items()}, summary
