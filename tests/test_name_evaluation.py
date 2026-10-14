import unittest

from evaluate_company_names import evaluate


class NameEvaluationTests(unittest.TestCase):
    def sample(self, **changes):
        sample = {'sample_id': 'run:example.com', 'domain': 'example.com',
                  'before_name': 'Product | Example Brand',
                  'after': {'company_name': 'Example Brand', 'name_status': 'official_observed'},
                  'developer_expected_name': 'Example Brand', 'source_sha256': {},
                  'pages': [{'url': 'https://example.com/', 'text': 'Example Brand is a supplier.'}]}
        sample.update(changes)
        return sample

    def test_developer_answers_do_not_become_human_gold(self):
        result = evaluate([self.sample()])
        self.assertEqual(result['human_gold']['total'], 0)
        self.assertEqual(result['developer_evidence_review'], {'total': 1, 'before_correct': 0, 'after_correct': 1})
        self.assertEqual(result['dataset_role'], 'development_regression_not_independent_test')

    def test_human_answer_requires_reviewer_and_saved_exact_quote(self):
        label = {'expected_name': 'Example Brand', 'reviewer': 'test reviewer',
                 'name_evidence': {'url': 'https://example.com/', 'quote': 'Example Brand is a supplier.'}}
        result = evaluate([self.sample()], {'run:example.com': label})
        self.assertEqual(result['human_gold'], {'total': 1, 'before_correct': 0, 'after_correct': 1})
        self.assertEqual(result['developer_evidence_review']['total'], 0)
        for bad in [dict(label, reviewer=''), dict(label, expected_name='Invented Brand'),
                    dict(label, name_evidence={'url': 'https://foreign.com/', 'quote': 'Example Brand'})]:
            with self.assertRaises(ValueError):
                evaluate([self.sample()], {'run:example.com': bad})

    def test_review_name_is_not_counted_as_confirmed_correct(self):
        result = evaluate([self.sample(after={'company_name': 'Example Brand', 'name_status': 'needs_review'})])
        self.assertEqual(result['developer_evidence_review']['after_correct'], 0)

    def test_unlabeled_samples_are_counted_separately(self):
        result = evaluate([self.sample(developer_expected_name=None,
                           after={'company_name': 'example', 'name_status': 'unknown'})])
        self.assertEqual(result['unlabeled_after_statuses'], {'unknown': 1})
        self.assertEqual(result['developer_evidence_review']['total'], 0)

    def test_unknown_label_id_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate([self.sample()], {'other:example.com': {}})
