import copy
import unittest
from unittest.mock import patch

from export_august import read_json
from final_rules import stable_hash
from school_experiment import CONFIG, ROOT, APPROVAL, select_school_inputs, load_approved_frozen


class SchoolSourceTests(unittest.TestCase):
    def test_actual_source_is_selected_without_modification(self):
        config = read_json(CONFIG)
        model, context = select_school_inputs(config)
        self.assertEqual(len(model.ids), 513)
        self.assertEqual(len(model.data['teachers']), 58)
        self.assertEqual(context['dataset_kind'], 'SCHOOL_PILOT')
        self.assertFalse(context['school_confirmed'])
        manifest_path = ROOT/config['school_manifest_path']
        manifest = read_json(manifest_path)
        source = read_json(manifest_path.parent/manifest['dataset_path'])
        self.assertEqual(model.dataset_hash, stable_hash(source))
        original = read_json(ROOT/'config/final-school.v1.json')
        for key in ('weights', 'normalization', 'difficulty', 'additional_constraints'):
            self.assertEqual(model.rules[key], original[key])

    def test_unapproved_existing_rules_are_not_silently_accepted(self):
        config = read_json(CONFIG)
        config['school_rules_path'] = 'config/final-school.v1.json'
        with self.assertRaisesRegex(ValueError, 'approval missing'):
            select_school_inputs(config)

    def test_wrong_dataset_size_cannot_fallback_to_synthetic(self):
        config = read_json(CONFIG); config['expected_school_events'] = 24
        with self.assertRaisesRegex(ValueError, 'synthetic fallback is forbidden'):
            select_school_inputs(config)

    def test_main_gate_only_relaxes_school_confirmation_for_explicit_assumption(self):
        model, _ = select_school_inputs(read_json(CONFIG))
        allowed = ['School group semantics are not confirmed']
        with patch('school_experiment.load_frozen', return_value=(model, {'ready_for_main_experiment': True}, [], allowed)):
            self.assertEqual(load_approved_frozen('unused')[0].rules['assignment_semantics_decision']['status'], APPROVAL)
        for blockers, ready in [(allowed+['Frozen code changed'], True), (allowed, False)]:
            with patch('school_experiment.load_frozen', return_value=(model, {'ready_for_main_experiment': ready}, [], blockers)):
                with self.assertRaises(ValueError): load_approved_frozen('unused')


if __name__ == '__main__': unittest.main()
