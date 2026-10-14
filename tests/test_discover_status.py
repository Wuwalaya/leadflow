"""Technical status regression tests with synthetic fixtures and no network."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import leadflow


def offline_api(scenario, requests):
    class OfflineAPI:
        def __init__(self, out, max_calls=90):
            self.calls, self.max_calls, self.jev_batches = 0, max_calls, 0

        def post(self, endpoint, key, payload):
            if self.calls >= self.max_calls:
                raise leadflow.APIBudgetExhausted('API call budget exhausted')
            self.calls += 1
            requests.append((endpoint, payload))
            if endpoint == leadflow.TAVILY_SEARCH_ENDPOINT:
                if scenario == 'all_search_failed' or payload['query'] == 'fail':
                    raise RuntimeError('Offline search failure')
                if scenario == 'empty_search':
                    return {'results': []}
                domains = ['first.example.com']
                if scenario in {'partial_extract', 'partial_jev', 'two_candidates'}:
                    domains.append('second.example.com')
                return {'results': [
                    {'url': f'https://{domain}/products', 'title': 'Offline fixture',
                     'content': 'Stainless steel sheet distributor', 'score': .9}
                    for domain in domains
                ]}
            if endpoint == leadflow.TAVILY_EXTRACT_ENDPOINT:
                results, failures = [], []
                for url in payload['urls']:
                    if scenario == 'all_extract_failed' or (scenario == 'partial_extract' and 'second.' in url):
                        failures.append({'url': url, 'error': 'Offline extraction failure'})
                    else:
                        text = ('# Offline fixture\n'
                                'We distribute stainless steel sheet and plate to industrial customers in Brazil.\n')
                        if scenario == 'no_evidence':
                            text = '# Offline fixture\nWelcome to our website. Contact form only.'
                        results.append({'url': url, 'raw_content': text})
                return {'results': results, 'failed_results': failures}
            if endpoint == leadflow.JEV_ENDPOINT:
                self.jev_batches += 1
                if scenario == 'all_jev_failed' or (scenario == 'partial_jev' and self.jev_batches == 2):
                    raise RuntimeError('Offline Jev failure')
                answers = {
                    name: ({'type': 'choice', 'choice': 'distributor', 'confidence': .9,
                            'probabilities': {role: .9 if role == 'distributor' else .1 if role == 'unknown' else 0
                                              for role in question['criteria']}}
                           if question['type'] == 'choice' else {'type': 'noul', 'noul': .9})
                    for name, question in payload['questions'].items()
                }
                if scenario == 'low_match':
                    answers['c1_product']['noul'] = .1
                return {'answers': answers}
            raise AssertionError('Unexpected endpoint')
    return OfflineAPI


class DiscoverStatusTests(unittest.TestCase):
    def run_discover(self, scenario='normal', extra_args=(), queries=('offline',)):
        requests, stdout, stderr = [], io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            query_file = root / 'queries.json'
            query_file.write_text(json.dumps(list(queries)), encoding='utf-8')
            args = ['leadflow.py', 'discover', '--out', str(root / 'out'),
                    '--db', str(root / 'test.sqlite'), '--query-file', str(query_file),
                    '--supplemental-limit', '0', '--jev-batch-size', '1', '--target', '15']
            with patch('sys.argv', args + list(extra_args)), patch('leadflow.load_env_keys') as load_env, \
                 patch.dict('os.environ', {'TAVILY_API_KEY': 'offline', 'TYPESAFE_API_KEY': 'offline'}), \
                 patch('leadflow.API', offline_api(scenario, requests)), \
                 patch('leadflow.urlopen', side_effect=AssertionError('Network forbidden')), \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = leadflow.main()
            load_env.assert_called_once()  # Mocked: never reads the actual .env.
            summary = json.loads((root / 'out' / 'summary.json').read_text(encoding='utf-8'))
            records = json.loads((root / 'out' / 'leads.json').read_text(encoding='utf-8'))
            return code, summary, records, requests, stdout.getvalue()

    def test_completed_with_unmet_business_target_returns_zero(self):
        code, summary, records, _, stdout = self.run_discover()
        self.assertEqual(code, 0)
        self.assertEqual(summary['run_status'], 'completed')
        self.assertFalse(summary['target_met'])
        self.assertEqual(summary['final_qualified_leads'], 1)
        self.assertTrue(summary['pipeline_coverage']['complete'])
        self.assertEqual(records[0]['qualification_status'], 'HIGH')
        self.assertIn('Technical run status: completed', stdout)
        self.assertIn('Target: 15 | Met: NO', stdout)

    def test_completed_low_classification_is_usable_without_qualified_leads(self):
        code, summary, records, _, _ = self.run_discover('low_match')
        self.assertEqual((code, summary['run_status']), (0, 'completed'))
        self.assertEqual(summary['final_qualified_leads'], 0)
        self.assertEqual(records[0]['qualification_status'], 'LOW')

    def test_all_jev_batches_failed_returns_nonzero_despite_audit_records(self):
        code, summary, records, _, stdout = self.run_discover('all_jev_failed')
        self.assertEqual((code, summary['run_status']), (1, 'failed'))
        self.assertEqual(records[0]['qualification_status'], 'UNCLASSIFIED')
        self.assertEqual(summary['stages']['jev']['requests_succeeded'], 0)
        self.assertEqual(summary['stages']['jev']['status'], 'failed')
        self.assertFalse(summary['pipeline_coverage']['complete'])
        self.assertIn('Technical run status: failed', stdout)

    def test_partial_extraction_keeps_usable_results_and_returns_nonzero(self):
        code, summary, records, _, stdout = self.run_discover('partial_extract', ['--target', '1'])
        self.assertEqual((code, summary['run_status']), (1, 'partial'))
        self.assertTrue(summary['target_met'])
        self.assertEqual(len(records), 1)
        self.assertEqual(summary['stages']['extract_primary']['items_failed'], 1)
        self.assertEqual(summary['stages']['extract_primary']['status'], 'partial')
        self.assertIn('Technical run status: partial', stdout)
        self.assertIn('Target: 1 | Met: YES', stdout)

    def test_budget_exhausted_before_jev_is_failed(self):
        code, summary, records, requests, _ = self.run_discover('normal', ['--max-api-calls', '2'])
        self.assertEqual((code, summary['run_status']), (1, 'failed'))
        self.assertEqual(len(requests), 2)
        self.assertEqual(summary['jev_calls_including_retries'], 0)
        self.assertEqual(records[0]['qualification_status'], 'UNCLASSIFIED')
        self.assertIn('budget exhausted', summary['errors'][0]['error'])

    def test_budget_exhausted_after_one_classification_is_partial(self):
        code, summary, records, requests, _ = self.run_discover('two_candidates', ['--max-api-calls', '3'])
        self.assertEqual((code, summary['run_status']), (1, 'partial'))
        self.assertEqual(len(requests), 3)
        self.assertEqual({record['qualification_status'] for record in records}, {'HIGH', 'UNCLASSIFIED'})

    def test_empty_search_completes_and_skips_downstream_without_smoke_coverage(self):
        code, summary, records, requests, stdout = self.run_discover('empty_search')
        self.assertEqual((code, summary['run_status']), (0, 'completed'))
        self.assertFalse(summary['target_met'])
        self.assertEqual(records, [])
        self.assertEqual(len(requests), 1)
        self.assertEqual(summary['stages']['search']['status'], 'completed')
        self.assertEqual(summary['stages']['extract_primary']['status'], 'skipped')
        self.assertEqual(summary['stages']['extract_primary']['reason'], 'no_candidate_urls')
        self.assertEqual(summary['stages']['evidence_validation']['status'], 'skipped')
        self.assertEqual(summary['stages']['jev']['status'], 'skipped')
        self.assertFalse(summary['pipeline_coverage']['complete'])
        self.assertIn('Full pipeline coverage (Search/Extract/Jev): NO', stdout)
        self.assertIn('Skipped stage: jev', stdout)

    def test_no_valid_official_evidence_skips_jev_without_fabricated_success(self):
        code, summary, records, requests, stdout = self.run_discover('no_evidence')
        self.assertEqual((code, summary['run_status']), (0, 'completed'))
        self.assertEqual(records, [])
        self.assertEqual(len(requests), 2)
        self.assertEqual(summary['stages']['evidence_validation']['rejected_count'], 1)
        self.assertEqual(summary['stages']['jev']['status'], 'skipped')
        self.assertEqual(summary['stages']['jev']['reason'], 'no_valid_official_evidence')
        self.assertFalse(summary['pipeline_coverage']['complete'])
        self.assertEqual(summary['jev_calls_including_retries'], 0)
        self.assertIn('no_valid_official_evidence', stdout)

    def test_all_extractions_failed_is_failed_and_jev_is_skipped(self):
        code, summary, records, _, _ = self.run_discover('all_extract_failed')
        self.assertEqual((code, summary['run_status']), (1, 'failed'))
        self.assertEqual(records, [])
        self.assertEqual(summary['stages']['extract_primary']['status'], 'failed')
        self.assertEqual(summary['stages']['jev']['status'], 'skipped')

    def test_partial_jev_batch_failure_is_partial(self):
        code, summary, records, _, _ = self.run_discover('partial_jev')
        self.assertEqual((code, summary['run_status']), (1, 'partial'))
        self.assertEqual({record['qualification_status'] for record in records}, {'HIGH', 'UNCLASSIFIED'})
        self.assertEqual(summary['stages']['jev']['output_count'], 1)

    def test_search_failure_severity_depends_on_usable_results(self):
        code, summary, _, _, _ = self.run_discover(queries=('fail', 'offline'))
        self.assertEqual((code, summary['run_status']), (1, 'partial'))
        self.assertEqual(summary['stages']['search']['requests_failed'], 1)
        code, summary, records, _, _ = self.run_discover('all_search_failed')
        self.assertEqual((code, summary['run_status']), (1, 'failed'))
        self.assertEqual(records, [])
        self.assertEqual(summary['stages']['search']['status'], 'failed')

    def test_invalid_thresholds_keep_parameter_error_exit_code(self):
        with patch('sys.argv', ['leadflow.py', 'discover', '--review-threshold', '0.9', '--high-threshold', '0.1']), \
             patch('leadflow.load_env_keys'), patch('leadflow.API') as api, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(leadflow.main(), 2)
        api.assert_not_called()

    def test_missing_credentials_keep_parameter_error_exit_code(self):
        with patch('sys.argv', ['leadflow.py', 'discover']), patch('leadflow.load_env_keys'), \
             patch.dict('os.environ', {}, clear=True), patch('leadflow.API') as api, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(leadflow.main(), 2)
        api.assert_not_called()

    def test_replay_completes_local_work_without_claiming_api_coverage(self):
        _, _, records, _, _ = self.run_discover('all_jev_failed')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.json'
            source.write_text(json.dumps(records), encoding='utf-8')
            args = ['leadflow.py', 'discover', '--replay-from', str(source),
                    '--out', str(root / 'out'), '--db', str(root / 'test.sqlite')]
            with patch('sys.argv', args), patch('leadflow.API') as api, \
                 patch('leadflow.load_env_keys') as load_env, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(leadflow.main(), 0)
            api.assert_not_called()
            load_env.assert_not_called()
            summary = json.loads((root / 'out' / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['run_status'], 'completed')
            self.assertFalse(summary['pipeline_coverage']['complete'])
            self.assertEqual(summary['stages']['jev']['status'], 'skipped')
            self.assertEqual(summary['stages']['jev']['reason'], 'replay_saved_records')
            self.assertEqual(summary['jev_unclassified'], 1)

    def test_real_api_budget_during_mocked_retry_never_adds_an_extra_call(self):
        with tempfile.TemporaryDirectory() as directory:
            api = leadflow.API(Path(directory), max_calls=1)
            with patch('leadflow.urlopen', side_effect=leadflow.URLError('Offline timeout')) as urlopen, \
                 patch('leadflow.time.sleep'), self.assertRaises(leadflow.APIBudgetExhausted):
                api.post(leadflow.TAVILY_SEARCH_ENDPOINT, 'offline', {'query': 'offline'})
            self.assertEqual(api.calls, 1)
            urlopen.assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
