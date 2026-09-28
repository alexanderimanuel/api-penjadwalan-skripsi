"""Immutable canonical facts and explicit, half-open school-clock intervals."""
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


class FrozenList(tuple):
    """Read-only JSON sequence; equality remains compatible with legacy lists."""
    def __eq__(self, other):
        return isinstance(other, (list, tuple)) and tuple.__eq__(self, tuple(other))

    def __ne__(self, other):
        return not self == other

    __hash__ = tuple.__hash__


class FrozenMap(Mapping):
    __slots__ = ('_values',)

    def __init__(self, values):
        object.__setattr__(self, '_values', MappingProxyType({k: freeze(v) for k, v in values.items()}))

    def __setattr__(self, name, value):
        raise TypeError('Canonical facts are immutable')

    def __delattr__(self, name):
        raise TypeError('Canonical facts are immutable')

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __deepcopy__(self, memo):
        return self


def freeze(value):
    if isinstance(value, FrozenMap):
        return value
    if isinstance(value, Mapping):
        return FrozenMap(value)
    if isinstance(value, (list, tuple)):
        return FrozenList(freeze(v) for v in value)
    return value


def thaw(value):
    """Detached JSON payload, never a writable alias into the registry."""
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(v) for v in value]
    return value


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    subject_id: str
    class_ids: tuple
    teacher_ids: tuple
    duration_jp: int
    duration_minutes: int
    break_pattern: tuple
    source_metadata: FrozenMap

    @classmethod
    def from_source(cls, row):
        for key in ('id', 'subject_id'):
            if not isinstance(row[key], str) or not row[key]:
                raise ValueError(f'Invalid event {key}')
        for key in ('class_ids', 'teacher_codes'):
            values = row[key]
            if not isinstance(values, (list, tuple)) or not values or any(not isinstance(v, str) or not v for v in values) or len(set(values)) != len(values):
                raise ValueError(f'Invalid event {key}')
        timing = row['time']
        if any(type(timing[k]) is not int or timing[k] <= 0 for k in ('jp', 'kbm_minutes')):
            raise ValueError('Event duration must be a positive integer')
        pattern = tuple((b['after_jp'], b['minutes']) for b in timing['breaks'])
        if any(type(p) is not int or type(m) is not int or not 0 < p < timing['jp'] or m <= 0 for p, m in pattern):
            raise ValueError('Invalid event break pattern')
        if list(pattern) != sorted(set(pattern)) or len({p for p, _ in pattern}) != len(pattern):
            raise ValueError('Duplicate/unordered event breaks')
        if not isinstance(row['source_evidence'], Mapping):
            raise ValueError('Source metadata must be a mapping')
        return cls(row['id'], row['subject_id'], tuple(row['class_ids']), tuple(row['teacher_codes']),
                   timing['jp'], timing['kbm_minutes'], pattern, freeze(row['source_evidence']))


@dataclass(frozen=True, slots=True)
class TimeSegment:
    day: str
    period: int | None
    kind: str
    start_minute: int
    end_minute: int


@dataclass(frozen=True, slots=True)
class Calendar:
    days: FrozenMap

    @classmethod
    def from_source(cls, source):
        days = {}
        for day in source['days']:
            day_id = day['id']
            if not isinstance(day_id, str) or not day_id or day_id in days:
                raise ValueError('Invalid/duplicate calendar day')
            segments = []
            periods = []
            for s in day['segments']:
                a, b, p, kind = s['start_minute'], s['end_minute'], s['period'], s['kind']
                if type(a) is not int or type(b) is not int or not 0 <= a < b <= 1440:
                    raise ValueError('Invalid clock interval')
                if kind not in ('kbm', 'break', 'fixed') or (p is not None and (type(p) is not int or p < 1)):
                    raise ValueError('Invalid segment kind/period')
                if kind == 'kbm' and p is None or kind == 'break' and p is not None:
                    raise ValueError('Teaching slots need periods; breaks do not have periods')
                if s.get('duration_minutes', b-a) != b-a:
                    raise ValueError('Clock and duration_minutes disagree')
                if segments and segments[-1].end_minute != a:
                    raise ValueError('Calendar must explicitly cover gaps with breaks/fixed activities; no overlaps')
                if p is not None:
                    periods.append(p)
                segments.append(TimeSegment(day_id, p, kind, a, b))
            if not segments or periods != sorted(set(periods)):
                raise ValueError('Empty day or duplicate/unordered periods')
            days[day_id] = tuple(segments)
        if not days:
            raise ValueError('Empty calendar')
        return cls(FrozenMap(days))

    def valid_starts(self, event):
        positions = []
        for day, segments in self.days.items():
            slots = {s.period: s for s in segments if s.period is not None}
            for first in segments:
                if first.kind != 'kbm':
                    continue
                span = [slots.get(p) for p in range(first.period, first.period + event.duration_jp)]
                if any(s is None or s.kind != 'kbm' for s in span):
                    continue
                crossed = [s for s in segments if s.start_minute < span[-1].end_minute and s.end_minute > first.start_minute]
                if any(s.kind == 'fixed' for s in crossed):
                    continue
                pattern = tuple((i, b.start_minute-a.end_minute) for i, (a, b) in enumerate(zip(span, span[1:]), 1) if b.start_minute > a.end_minute)
                if sum(s.end_minute-s.start_minute for s in span) == event.duration_minutes and pattern == event.break_pattern:
                    positions.append((day, first.period))
        return tuple(positions)
