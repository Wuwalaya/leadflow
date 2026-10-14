"""Synthetic contact fixtures are offline regression data, never discovered leads."""
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import leadflow
from contact_enrichment import (CachedContactSource, LiveContactSource, discover_contact_urls,
                                enrich_contacts, observe_contacts)
from evaluate_contacts import baseline_pages, evaluate, review_template


def fixture(domain='acme.example.org', name='Acme Metals', status='HIGH'):
    page = {'url': 'https://' + domain + '/',
            'text': name + ' supplies stainless steel sheet in United States.\n[Contact](/contact)'}
    record = dict(leadflow.name_details([page], domain), domain=domain, website=page['url'],
                  qualification_status=status, emails=[], evidence_urls=[page['url']])
    return record, page


class ContactEvidenceTests(unittest.TestCase):
    def test_email_and_form_keep_exact_evidence_and_type(self):
        record, primary = fixture()
        page = {'url': record['website'] + 'contact',
                'text': 'Sales: Sales@acme.example.org\nContact Form\nName Email Message Submit'}
        rows, stages = observe_contacts(record, [page], [primary])
        self.assertEqual([row['type'] for row in rows], ['email', 'contact_form'])
        self.assertTrue(all(row['validation_status'] == 'accepted' for row in rows))
        self.assertEqual(rows[0]['role'], 'sales')
        self.assertEqual(rows[0]['observed_text'], 'Sales@acme.example.org')
        self.assertIn(rows[0]['quote'], page['text'])
        self.assertEqual(rows[0]['source_url'], page['url'])
        self.assertEqual(rows[0]['identity_evidence'], record['name_evidence'])
        self.assertEqual(stages[0]['accepted_emails'], 1)

    def test_rejected_email_retains_source_and_reasons(self):
        record, primary = fixture()
        page = {'url': record['website'] + 'contact', 'text': 'Website by agency@builder.example.org\nprivacy@acme.example.org'}
        rows, stage = observe_contacts(record, [page], [primary])
        self.assertEqual(len(rows), 2)
        self.assertIn('email_domain_mismatch_requires_review', rows[0]['reasons'])
        self.assertIn('agency_or_non_sales_context', rows[0]['reasons'])
        self.assertIn('non_sales_role', rows[1]['reasons'])
        self.assertTrue(all(row['quote'] in page['text'] for row in rows))
        self.assertEqual(stage[0]['rejected_emails'], 2)

    def test_same_domain_other_company_declaration_is_rejected(self):
        record, primary = fixture()
        page = {'url': record['website'] + 'contact',
                'text': 'Other Metals supplies sheet.\nSales: sales@acme.example.org'}
        rows, _ = observe_contacts(record, [page], [primary])
        self.assertIn('page_subject_conflict', rows[0]['reasons'])

    def test_partner_context_and_same_domain_agency_are_rejected(self):
        record, primary = fixture()
        page = {'url': record['website'] + 'contact',
                'text': 'Partner contact: partner@acme.example.org\nWebsite by design@acme.example.org'}
        rows, _ = observe_contacts(record, [page], [primary])
        self.assertIn('other_entity_context_requires_review', rows[0]['reasons'])
        self.assertIn('agency_or_non_sales_context', rows[1]['reasons'])

    def test_identity_label_without_saved_quote_cannot_accept_email(self):
        record, primary = fixture()
        record['name_evidence']['quote'] = 'Acme Metals is invented evidence'
        page = {'url': record['website'] + 'contact', 'text': 'sales@acme.example.org'}
        rows, _ = observe_contacts(record, [page], [primary])
        self.assertIn('company_identity_unresolved', rows[0]['reasons'])

    def test_unknown_name_and_group_ambiguity_remain_unverified(self):
        record, primary = fixture()
        for status in ('unknown', 'needs_review'):
            record['name_status'] = status
            rows, _ = observe_contacts(record, [{'url': primary['url'], 'text': 'sales@acme.example.org'}], [primary])
            self.assertIn('company_identity_unresolved', rows[0]['reasons'])

    def test_third_party_page_never_attributes_same_domain_email(self):
        record, primary = fixture()
        rows, _ = observe_contacts(record, [{'url': 'https://other.example.org/contact',
                                            'text': 'sales@acme.example.org'}], [primary])
        self.assertIn('source_not_target_official_site', rows[0]['reasons'])

    def test_protected_email_is_diagnosed_without_guessing(self):
        record, primary = fixture()
        page = {'url': record['website'] + 'contact',
                'text': 'Contact (/cdn-cgi/l/email-protection) [email protected] sales [at] acme.example.org'}
        rows, stages = observe_contacts(record, [page], [primary])
        self.assertEqual(rows, [])
        self.assertEqual(len(stages[0]['visibility_limits']), 3)

    def test_only_observed_official_contact_links_are_selected(self):
        record, primary = fixture()
        primary['text'] += '\n<a href="/sales">Sales</a>\n[Contact](https://evil.example.org/contact)\n[Contact](mailto:info@acme.example.org)'
        urls = discover_contact_urls(record, [primary])
        self.assertEqual({row['url'] for row in urls}, {record['website'] + 'contact', record['website'] + 'sales'})
        primary['text'] = 'Contact us'
        self.assertEqual(discover_contact_urls(record, [primary]), [])

    def test_free_mail_and_group_domain_are_not_automatically_whitelisted(self):
        record, primary = fixture()
        rows, _ = observe_contacts(record, [{'url': record['website'] + 'contact',
                                            'text': 'sales@group.example.org acme@freemail.example.org'}], [primary])
        self.assertTrue(all('email_domain_mismatch_requires_review' in row['reasons'] for row in rows))

    def test_newsletter_and_login_forms_do_not_become_contact_forms(self):
        record, primary = fixture()
        page = {'url': record['website'] + 'contact', 'text':
                '<form>Newsletter <input name="email"><input type="submit"></form>\n'
                '<form>Login <input name="message"><input type="password"></form>\n'
                '<form>Contact sales <textarea name="message"></textarea><input type="submit"></form>'}
        rows, _ = observe_contacts(record, [page], [primary])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['type'], 'contact_form')
        self.assertIn('Contact sales', rows[0]['quote'])

    def test_invalid_observed_email_is_rejected_without_repair(self):
        record, primary = fixture()
        rows, _ = observe_contacts(record, [{'url': record['website'] + 'contact',
                                            'text': 'sales..team@acme.example.org'}], [primary])
        self.assertEqual(rows[0]['value'], 'sales..team@acme.example.org')
        self.assertIn('invalid_email_syntax', rows[0]['reasons'])

    def test_contact_query_and_homepage_anchor_are_observed_urls(self):
        record, primary = fixture()
        primary['text'] = '[Contact](/index.php?page=contact)\n[Sales](#sales)'
        urls = discover_contact_urls(record, [primary])
        self.assertEqual({row['url'] for row in urls}, {record['website'] + 'index.php?page=contact', record['website']})


class ContactEnrichmentTests(unittest.TestCase):
    def enrich(self, text='', source=None, **kwargs):
        record, primary = fixture()
        page = {'url': record['website'] + 'contact', 'text': text}
        source = source or CachedContactSource([page])
        return enrich_contacts([record], {record['domain']: [primary]}, source, **kwargs)

    def test_fixed_cohort_email_coverage_and_inputs_unchanged(self):
        record, primary = fixture()
        original = copy.deepcopy(record)
        page = {'url': record['website'] + 'contact', 'text': 'Sales sales@acme.example.org'}
        source = CachedContactSource([page])
        records, metrics = enrich_contacts([record], {record['domain']: [primary]}, source)
        self.assertEqual(record, original)
        self.assertEqual(records[0]['emails'], ['sales@acme.example.org'])
        self.assertEqual((metrics['before_with_email'], metrics['after_with_email']), (0, 1))
        self.assertEqual(metrics['high_review_denominator'], 1)
        self.assertEqual(metrics['additional_cost']['api_calls_including_retries'], 0)
        self.assertEqual(records[0]['qualification_status'], 'HIGH')

    def test_form_only_is_available_without_email_or_submission_claim(self):
        records, metrics = self.enrich('Contact Form')
        self.assertEqual(records[0]['emails'], [])
        self.assertEqual(records[0]['contact_diagnostics']['outcome'], 'form_observed')
        self.assertEqual(metrics['after_with_email'], 0)
        self.assertIn('not_submission_verified', records[0]['contact_observations'][0]['verification'])

    def test_fetch_failure_cache_miss_and_empty_content_are_distinct(self):
        for source, expected in (
                (CachedContactSource(failures=[{'url': 'https://acme.example.org/contact', 'error': 'timeout'}]), 'fetch_failed'),
                (CachedContactSource(), 'cache_miss'),
                (CachedContactSource([{'url': 'https://acme.example.org/contact', 'text': ''}]), 'empty_content')):
            records, _ = self.enrich(source=source)
            diagnostic = records[0]['contact_diagnostics']
            self.assertEqual(diagnostic['page_discovery']['status'], 'found')
            self.assertEqual(diagnostic['fetches'][0]['status'], expected)
            self.assertIn(expected, diagnostic['outcomes'])

    def test_no_email_in_fetched_text_is_not_claimed_absent_from_website(self):
        records, _ = self.enrich('Call our sales office.')
        diagnostic = records[0]['contact_diagnostics']
        self.assertEqual(diagnostic['outcome'], 'no_email_observed_in_fetched_content')
        self.assertEqual(diagnostic['extraction_omission'], 'unknown_requires_raw_page_or_human_evidence')

    def test_email_extracted_but_rejected_has_separate_validation_diagnosis(self):
        records, _ = self.enrich('sales@other.example.org')
        diagnostic = records[0]['contact_diagnostics']
        self.assertEqual(diagnostic['supplemental_pages'][0]['email_matches'], 1)
        self.assertEqual(diagnostic['outcome'], 'emails_rejected')
        self.assertEqual(diagnostic['validation_false_rejection'], 'unknown_requires_human_review')

    def test_candidate_and_page_budgets_are_distinct(self):
        records, _ = self.enrich(candidate_limit=0)
        self.assertEqual(records[0]['contact_diagnostics']['outcome'], 'candidate_budget_exhausted')
        records, _ = self.enrich(pages_per_candidate=0)
        self.assertIn('page_budget_exhausted', records[0]['contact_diagnostics']['outcomes'])

    def test_low_unclassified_and_existing_email_are_skipped(self):
        record, primary = fixture()
        records = [dict(record, qualification_status='LOW'), dict(record, qualification_status='UNCLASSIFIED'),
                   dict(record, emails=['legacy@acme.example.org'])]
        source = CachedContactSource()
        after, metrics = enrich_contacts(records, {record['domain']: [primary]}, source)
        self.assertEqual(metrics['selected_for_enrichment'], 0)
        self.assertEqual(source.fetch_lookups + source.search_lookups, 0)
        self.assertTrue(all(item['contact_diagnostics']['outcome'] == 'not_eligible' for item in after))

    def test_missing_contact_search_cache_differs_from_empty_successful_search(self):
        record, primary = fixture()
        primary['text'] = primary['text'].split('\n')[0]
        for source, expected in (
                (CachedContactSource(), 'cache_miss'),
                (CachedContactSource(searches=[{'query': 'site:acme.example.org contact',
                                                'response': {'results': []}}]), 'not_found_in_search')):
            after, _ = enrich_contacts([record], {record['domain']: [primary]}, source)
            self.assertEqual(after[0]['contact_diagnostics']['page_discovery']['status'], expected)

    def test_contact_page_cannot_resolve_unknown_primary_identity(self):
        record, primary = fixture()
        primary['text'] = 'Product sheet stainless steel\n[Contact](/contact)'
        record.update(name_status='unknown', name_evidence=None)
        page = {'url': record['website'] + 'contact', 'text': 'Acme Metals supplies sheet.\nsales@acme.example.org'}
        after, _ = enrich_contacts([record], {record['domain']: [primary]}, CachedContactSource([page]))
        self.assertEqual(after[0]['emails'], [])
        self.assertIn('company_identity_unresolved', after[0]['contact_observations'][0]['reasons'])

    def test_repeat_replay_is_deterministic(self):
        first = self.enrich('sales@acme.example.org')
        second = self.enrich('sales@acme.example.org')
        self.assertEqual(first, second)


class ContactBudgetAndPersistenceTests(unittest.TestCase):
    def test_real_api_retry_never_exceeds_contact_or_shared_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            api = leadflow.API(Path(directory), max_calls=7)
            api.calls = 5
            source = LiveContactSource(api, max_calls=1)
            with patch.dict('os.environ', {'TAVILY_API_KEY': 'offline'}), \
                 patch('leadflow.urlopen', side_effect=HTTPError('https://offline', 429, 'offline', {}, None)) as network, \
                 patch('leadflow.time.sleep'):
                with self.assertRaises(leadflow.APIBudgetExhausted):
                    source.fetch('https://acme.example.org/contact')
            self.assertEqual((api.calls, network.call_count, api.max_calls), (6, 1, 7))
            self.assertEqual(source.usage()['api_calls_including_retries'], 1)
            self.assertFalse(source.usage()['usage_complete'])

    def test_exhausted_budget_diagnosed_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            api = leadflow.API(Path(directory), max_calls=0)
            source = LiveContactSource(api, max_calls=2)
            record, primary = fixture()
            with patch('leadflow.urlopen', side_effect=AssertionError('Network forbidden')):
                after, _ = enrich_contacts([record], {record['domain']: [primary]}, source)
            self.assertIn('budget_exhausted', after[0]['contact_diagnostics']['outcomes'])
            self.assertEqual(api.calls, 0)

    def test_live_contact_extract_has_no_product_chunk_filter(self):
        class MockAPI:
            calls, max_calls, usage = 0, 1, []

            def post(self, endpoint, key, payload):
                self.calls += 1
                self.payload = payload
                self.usage.append({'usage': {'credits': 1}})
                return {'results': [{'url': payload['urls'][0], 'raw_content': 'sales@acme.example.org'}]}
        api = MockAPI()
        source = LiveContactSource(api, 1)
        self.assertEqual(source.fetch('https://acme.example.org/contact')['status'], 'fetched')
        self.assertNotIn('query', api.payload)
        self.assertNotIn('chunks_per_source', api.payload)

    def test_contacts_survive_sqlite_and_sales_name_protections(self):
        record, primary = fixture()
        page = {'url': record['website'] + 'contact', 'text': 'sales@acme.example.org'}
        after, _ = enrich_contacts([record], {record['domain']: [primary]}, CachedContactSource([page]))
        with tempfile.TemporaryDirectory() as directory:
            store = leadflow.Store(str(Path(directory) / 'test.sqlite'))
            protected = dict(record, run_id='first', name_status='human_confirmed')
            aid, _ = store.upsert(protected)
            store.db.execute("UPDATE accounts SET owner='sales-1',status='contacted' WHERE id=?", (aid,))
            store.db.commit()
            incoming = dict(after[0], run_id='second')
            second, _ = store.upsert(incoming)
            self.assertEqual(aid, second)
            stored = json.loads(store.db.execute('SELECT latest_json FROM accounts WHERE id=?', (aid,)).fetchone()[0])
            self.assertEqual(stored['contact_observations'][0]['value'], 'sales@acme.example.org')
            self.assertEqual(stored['name_status'], 'human_confirmed')
            self.assertEqual(store.db.execute('SELECT owner,status FROM accounts').fetchone(), ('sales-1', 'contacted'))
            self.assertEqual(store.db.execute('SELECT count(*) FROM observations').fetchone()[0], 2)
            store.db.close()

    def test_protected_different_entity_does_not_inherit_new_contacts(self):
        record, primary = fixture()
        page = {'url': record['website'] + 'contact', 'text': 'sales@acme.example.org'}
        after, _ = enrich_contacts([record], {record['domain']: [primary]}, CachedContactSource([page]))
        with tempfile.TemporaryDirectory() as directory:
            store = leadflow.Store(str(Path(directory) / 'test.sqlite'))
            old = dict(record, company_name='Previous Metals', name_status='human_confirmed', run_id='first')
            store.upsert(old)
            incoming = dict(after[0], run_id='second')
            store.upsert(incoming)
            self.assertEqual(incoming['company_name'], 'Previous Metals')
            self.assertEqual(incoming['emails'], [])
            self.assertIn('saved_account_identity_conflict', incoming['contact_observations'][0]['reasons'])
            self.assertEqual(incoming['contact_diagnostics']['outcome'], 'saved_account_identity_conflict')
            self.assertGreater(store.db.execute('SELECT count(*) FROM reviews').fetchone()[0], 0)
            store.db.close()


class ContactEvaluationTests(unittest.TestCase):
    def test_offline_evaluation_does_not_load_env_network_or_modify_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, out = root / 'source', root / 'out'
            source.mkdir()
            record, primary = fixture()
            (source / 'leads.json').write_text(json.dumps([record]))
            (source / 'tavily_extract_responses.json').write_text(json.dumps([{'response': {'results': [
                {'url': primary['url'], 'raw_content': primary['text']}]}}]))
            extra = root / 'contacts.json'
            extra.write_text(json.dumps({'pages': [{'url': record['website'] + 'contact',
                                                    'text': 'sales@acme.example.org'}]}))
            originals = {path: path.read_bytes() for path in source.iterdir()}
            with patch('leadflow.load_env_keys', side_effect=AssertionError('No .env')), \
                 patch('leadflow.urlopen', side_effect=AssertionError('No network')):
                report = evaluate(source, out, extra)
            self.assertEqual(report['metrics']['after_with_email'], 1)
            self.assertEqual(report['metrics']['additional_cost']['monetary_cost'], 0)
            self.assertEqual(report['human_review']['labeled'], 0)
            self.assertIsNone(report['human_review']['high_accuracy'])
            for path, original in originals.items():
                self.assertEqual(path.read_bytes(), original)
            with self.assertRaisesRegex(ValueError, 'must not exist'):
                evaluate(source, out)
            template = json.loads((out / 'lead_review_template.json').read_text())
            self.assertIsNone(template['rows'][0]['sales_decision'])
            self.assertEqual(len(report['source_sha256']), 3)

    def test_baseline_excludes_supplemental_contact_pages(self):
        record, primary = fixture()
        contact = {'url': record['website'] + 'contact', 'text': 'sales@acme.example.org'}
        pages = baseline_pages([record], [primary, contact])
        self.assertEqual(pages[record['domain']], [primary])

    def test_template_has_no_synthetic_labels_and_defines_denominators(self):
        record, primary = fixture()
        after, _ = enrich_contacts([record], {record['domain']: [primary]}, CachedContactSource())
        template = review_template(after, 'frozen')
        self.assertIsNone(template['rows'][0]['company_identity_correct'])
        self.assertIsNone(template['rows'][0]['reviewer'])
        self.assertIn('validation_false_rejection', template['allowed_failure_types'])
        self.assertIn('hold and unreviewed excluded', template['sales_acceptance_definition'])


class ContactCredentialTests(unittest.TestCase):
    def saved_source(self, root):
        source = root / 'source'
        source.mkdir()
        record, primary = fixture()
        (source / 'leads.json').write_text(json.dumps([record]), encoding='utf-8')
        (source / 'tavily_extract_responses.json').write_text(json.dumps([{'response': {'results': [
            {'url': primary['url'], 'raw_content': primary['text']}]}}]), encoding='utf-8')
        return source

    def test_live_missing_environment_key_uses_existing_dotenv_loader(self):
        # Mock the loader; no real .env is read and no API operation is funded.
        def load_fixture_key(path, names):
            self.assertEqual(path, Path('.env'))
            self.assertEqual(names, {'TAVILY_API_KEY'})
            os.environ['TAVILY_API_KEY'] = 'offline-fixture'

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.saved_source(root)
            with patch.dict('os.environ', {}, clear=True), \
                 patch('leadflow.load_env_keys', side_effect=load_fixture_key) as loader, \
                 patch('leadflow.urlopen', side_effect=AssertionError('No network')):
                report = evaluate(source, root / 'out', candidate_limit=0, live=True)
            loader.assert_called_once()
            self.assertEqual(report['metrics']['additional_cost']['api_calls_including_retries'], 0)

    def test_live_exported_environment_key_does_not_read_dotenv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.saved_source(root)
            with patch.dict('os.environ', {'TAVILY_API_KEY': 'offline-fixture'}, clear=True), \
                 patch('leadflow.load_env_keys', side_effect=AssertionError('No .env read')), \
                 patch('leadflow.urlopen', side_effect=AssertionError('No network')):
                report = evaluate(source, root / 'out', candidate_limit=0, live=True)
            self.assertEqual(report['metrics']['additional_cost']['api_calls_including_retries'], 0)

    def test_live_missing_key_after_loader_fails_before_api_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.saved_source(root)
            with patch.dict('os.environ', {}, clear=True), patch('leadflow.load_env_keys') as loader, \
                 patch('evaluate_contacts.API') as api:
                with self.assertRaisesRegex(ValueError, 'TAVILY_API_KEY'):
                    evaluate(source, root / 'out', live=True)
            loader.assert_called_once_with(Path('.env'), {'TAVILY_API_KEY'})
            api.assert_not_called()
            self.assertFalse((root / 'out').exists())


class ContactDiscoverIntegrationTests(unittest.TestCase):
    def run_cli(self, contact_budget=2, total_budget=10, fail=False, no_link=False):
        requests = []
        record, primary = fixture()
        if no_link:
            primary['text'] = primary['text'].split('\n')[0]

        class OfflineAPI:
            def __init__(self, out, max_calls=90):
                self.calls, self.max_calls, self.usage = 0, max_calls, []

            def post(self, endpoint, key, payload):
                if self.calls >= self.max_calls:
                    raise leadflow.APIBudgetExhausted('Offline budget exhausted')
                self.calls += 1
                requests.append((endpoint, payload))
                if endpoint == leadflow.TAVILY_SEARCH_ENDPOINT:
                    response = {'results': [{'url': record['website'] + 'contact' if 'site:' in payload['query'] else record['website'],
                                              'title': 'Acme Metals', 'content': 'Stainless steel sheet distributor'}]}
                elif endpoint == leadflow.TAVILY_EXTRACT_ENDPOINT:
                    if 'contact' in payload['urls'][0]:
                        response = ({'results': [], 'failed_results': [{'url': payload['urls'][0], 'error': 'offline timeout'}]}
                                    if fail else {'results': [{'url': payload['urls'][0], 'raw_content': 'sales@acme.example.org'}]})
                    else:
                        response = {'results': [{'url': primary['url'], 'raw_content': primary['text']}]}
                elif endpoint == leadflow.JEV_ENDPOINT:
                    response = {'answers': {name: ({'type': 'noul', 'noul': .9} if question['type'] == 'noul'
                                else {'type': 'choice', 'choice': 'distributor', 'confidence': .9,
                                      'probabilities': {role: .9 if role == 'distributor' else .1 if role == 'unknown' else 0
                                                        for role in question['criteria']}})
                                            for name, question in payload['questions'].items()}}
                else:
                    raise AssertionError('Unexpected endpoint')
                response['usage'] = {'credits': 1} if endpoint != leadflow.JEV_ENDPOINT else {'input_tokens': 10, 'output_tokens': 5}
                self.usage.append({'endpoint': endpoint, 'usage': response['usage']})
                return response

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            query = root / 'queries.json'
            query.write_text('["offline"]')
            args = ['leadflow.py', 'discover', '--query-file', str(query), '--out', str(root / 'out'),
                    '--db', str(root / 'test.sqlite'), '--country', 'USA', '--supplemental-limit', '0',
                    '--contact-max-api-calls', str(contact_budget), '--max-api-calls', str(total_budget)]
            with patch('sys.argv', args), patch('leadflow.API', OfflineAPI), patch('leadflow.load_env_keys'), \
                 patch.dict('os.environ', {'TAVILY_API_KEY': 'offline', 'TYPESAFE_API_KEY': 'offline'}), \
                 patch('leadflow.urlopen', side_effect=AssertionError('No network')), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = leadflow.main()
            summary = json.loads((root / 'out' / 'summary.json').read_text())
            rows = json.loads((root / 'out' / 'leads.json').read_text())
            return code, summary, rows, requests

    def test_opt_in_discovers_email_after_jev_and_persists_evidence(self):
        code, summary, rows, requests = self.run_cli()
        self.assertEqual((code, summary['run_status']), (0, 'completed'))
        self.assertEqual(rows[0]['emails'], ['sales@acme.example.org'])
        self.assertEqual(requests[2][0], leadflow.JEV_ENDPOINT)
        self.assertEqual(requests[3][0], leadflow.TAVILY_EXTRACT_ENDPOINT)
        self.assertEqual(summary['tavily_calls_including_retries'], 3)
        self.assertEqual(summary['contact_enrichment']['additional_cost']['api_calls_including_retries'], 1)
        self.assertEqual(rows[0]['contact_observations'][0]['validation_status'], 'accepted')

    def test_disabled_contacts_keep_existing_call_order_and_status(self):
        code, summary, rows, requests = self.run_cli(contact_budget=0)
        self.assertEqual((code, summary['run_status']), (0, 'completed'))
        self.assertEqual(len(requests), 3)
        self.assertNotIn('contact_enrichment', summary)
        self.assertNotIn('contact_diagnostics', rows[0])

    def test_fetch_failure_retains_qualification_and_partial_exit_code(self):
        code, summary, rows, _ = self.run_cli(fail=True)
        self.assertEqual((code, summary['run_status']), (1, 'partial'))
        self.assertEqual(rows[0]['qualification_status'], 'HIGH')
        self.assertEqual(rows[0]['emails'], [])
        self.assertIn('contact_enrichment', summary['affected_stages'])
        self.assertEqual(rows[0]['contact_diagnostics']['outcome'], 'fetch_failed')

    def test_shared_budget_exhausted_after_jev_does_not_add_requests(self):
        code, summary, rows, requests = self.run_cli(total_budget=3)
        self.assertEqual((code, summary['run_status']), (1, 'partial'))
        self.assertEqual(len(requests), 3)
        self.assertEqual(rows[0]['contact_diagnostics']['outcome'], 'budget_exhausted')
        self.assertEqual(summary['contact_enrichment']['additional_cost']['api_calls_including_retries'], 0)

    def test_one_contact_attempt_can_search_but_not_fund_extract(self):
        code, summary, rows, requests = self.run_cli(contact_budget=1, no_link=True)
        self.assertEqual((code, summary['run_status']), (1, 'partial'))
        self.assertEqual(len(requests), 4)
        self.assertEqual(rows[0]['contact_diagnostics']['page_discovery']['status'], 'found')
        self.assertEqual(rows[0]['contact_diagnostics']['fetches'][0]['status'], 'budget_exhausted')


if __name__ == '__main__':
    unittest.main()
