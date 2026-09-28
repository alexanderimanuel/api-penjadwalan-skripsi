"""Small intentionally invalid schedules; no school assumptions or SA experiment."""
import copy
import json
import random
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from final_foundation import Calendar, thaw
from final_rules import FinalModel, combine, raw_bounds
from final_validator import validate


def event(eid, teacher='t1', group='X-1', subject='S1', jp=1, breaks=()):
    return {'id': eid, 'subject_id': subject, 'class_ids': [group], 'teacher_codes': [teacher],
            'time': {'jp': jp, 'kbm_minutes': 40*jp,
                     'breaks': [{'after_jp': p, 'minutes': m} for p, m in breaks]},
            'source_evidence': {'fixture': True, 'pages': [1], 'nested': {'row': eid}}}


def fixture(events):
    sequence = [(1, 'kbm', 40), (2, 'kbm', 40), (None, 'break', 15),
                (3, 'kbm', 40), (4, 'kbm', 40), (5, 'fixed', 20),
                (6, 'kbm', 40), (None, 'break', 10), (7, 'kbm', 40), (8, 'kbm', 40)]
    days = []
    for day in ('d1', 'd2'):
        start = 480
        segments = []
        for period, kind, minutes in sequence:
            segments.append({'period': period, 'kind': kind, 'start_minute': start,
                             'end_minute': start+minutes, 'duration_minutes': minutes})
            start += minutes
        days.append({'id': day, 'segments': segments})
    data = {'id': 'FOUNDATION-SYNTHETIC', 'status': 'synthetic', 'events': events,
            'calendar': {'days': days},
            'teachers': [{'source_code': t} for t in sorted({t for e in events for t in e['teacher_codes']})]}
    rules = {'rules_version': 'TEST', 'weights_version': 'TEST', 'normalization_version': 'TEST',
             'reviewed_for_dataset': True, 'difficulty': {'version': 'TEST', 'source': 'synthetic test',
                'daily_limit': 1, 'by_grade': {'X': {e['subject_id']: 'HIGH' for e in events}}},
             'weights': {'SC1': .25, 'SC2': .25, 'SC3': .5},
             'normalization': {'method': 'divide_by_upper_bound', 'source': 'synthetic bounds'},
             'additional_constraints': {}}
    rules['normalization']['upper_bounds'] = raw_bounds(data, rules)
    return data, rules


def model(events):
    return FinalModel(*fixture(events))


class FoundationTests(unittest.TestCase):
    def test_01_exactly_one_teacher_collision_after_start_period(self):
        m = model([event('a', group='X-1', jp=2), event('b', group='X-2', jp=2, breaks=((1, 15),))])
        result = m.evaluate({'a': ('d1', 1), 'b': ('d1', 2)})
        self.assertEqual(result['hard'], {'HC1': 1, 'HC2': 0, 'HC3': 0, 'HC4': 0, 'HC5': 0, 'HC6': 0, 'HC7': 0, 'HC8': 0, 'total': 1})
        self.assertEqual(result['details']['HC1'][0]['overlap_minutes'], [[520, 560]])
        self.assertEqual(result['F'], 2+result['S'])
        self.assertEqual(m.score({'a': ('d1', 1), 'b': ('d1', 2)})['H'], 1)

    def test_02_exactly_one_class_collision(self):
        m = model([event('a', teacher='t1', jp=2), event('b', teacher='t2', jp=2, breaks=((1, 15),))])
        result = m.evaluate({'a': ('d1', 1), 'b': ('d1', 2)})
        self.assertEqual(result['hard'], {'HC1': 0, 'HC2': 1, 'HC3': 0, 'HC4': 0, 'HC5': 0, 'HC6': 0, 'HC7': 0, 'HC8': 0, 'total': 1})
        self.assertEqual(result['details']['HC2'][0]['shared_ids'], ['X-1'])

    def test_03_missing_duplicate_unknown_events(self):
        m = model([event('a'), event('b')])
        base = [{'event_id': 'a', 'day': 'd1', 'start_period': 1},
                {'event_id': 'b', 'day': 'd1', 'start_period': 2}]
        for rows in (base[:1], base+[base[0]], base+[{'event_id': 'unknown'}]):
            with self.subTest(rows=rows):
                r = m.evaluate(rows)
                self.assertEqual(r['hard']['HC3'], 1)
                self.assertFalse(r['feasible'])
                self.assertTrue(r['details']['HC3'])
                self.assertIsNone(r['F'])

    def test_04_illegal_domain_and_duration(self):
        m = model([event('a', jp=2)])
        for pos in [('d1', 2), ('d1', 8), ('unknown', 1), ('d1', True), ('d1', 999999999)]:
            with self.subTest(pos=pos):
                self.assertEqual(m.evaluate({'a': pos})['hard']['HC4'], 1)
        for field, value in [('jp', 3), ('kbm_minutes', 79), ('breaks', [{'after_jp': 1, 'minutes': 15}])]:
            rows = m.timetable({'a': ('d1', 1)})
            rows[0][field] = value
            r = validate(m, rows)
            self.assertEqual(r['hard']['HC4'], 1)
            self.assertEqual(r['hard']['HC6'], 1)

    def test_05_fixed_collision_inside_multiperiod_event(self):
        m = model([event('a', jp=2)])
        r = m.evaluate({'a': ('d1', 4)})
        self.assertEqual(r['hard']['HC5'], 1)
        self.assertEqual(r['details']['HC5'][0]['segments'][0]['period'], 5)
        self.assertNotIn(('d1', 4), m.domains['a'])

    def test_06_each_immutable_attribute_checked(self):
        m = model([event('a')])
        mutations = {'subject_id': 'other', 'class_ids': ['X-other'], 'teacher_codes': ['other'],
                     'jp': 2, 'kbm_minutes': 39, 'breaks': [{'after_jp': 1, 'minutes': 15}],
                     'source_metadata': {'fabricated': True}}
        for field, value in mutations.items():
            with self.subTest(field=field):
                rows = m.timetable({'a': ('d1', 1)})
                rows[0][field] = value
                r = validate(m, rows)
                self.assertEqual(r['hard']['HC6'], 1)
                self.assertIn(field, r['details']['HC6'][0]['fields'])

    def test_07_exactly_one_internal_teacher_gap(self):
        m = model([event('a'), event('b')])
        r = m.evaluate({'a': ('d1', 1), 'b': ('d1', 3)})
        self.assertEqual(r['soft_raw']['SC1'], 1)
        self.assertEqual(r['details']['SC1'][0]['empty_kbm_periods'], [2])
        for a, b in [(2, 3), (4, 6), (6, 7)]:
            self.assertEqual(m.evaluate({'a': ('d1', a), 'b': ('d1', b)})['soft_raw']['SC1'], 0)

    def test_08_sc2_n_one_two_three(self):
        for n, expected in [(1, 0), (2, 1), (3, 3)]:
            with self.subTest(N=n):
                m = model([event(str(i)) for i in range(n)])
                r = m.evaluate({str(i): ('d1', i+1) for i in range(n)})
                self.assertEqual(r['soft_raw']['SC2'], expected)
                self.assertEqual(r['details']['SC2'][0]['N'], n)

    def test_09_sc3_three_distinct_high_limit_one(self):
        m = model([event(str(i), subject=f'S{i}') for i in range(3)])
        r = m.evaluate({str(i): ('d1', i+1) for i in range(3)})
        self.assertEqual(r['soft_raw']['SC3'], 2)
        self.assertEqual(r['details']['SC3'][0]['K'], 3)
        self.assertEqual(r['details']['SC3'][0]['daily_limit'], 1)

    def test_10_feasible_schedule_and_auditable_schema(self):
        m = model([event('a'), event('b')])
        r = m.evaluate({'a': ('d1', 1), 'b': ('d1', 2)})
        self.assertEqual(r['hard']['total'], 0)
        self.assertTrue(r['feasible'])
        self.assertEqual(r['soft_raw'], {'SC1': 0, 'SC2': 1, 'SC3': 0})
        self.assertEqual(r['soft_normalized'], {'SC1': 0, 'SC2': 1, 'SC3': 0})
        self.assertEqual(r['S'], .25)
        self.assertEqual(r['F'], .25)
        self.assertEqual(r['HC'], {k: v for k, v in r['hard'].items() if k != 'total'})
        json.dumps(r, allow_nan=False)

    def test_11_hard_dominates_entire_soft_range(self):
        m = model([event('a'), event('b', subject='S2'), event('c')])
        zero = {f'HC{i}': 0 for i in range(1, 7)}
        upper = thaw(m.rules['normalization']['upper_bounds'])
        worst_soft = combine(zero, upper, m.rules)
        one_hard = combine({**zero, 'HC1': 1}, dict.fromkeys(upper, 0), m.rules)
        self.assertEqual(worst_soft['S'], 1)
        self.assertEqual(one_hard['S'], 0)
        self.assertEqual(worst_soft['F'], 1)
        self.assertEqual(one_hard['F'], 2)
        self.assertLess(worst_soft['F'], one_hard['F'])
        for h in range(5):
            self.assertLess(combine({**zero, 'HC1': h}, upper, m.rules)['F'],
                            combine({**zero, 'HC1': h+1}, dict.fromkeys(upper, 0), m.rules)['F'])

    def test_registry_deep_immutability_and_input_aliases(self):
        data, rules = fixture([event('a', jp=2)])
        m = FinalModel(data, rules)
        original = m.dataset_hash
        data['events'][0]['teacher_codes'][0] = 'bad'
        rules['difficulty']['daily_limit'] = 99
        self.assertEqual(m.events['a']['teacher_codes'], ['t1'])
        self.assertEqual(m.rules['difficulty']['daily_limit'], 1)
        with self.assertRaises(TypeError): m.events['a']['teacher_codes'][0] = 'bad'
        with self.assertRaises(TypeError): m.events['a']['time']['jp'] = 99
        with self.assertRaises(TypeError): m.events['a']['source_evidence']['pages'][0] = 99
        with self.assertRaises(TypeError): m.events['a']['source_evidence']['nested']['row'] = 'bad'
        with self.assertRaises(TypeError): m.events['a'] = {}
        with self.assertRaises(AttributeError): m.events = {}
        with self.assertRaises(FrozenInstanceError): m.event_registry['a'].duration_jp = 99
        with self.assertRaises(FrozenInstanceError): m.event_registry['a'].event_id = 'other'
        with self.assertRaises(TypeError): m.event_registry['a'].source_metadata['fixture'] = False
        with self.assertRaises(TypeError): m.data['events'][0]['class_ids'][0] = 'bad'
        with self.assertRaises(TypeError): m.rules['weights']['SC1'] = 99
        self.assertEqual(m.dataset_hash, original)
        self.assertEqual(copy.deepcopy(m).events, m.events)

    def test_calendar_and_domain_are_explicit_and_read_only(self):
        m = model([event('a', jp=2, breaks=((1, 15),))])
        self.assertEqual({s.kind for s in m.calendar.days['d1']}, {'kbm', 'break', 'fixed'})
        self.assertEqual(m.domains['a'], [('d1', 2), ('d2', 2)])
        self.assertTrue(m.evaluate({'a': ('d1', 2)})['feasible'])
        with self.assertRaises(FrozenInstanceError): m.calendar.days['d1'][0].end_minute = 999
        with self.assertRaises(TypeError): m.data['calendar']['days'][0]['segments'][0]['kind'] = 'fixed'

    def test_fixed_activity_without_period_cannot_be_crossed(self):
        data, rules = fixture([event('a', jp=2, breaks=((1, 15),))])
        data['calendar']['days'][0]['segments'][2]['kind'] = 'fixed'
        m = FinalModel(data, rules)
        self.assertNotIn(('d1', 2), m.domains['a'])
        r = m.evaluate({'a': ('d1', 2)})
        self.assertEqual(r['hard']['HC5'], 1)
        self.assertEqual(r['hard']['HC4'], 1)

    def test_bad_calendars_fail_before_search(self):
        data, _ = fixture([event('a')])
        for field, value in [('start_minute', 519), ('start_minute', 521), ('period', 1), ('duration_minutes', 39)]:
            bad = copy.deepcopy(data['calendar'])
            bad['days'][0]['segments'][1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): Calendar.from_source(bad)

    def test_validator_ignores_all_search_derived_state(self):
        m = model([event('a', jp=2), event('b', group='X-2', jp=2, breaks=((1, 15),))])
        x = {'a': ('d1', 1), 'b': ('d1', 2)}
        expected = m.evaluate(x)
        m.domains = {}; m.allowed = {}; m.active = {}; m.days = {}
        with patch.object(m, 'score', side_effect=AssertionError('search scorer used')):
            with patch.object(Calendar, 'valid_starts', side_effect=AssertionError('domain generator used')):
                self.assertEqual(m.evaluate(x), expected)

    def test_pairs_count_once_across_multiple_periods_and_resources(self):
        events = [event('a', jp=2), event('b', jp=2)]
        for e in events:
            e['teacher_codes'] = ['t1', 't2']; e['class_ids'] = ['X-1', 'X-2']
        m = model(events)
        r = m.evaluate({'a': ('d1', 1), 'b': ('d1', 1)})
        self.assertEqual(r['hard']['HC1'], 1)
        self.assertEqual(r['hard']['HC2'], 1)
        self.assertEqual(len(r['details']['HC1'][0]['overlap_minutes']), 2)

    def test_placement_only_and_malformed_payloads_are_not_silently_accepted(self):
        m = model([event('a')])
        r = m.evaluate([{'event_id': 'a', 'day': 'd1', 'start_period': 1, 'teacher_codes': ['bad']}])
        self.assertEqual(r['hard']['HC6'], 1)
        with self.assertRaises(ValueError): m.score({'a': {'day': 'd1', 'start_period': 1}})
        with self.assertRaises(ValueError): m.timetable({'unknown': ('d1', 1)})
        for rows in ([None], [{'event_id': []}], [{'event_id': 'a', 'class_ids': [[]], 'teacher_codes': None}]):
            self.assertFalse(validate(m, rows)['feasible'])

    def test_random_complete_states_match_search_score(self):
        m = model([event('a', jp=2), event('b', group='X-2'), event('c', teacher='t2', subject='S2')])
        rng = random.Random(83)
        for _ in range(100):
            x = {eid: rng.choice(m.domains[eid]) for eid in m.ids}
            direct = m.evaluate(x)
            search = m.score(x)
            for key in ('hard', 'soft_raw', 'soft_normalized', 'S', 'F', 'feasible'):
                self.assertEqual(direct[key], search[key])

    def test_school_snapshot_baseline_and_all_domains_preserved(self):
        from final_cli import load
        from model_august import ROOT, domain
        data, rules = load(ROOT/'config/final-school.user-approved.v1.json')
        m = FinalModel(data, rules)
        self.assertEqual(len(m.events), 513)
        for e in data['events']:
            old_domain = [(p['day'], p['start_period']) for p in domain(e, data['calendar'])]
            self.assertEqual(m.domains[e['id']], old_domain)
        x = {eid: (e['reference']['day'], e['reference']['start_period']) for eid, e in m.events.items()}
        result = m.evaluate(x)
        self.assertEqual(result['hard']['total'], 1)
        self.assertEqual(result['hard']['HC1'], 1)
        self.assertEqual(result['soft_raw'], {'SC1': 246, 'SC2': 0, 'SC3': 17})
        self.assertAlmostEqual(result['S'], .08349667799056008)
        rng = random.Random(20260921)
        for state in [x]+[{eid: rng.choice(m.domains[eid]) for eid in m.ids} for _ in range(3)]:
            reference, search = m.evaluate(state), m.score(state)
            for key in ('hard', 'soft_raw', 'soft_normalized', 'S', 'F', 'feasible'):
                self.assertEqual(reference[key], search[key])


if __name__ == '__main__':
    unittest.main()
