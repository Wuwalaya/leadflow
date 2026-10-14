"""Offline regression tests; all company evidence below is synthetic test data."""
import contextlib
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import leadflow
from leadflow import rule_classify
from target_config import resolve_target


class OfflineAPI:
    """Canned typed decisions test wiring, not real company truth or model accuracy."""
    requests = []
    material = 'stainless steel'
    location = 'United States'
    malformed_second_answer = False
    two_candidates = False

    def __init__(self, out, max_calls=90):
        self.calls, self.max_calls = 0, max_calls

    def post(self, endpoint, key, payload):
        if self.calls >= self.max_calls:
            raise RuntimeError('API call budget exhausted')
        self.calls += 1
        self.requests.append((endpoint, payload))
        if endpoint == leadflow.TAVILY_SEARCH_ENDPOINT:
            supplemental = payload['query'].startswith('site:')
            results = [{
                'url': 'https://example.com/products' if supplemental else 'https://example.com/',
                'title': 'Example | Metal distributor',
                'content': f'Example distributor of {self.material}', 'score': .9,
            }]
            if self.two_candidates:
                results.append(dict(results[0], url='https://second.example/products'))
            return {'results': results}
        if endpoint == leadflow.TAVILY_EXTRACT_ENDPOINT:
            return {'results': [{
                'url': url,
                'raw_content': ('# Example\n'
                                f'We are a distributor stocking {self.material} for industrial customers.\n'
                                + f'Headquarters: {self.location}\n'
                                + (f'Our {self.material} sheet and plate are available for distribution.'
                                   if url.endswith('/products') else 'Contact our team for product information.')),
            } for url in payload['urls']]}
        if endpoint == leadflow.JEV_ENDPOINT:
            answers = {key: ({'type': 'choice', 'choice': 'distributor',
                             'probabilities': {role: (.9 if role == 'distributor' else .1 if role == 'unknown' else 0)
                                               for role in question['criteria']}, 'confidence': .9}
                            if question['type'] == 'choice' else {'type': 'noul', 'noul': .9})
                       for key, question in payload['questions'].items()}
            if self.malformed_second_answer:
                answers['c2_product'] = {'type': 'noul', 'noul': 'invalid'}
            return {'answers': answers}
        raise AssertionError('Unexpected API endpoint')


class TargetRegressionTests(unittest.TestCase):
    def setUp(self):
        OfflineAPI.requests = []
        OfflineAPI.material = 'stainless steel'
        OfflineAPI.location = 'United States'
        OfflineAPI.malformed_second_answer = False
        OfflineAPI.two_candidates = False

    def test_country_aliases_and_unsupported_configurations(self):
        for alias in ('USA', 'US', 'United States', 'u.s.', 'U.S.A.', 'united states of america'):
            self.assertEqual(resolve_target(alias).country.name, 'United States')
        for alias in ('br', 'Brasil', 'Brazil'):
            self.assertEqual(resolve_target(alias).country.name, 'Brazil')
        for args in [('France', 'Stainless Steel'), ('USA', 'Aluminum Sheet'),
                     ('Brazil', 'Carbon Steel Sheet/Plate'), ('USA', 'Stainless Steel Pipe'),
                     ('USA', 'Carbon Steel Sheet/Plate', 'profiles')]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                resolve_target(*args)

    def test_usa_country_evidence(self):
        facts = rule_classify([{
            'url': 'https://example.com/contact',
            'text': 'Example Metals distributor of stainless steel sheet.\nHeadquarters: United States',
        }], 'Stainless Steel Flat/Profiles', 'USA', 'both')
        self.assertTrue(facts['country_match'])

    def test_carbon_target_rejects_stainless_material(self):
        facts = rule_classify([{
            'url': 'https://example.com/products',
            'text': 'Stainless steel sheet distributor.\nHeadquarters: United States',
        }], 'Carbon Steel Sheet/Plate', 'United States', 'both')
        self.assertIsNone(facts['material_evidence'])
        self.assertEqual(facts['product_matches'], [])

    def test_material_and_form_must_not_come_from_separate_listings(self):
        for text in ('Stainless steel tubing. Carbon steel sheet.',
                     'Stainless steel tubing\nCarbon steel sheet'):
            facts = rule_classify([{'url': 'https://example.com/', 'text': text}],
                                  'Stainless Steel', 'USA', 'both')
            self.assertEqual(facts['product_matches'], [])

    def test_carbon_rules_require_material_and_only_sheet_plate(self):
        facts = rule_classify([{'url': 'https://example.com/',
                               'text': 'Carbon steel sheet and plate distributor, United States.\nCarbon steel coils and flat bars.'}],
                              'Carbon Steel Sheet/Plate', 'US', 'both')
        self.assertTrue(facts['country_match'])
        self.assertEqual([item['form'] for item in facts['product_matches']], ['sheet_plate'])
        self.assertIn('Carbon steel', facts['material_evidence']['quote'])

    def test_us_rules_do_not_accept_brazil_or_contact_us_as_location(self):
        for text in ('Contact us for stainless steel sheets', 'Stainless steel sheet, São Paulo, Brasil'):
            facts = rule_classify([{'url': 'https://example.com.br/', 'text': text}],
                                  'Stainless Steel', 'US', 'both')
            self.assertFalse(facts['country_match'])

    def test_queries_evidence_and_jev_use_selected_target_and_scope(self):
        record = {'company_name': 'Example', 'domain': 'example.com',
                  'evidence_urls': ['https://example.com/'], 'evidence': 'Test evidence'}
        for product in ('Stainless Steel Flat/Profiles', 'Carbon Steel Sheet/Plate'):
            config = resolve_target('US', product, 'flat')
            queries = leadflow.market_discovery_queries(config)
            payload = leadflow.build_qualification_payload([record], config)
            instructions = json.dumps(payload).lower()
            combined = ' '.join(queries + [config.extraction_query(), config.supplemental_query('example.com'), instructions]).lower()
            for wrong in ('brazil', 'brasil', 'inox', 'chapa', 'bobina', 'cantoneira', 'sao paulo'):
                self.assertNotIn(wrong, combined)
            self.assertEqual(payload['state']['target']['country'], 'United States')
            self.assertNotIn('flat_bar', payload['state']['target']['allowed_forms'])
            self.assertIn('same offering', instructions)
            if product.startswith('Carbon'):
                self.assertNotIn('stainless', combined)
                self.assertNotIn('coil', combined)
                self.assertIn('c1_material', payload['questions'])
            else:
                self.assertIn('c1_stainless', payload['questions'])
            evidence = leadflow.relevant_evidence(
                f'Cookie preferences\nHeadquarters: United States\nOur {config.product.material} sheet products', target=config)
            self.assertIn('Headquarters: United States', evidence)
            self.assertNotIn('Cookie', evidence)

    def run_cli(self, args):
        with patch('sys.argv', ['leadflow.py'] + args), patch('leadflow.load_env_keys') as load_env, \
             patch.dict('os.environ', {'TAVILY_API_KEY': 'offline', 'TYPESAFE_API_KEY': 'offline'}), \
             patch('leadflow.API', OfflineAPI), contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            code = leadflow.main()
        return code, load_env

    def test_cli_discover_to_sqlite_csv_and_replay_for_three_configurations(self):
        scenarios = [('Brasil', 'Stainless Steel Flat/Profiles', 'Brazil', 'stainless steel'),
                     ('USA', 'Stainless Steel Flat/Profiles', 'United States', 'stainless steel'),
                     ('US', 'Carbon Steel Sheet/Plate', 'United States', 'carbon steel')]
        for country, product, canonical, material in scenarios:
            with self.subTest(country=country, product=product), tempfile.TemporaryDirectory() as directory:
                OfflineAPI.requests = []
                OfflineAPI.material, OfflineAPI.location = material, canonical
                out, db = Path(directory) / 'run', Path(directory) / 'test.sqlite'
                args = ['discover', '--country', country, '--product', product,
                        '--out', str(out), '--db', str(db), '--candidate-limit', '2',
                        '--supplemental-limit', '1', '--target', '1', '--max-api-calls', '30']
                code, _ = self.run_cli(args)
                self.assertEqual(code, 0)
                plan = json.loads((out / 'query_plan.json').read_text())
                self.assertEqual((plan['country'], plan['product']), (canonical, product))
                records = json.loads((out / 'leads.json').read_text())
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['country'], canonical)
                self.assertEqual(records[0]['target']['product'], product)
                self.assertEqual(records[0]['qualification_status'], 'HIGH')
                with (out / 'leads.csv').open(encoding='utf-8-sig') as handle:
                    exported = list(csv.DictReader(handle))
                self.assertEqual(exported[0]['Country'], canonical)
                self.assertEqual(exported[0]['Product Match'], 'sheet_plate')
                with sqlite3.connect(db) as connection:
                    self.assertEqual(connection.execute('SELECT country FROM accounts').fetchone()[0], canonical)
                    connection.execute("UPDATE accounts SET owner='sales-1', status='contacted'")
                for endpoint, payload in OfflineAPI.requests:
                    if endpoint == leadflow.TAVILY_SEARCH_ENDPOINT:
                        self.assertEqual(payload['country'], canonical.lower())
                    if canonical == 'United States':
                        text = json.dumps(payload).lower()
                        for wrong in ('brazil', 'brasil', 'inox', 'chapa', 'bobina'):
                            self.assertNotIn(wrong, text)
                        if material == 'carbon steel':
                            self.assertNotIn('stainless', text)
                self.assertTrue(any(p.get('query', '').startswith('site:') for _, p in OfflineAPI.requests))
                self.assertEqual(sum(endpoint == leadflow.TAVILY_EXTRACT_ENDPOINT for endpoint, _ in OfflineAPI.requests), 2)
                count = len(OfflineAPI.requests)
                code, load_env = self.run_cli(args + ['--replay-from', str(out)])
                self.assertEqual(code, 0)
                load_env.assert_not_called()
                self.assertEqual(len(OfflineAPI.requests), count)
                with sqlite3.connect(db) as connection:
                    self.assertEqual(connection.execute('SELECT count(*) FROM accounts').fetchone()[0], 1)
                    self.assertEqual(connection.execute('SELECT owner,status,country FROM accounts').fetchone(), ('sales-1', 'contacted', canonical))
                    self.assertEqual(connection.execute('SELECT count(*) FROM observations').fetchone()[0], 2)

    def test_replay_cannot_silently_relabel_country(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source = path / 'saved.json'
            source.write_text(json.dumps([{'country': 'United States', 'target': resolve_target('USA').as_dict()}]))
            with self.assertRaisesRegex(ValueError, 'Replay target differs'):
                leadflow.replay_discovery(source, path / 'out', str(path / 'test.sqlite'), 1,
                                                expected_config=resolve_target('Brazil'))
            self.assertFalse((path / 'test.sqlite').exists())

    def test_malformed_later_jev_answer_does_not_duplicate_batch_records(self):
        OfflineAPI.two_candidates = True
        OfflineAPI.malformed_second_answer = True
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            code, _ = self.run_cli(['discover', '--country', 'USA', '--out', str(out),
                                    '--db', str(out / 'test.sqlite'), '--supplemental-limit', '0'])
            self.assertEqual(code, 1)
            records = json.loads((out / 'leads.json').read_text())
            self.assertEqual(len(records), 2)
            self.assertEqual({record['qualification_status'] for record in records}, {'UNCLASSIFIED'})
            self.assertEqual(len(json.loads((out / 'errors.json').read_text())), 1)

    def test_invalid_cli_target_fails_before_credentials_or_network(self):
        for args in (['discover', '--country', 'France'],
                     ['plan', '--product', 'Steel Pipe'],
                     ['discovery-smoke', '--country', 'USA'],
                     ['jev-smoke', '--product', 'Carbon Steel Sheet/Plate']):
            code, load_env = self.run_cli(args)
            self.assertEqual(code, 2)
            load_env.assert_not_called()
        self.assertEqual(OfflineAPI.requests, [])

    def test_combined_api_budget_and_jev_failure_are_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            # Six searches + primary extraction + supplemental search/extraction exhaust nine calls.
            code, _ = self.run_cli(['discover', '--country', 'USA', '--out', str(out),
                                    '--db', str(out / 'test.sqlite'), '--max-api-calls', '9'])
            self.assertEqual(code, 1)  # Audit records do not make a failed pipeline successful.
            summary = json.loads((out / 'summary.json').read_text())
            self.assertEqual(summary['run_status'], 'failed')
            self.assertEqual(len(OfflineAPI.requests), 9)
            self.assertEqual(summary['jev_unclassified'], 1)
            self.assertFalse(summary['target_met'])
            self.assertEqual(summary['errors'][0]['stage'], 'jev_batch')

    def test_live_adapters_and_persistence_receive_target(self):
        config = resolve_target('USA', 'Carbon Steel Sheet/Plate')
        with tempfile.TemporaryDirectory() as directory, patch.object(leadflow.API, 'post', return_value={'results': []}) as post:
            api = leadflow.API(Path(directory))
            api.search('test', target=config)
            self.assertEqual(post.call_args.args[2]['country'], 'united states')
            api.extract(['https://example.com/'], config)
            self.assertIn('Carbon Steel', post.call_args.args[2]['query'])
            self.assertNotIn('stainless', post.call_args.args[2]['query'].lower())
            pages = [{'url': 'https://example.com/', 'text': 'Carbon steel sheet distributor, United States'}]
            facts = rule_classify(pages, config.product.name, 'USA', 'both')
            record = leadflow.finish_record(facts, pages, 'https://example.com/', 'test-run', config.product.name, 'USA', 'both')
            store = leadflow.Store(str(Path(directory) / 'test.sqlite'))
            try:
                store.upsert(record)
                self.assertEqual(store.db.execute('SELECT country FROM accounts').fetchone()[0], 'United States')
            finally:
                store.db.close()

    def test_llm_prompt_and_validation_use_carbon_config(self):
        pages = [{'url': 'https://example.com/', 'text': 'Stainless steel sheet. Carbon steel plate. United States'}]
        response = {'company_name': 'Example', 'material_evidence': {'url': pages[0]['url'], 'quote': 'Stainless steel sheet.'},
                    'product_matches': [{'form': 'coil_strip', 'evidence': {'url': pages[0]['url'], 'quote': 'Carbon steel plate.'}}]}
        with patch.dict('os.environ', {'LLM_MODEL': 'offline', 'LLM_API_KEY': 'offline'}), \
             patch.object(leadflow.API, 'post', return_value={'choices': [{'message': {'content': json.dumps(response)}}]}) as post:
            facts = leadflow.llm_classify(leadflow.API(Path('/tmp')), pages, 'Carbon Steel Sheet/Plate', 'USA', 'both')
            payload = post.call_args.args[2]
        self.assertNotIn('stainless', payload['messages'][0]['content'].lower())
        self.assertIn('Carbon Steel', payload['messages'][0]['content'])
        self.assertEqual(json.loads(payload['messages'][1]['content'])['target']['country'], 'United States')
        self.assertIsNone(facts['material_evidence'])
        self.assertEqual(facts['product_matches'], [])
