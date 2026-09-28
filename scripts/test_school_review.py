from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest

from build_school_review import build, grid, load_data, teacher_codes, redact_score
from final_validator import validate


class SchoolReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model,cls.schedules,_,_,_=load_data()
        cls.codes=teacher_codes(cls.model)

    def test_teacher_collision_remains_visible_in_baseline(self):
        g,rows=grid(self.model,self.schedules['BASELINE'],'guru','39',self.codes)
        hit=[r for r in rows if r['day']=='rabu' and r['period']==7][0]
        self.assertEqual(hit['occupancy_state'],'conflict')
        self.assertEqual(len(hit['event_ids']),2)
        self.assertEqual(set(hit['class_ids']),{'XI-1','XII-9'})

    def test_break_fixed_and_zero_load_teacher_are_distinct(self):
        g,rows=grid(self.model,self.schedules['BASELINE'],'guru','1',self.codes)
        self.assertEqual(g['resource_id'],'G001')
        self.assertEqual({r['segment_kind'] for r in rows},{'kbm','break','fixed'})
        self.assertTrue(all(r['occupancy_state']=='empty' for r in rows if r['segment_kind']=='kbm'))
        self.assertTrue(all(not r['event_ids'] for r in rows if r['segment_kind'] in ('break','fixed')))

    def test_class_view_expands_every_event_jp_without_mutation(self):
        x=self.schedules['A'];before=json.dumps(x,sort_keys=True)
        _,rows=grid(self.model,x,'kelas','X-1',self.codes)
        actual=Counter(e for r in rows for e in r['event_ids'])
        expected={eid:e['time']['jp'] for eid,e in self.model.events.items() if 'X-1' in e['class_ids']}
        self.assertEqual(dict(actual),expected)
        self.assertEqual(json.dumps(x,sort_keys=True),before)

    def test_score_redaction_keeps_penalties(self):
        source=validate(self.model,self.model.timetable(self.schedules['BASELINE']))
        redacted=redact_score(source,self.codes)
        self.assertEqual(source['raw'],redacted['raw'])
        self.assertEqual(redacted['details']['HC1'][0]['shared_ids'],['G039'])
        self.assertEqual(source['details']['HC1'][0]['shared_ids'],['39'])

    def test_complete_package_blank_review_privacy_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'review';build(output)
            review=json.loads((output/'school-review-input.json').read_text(encoding='utf-8'))
            self.assertIsNone(review['final_choice'])
            for r in review['ratings']:
                self.assertTrue(all(v is None for k,v in r.items() if k!='schedule'))
            for label in ['BASELINE','A','B','C']:
                self.assertTrue((output/label/'jadwal-kelas.html').exists())
                self.assertTrue((output/label/'jadwal-guru.html').exists())
                rows=json.loads((output/label/'timetable.json').read_text(encoding='utf-8'))
                self.assertEqual(len(rows),513)
                self.assertTrue(all('source_metadata' not in r for r in rows))
                self.assertTrue(all(t.startswith('G') for r in rows for t in r['teacher_codes']))
            text=''.join(p.read_text(encoding='utf-8') for p in output.rglob('*') if p.is_file())
            for t in self.model.data['teachers']:self.assertNotIn(t['display_name'],text)
            with self.assertRaisesRegex(ValueError,'reviewer responses'):build(output)


if __name__=='__main__':unittest.main()
