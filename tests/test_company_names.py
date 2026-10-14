"""Synthetic name regressions; no company observations or live evidence."""
import json
import tempfile
import unittest
from pathlib import Path

from leadflow import Store, export_discovery, llm_classify, name_details, rule_classify
from unittest.mock import patch


class CompanyNameRegressionTests(unittest.TestCase):
    def identify(self, text, domain='example.com', **kwargs):
        return name_details([{'url': 'https://' + domain + '/about', 'text': text}], domain, **kwargs)

    def test_official_name_does_not_need_to_spell_domain(self):
        result = self.identify('Example Industrial Supply is a distributor.', 'eis.example')
        self.assertEqual(result['company_name'], 'Example Industrial Supply')
        self.assertEqual(result['name_status'], 'official_observed')
        self.assertEqual(result['name_evidence']['url'], 'https://eis.example/about')
        self.assertEqual(result['name_evidence']['quote'], 'Example Industrial Supply is a distributor.')

    def test_brand_is_not_replaced_by_different_legal_name(self):
        text = '<script type="application/ld+json">' + json.dumps({
            '@type': 'Organization', 'name': 'Example Brand', 'legalName': 'Different Holdings Inc.',
            'url': 'https://example.com/'}) + '</script>'
        result = self.identify(text)
        self.assertEqual(result['company_name'], 'Example Brand')
        self.assertEqual(result['name_status'], 'official_observed')
        self.assertIn('Different Holdings Inc.', result['name_evidence']['quote'])

    def test_domain_abbreviation_is_unverified(self):
        result = self.identify('# Stainless Steel Sheet\nWe offer sheet.', 'sss-steel.example')
        self.assertEqual(result['company_name'], 'sss-steel')
        self.assertEqual(result['name_status'], 'needs_review')
        self.assertEqual(result['name_reason'], 'domain_fallback')
        self.assertIsNone(result['name_evidence'])
        self.assertNotEqual(self.identify('<title>sss-steel</title>', 'sss-steel.example')['name_status'], 'official_observed')

    def test_generic_product_heading_cannot_confirm_domain(self):
        result = self.identify('# Stainless Steel\nStainless Steel is a material.', 'stainlesssteel.example')
        self.assertEqual(result['name_status'], 'needs_review')
        self.assertIsNone(result['name_evidence'])

    def test_group_and_subsidiary_ambiguity_needs_review(self):
        result = self.identify('Our Companies\nExample Brand is a distributor.\nExample Holdings Headquarters')
        self.assertEqual(result['name_status'], 'needs_review')
        self.assertEqual(result['name_reason'], 'group_entity_ambiguity')

    def test_conflicting_self_declarations_need_review(self):
        result = self.identify('Example Brand is a distributor.\nAnother Brand is a manufacturer.')
        self.assertEqual(result['name_status'], 'needs_review')
        self.assertEqual(result['name_reason'], 'conflicting_names')
        self.assertEqual(len(result['name_candidates']), 2)

    def test_legal_suffix_variation_keeps_brand(self):
        result = self.identify('Example Brand is a distributor.\nExample Brand Corporation provides metal.')
        self.assertEqual(result['company_name'], 'Example Brand')
        self.assertEqual(result['name_status'], 'official_observed')

    def test_pronouns_and_sentence_openers_are_not_companies(self):
        # Sentence openers must not be interpreted as company names.
        result = self.identify('Example Brand is a distributor.\nToday, we stand as a supplier.\nIt has been used for metal.\nAs Texans, we offer metal.')
        self.assertEqual(result['company_name'], 'Example Brand')
        self.assertEqual(result['name_status'], 'official_observed')

    def test_holdings_and_operating_company_are_not_legal_suffix_aliases(self):
        result = self.identify('Example Brand is a distributor.\nExample Brand Holdings is a group.')
        self.assertEqual(result['name_status'], 'needs_review')

    def test_parent_mention_does_not_replace_site_subject(self):
        result = self.identify('Example Brand is a distributor.\nExplore our parent company, Another Brand, to learn more.')
        self.assertEqual(result['company_name'], 'Example Brand')
        self.assertEqual(result['name_status'], 'official_observed')

    def test_missing_and_other_domain_evidence_remain_unknown(self):
        self.assertEqual(name_details([], 'example.com')['name_status'], 'unknown')
        result = name_details([{'url': 'https://foreign.example', 'text': 'Foreign Brand is a distributor.'}], 'example.com')
        self.assertEqual(result['name_status'], 'unknown')
        self.assertIsNone(result['name_evidence'])

    def test_search_title_is_only_a_review_hint(self):
        result = self.identify('We supply metal.', 'examplebrand.example', title='Example Brand | Steel Supplier')
        self.assertEqual(result['company_name'], 'Example Brand')
        self.assertEqual(result['name_status'], 'needs_review')
        self.assertIsNone(result['name_evidence'])

    def test_official_title_retains_exact_source(self):
        result = self.identify('<title>Steel Supplier | Example Brand</title>', 'examplebrand.example')
        self.assertEqual(result['name_status'], 'official_observed')
        self.assertEqual(result['name_evidence']['quote'], '<title>Steel Supplier | Example Brand</title>')

    def test_schema_for_product_and_related_company_cannot_confirm_name(self):
        for node in [
            {'@type': 'Product', 'name': 'Stainless Steel Sheet', 'url': 'https://example.com/'},
            {'@type': 'Organization', 'name': 'Foreign Brand', 'url': 'https://foreign.example/'},
            {'@type': 'Organization', 'name': 'Unbound Brand'},
        ]:
            text = '<script type="application/ld+json">' + json.dumps(node) + '</script>'
            self.assertNotEqual(self.identify(text)['name_status'], 'official_observed')

    def test_directory_subject_is_unknown(self):
        result = self.identify('United Stainless is a distributor.', 'rocketreach.co', title='United Stainless Information')
        self.assertEqual(result['company_name'], 'unknown')
        self.assertEqual(result['name_status'], 'unknown')

    def test_unrelated_product_page_declaration_requires_site_anchor(self):
        result = name_details([{'url': 'https://example.com/products/sheet',
                                'text': 'Another Brand is a distributor.'}], 'example.com')
        self.assertEqual(result['name_status'], 'needs_review')
        self.assertEqual(result['name_reason'], 'site_subject_unresolved')

    def test_malformed_schema_types_do_not_crash_extraction(self):
        text = '<script type="application/ld+json">' + json.dumps({
            '@type': [{}], 'name': 'Example Brand', 'url': 'https://example.com/'}) + '</script>'
        self.assertNotEqual(self.identify(text)['name_status'], 'official_observed')

    def test_llm_valid_quote_for_wrong_entity_is_not_name_confirmation(self):
        class FakeAPI:
            def post(self, *args):
                return {'choices': [{'message': {'content': json.dumps({
                    'company_name': 'Stainless Steel Sheet', 'name_evidence': {
                        'url': 'https://example.com/', 'quote': 'Stainless Steel Sheet'},
                    'customer_type': 'unknown', 'product_matches': []})}}]}
        with patch.dict('os.environ', {'LLM_MODEL': 'offline', 'LLM_API_KEY': 'fake'}):
            result = llm_classify(FakeAPI(), [{'url': 'https://example.com/', 'text': '# Stainless Steel Sheet'}], 'Stainless Steel', 'USA', 'both')
        self.assertNotEqual(result['company_name'], 'Stainless Steel Sheet')
        self.assertNotEqual(result['name_status'], 'official_observed')

    def test_historical_product_title_is_not_company_name(self):
        # Synthetic declaration preserves the product-title vs. entity-name regression.
        pages = [{'url': 'https://alloy.example/', 'text':
                  'Example Alloy Supply is a distributor of stainless steel sheet in United States.'}]
        result = rule_classify(pages, 'Stainless Steel', 'USA', 'both',
                               'Stainless Steel Flat Bar Supplier | Example Alloy Supply')
        self.assertEqual(result['company_name'], 'Example Alloy Supply')
        self.assertEqual(result['name_status'], 'official_observed')
        self.assertIn(result['company_name'], result['name_evidence']['quote'])

    def test_human_confirmed_name_survives_domain_fallback_and_export_record(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / 'test.sqlite'))
            original = {'domain': 'example.com', 'company_name': 'Approved Brand',
                        'name_status': 'human_confirmed', 'name_evidence':
                        {'url': 'https://example.com/about', 'quote': 'Approved Brand'},
                        'run_id': 'first'}
            aid, _ = store.upsert(original)
            incoming = dict(original, company_name='example', name_status='needs_review',
                            name_evidence=None, name_reason='domain_fallback', run_id='second')
            bid, _ = store.upsert(incoming)
            self.assertEqual(aid, bid)
            self.assertEqual(store.count(), 1)
            self.assertEqual(store.db.execute('SELECT name FROM accounts').fetchone()[0], 'Approved Brand')
            self.assertEqual(incoming['company_name'], 'Approved Brand')
            observed = json.loads(store.db.execute('SELECT payload FROM observations ORDER BY id DESC').fetchone()[0])
            self.assertEqual(observed['company_name'], 'example')
            export_discovery(Path(directory), [incoming], {'run_id': 'second'}, store)
            exported = json.loads((Path(directory) / 'leads.json').read_text())[0]
            self.assertEqual(exported['company_name'], 'Approved Brand')
            self.assertEqual(exported['name_status'], 'human_confirmed')
            self.assertEqual(exported['name_observation']['company_name'], 'example')
            self.assertIn('Approved Brand', (Path(directory) / 'leads.csv').read_text())
            self.assertEqual(json.loads((Path(directory) / 'name_review.json').read_text())[0]['name_evidence'], original['name_evidence'])
            store.db.close()

    def test_human_confirmed_name_is_protected_from_automatic_official_name(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / 'test.sqlite'))
            first = {'company_name': 'Approved Brand', 'name_status': 'human_confirmed',
                     'domain': 'example.com', 'run_id': 'first'}
            aid, _ = store.upsert(first)
            incoming = dict(first, company_name='Another Brand', name_status='official_observed', run_id='second')
            bid, _ = store.upsert(incoming)
            self.assertEqual(aid, bid)
            self.assertEqual(incoming['company_name'], 'Approved Brand')
            store.db.close()

    def test_legacy_schema_migration_preserves_confirmed_latest_json(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'old.sqlite')
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE accounts(id TEXT PRIMARY KEY,name TEXT,domain TEXT,registry_id TEXT,owner TEXT,status TEXT,first_seen TEXT,last_seen TEXT,latest_json TEXT)')
            db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?,?)',
                       ('old', 'Approved Brand', 'example.com', None, 'sales', 'contacted', 'first', 'first',
                        json.dumps({'company_name': 'Approved Brand', 'name_status': 'human_confirmed'})))
            db.commit()
            db.close()
            store = Store(path)
            incoming = {'company_name': 'example', 'domain': 'example.com', 'run_id': 'second', 'name_status': 'needs_review', 'name_reason': 'domain_fallback'}
            aid, _ = store.upsert(incoming)
            self.assertEqual(aid, 'old')
            self.assertEqual(incoming['name_status'], 'human_confirmed')
            self.assertEqual(store.db.execute('SELECT name,owner,status FROM accounts').fetchone(), ('Approved Brand', 'sales', 'contacted'))
            store.db.close()

    def test_same_domain_rerun_preserves_official_name_and_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / 'test.sqlite'))
            first = dict(self.identify('Example Brand is a distributor.'), domain='example.com', run_id='first')
            aid, _ = store.upsert(first)
            second = dict(self.identify('# Sheet'), domain='example.com', run_id='second')
            bid, _ = store.upsert(second)
            self.assertEqual(aid, bid)
            self.assertEqual(store.count(), 1)
            self.assertEqual(second['company_name'], 'Example Brand')
            self.assertEqual(store.db.execute('SELECT count(*) FROM observations').fetchone()[0], 2)
            self.assertEqual(store.db.execute('SELECT count(*) FROM reviews').fetchone()[0], 1)
            store.db.close()


class NameUpsertQualityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(str(Path(directory.name) / 'test.sqlite'))
        self.addCleanup(self.store.db.close)

    def record(self, name='Candidate Brand', **changes):
        record = {'company_name': name, 'domain': 'example.com', 'run_id': 'first',
                  'name_status': 'needs_review', 'name_reason': 'site_subject_unresolved',
                  'name_evidence': {'url': 'https://example.com/products',
                                    'quote': name + ' is a distributor.'}}
        record.update(changes)
        return record

    def latest_observation(self):
        return json.loads(self.store.db.execute(
            'SELECT payload FROM observations ORDER BY id DESC').fetchone()[0])

    def assert_name_review(self, account_id):
        self.assertEqual(self.store.db.execute(
            'SELECT account_id,related_account_id,reason,run_id FROM reviews').fetchall(),
            [(account_id, account_id, 'name_update_requires_review', 'second')])

    def test_same_level_domain_fallback_preserves_candidate_and_evidence(self):
        first = self.record()
        aid, _ = self.store.upsert(first)
        second = self.record('example', run_id='second', name_reason='domain_fallback', name_evidence=None)
        bid, _ = self.store.upsert(second)
        self.assertEqual((aid, self.store.count()), (bid, 1))
        self.assertEqual(second['company_name'], first['company_name'])
        self.assertEqual(second['name_evidence'], first['name_evidence'])
        self.assertEqual(second['name_status'], 'needs_review')
        self.assertEqual(second['name_observation']['company_name'], 'example')
        self.assertEqual(self.latest_observation()['company_name'], 'example')
        self.assertIsNone(self.latest_observation()['name_evidence'])
        self.assert_name_review(aid)

    def test_same_level_conflict_keeps_display_name_and_all_new_information(self):
        first = self.record(name_reason='group_entity_ambiguity')
        aid, _ = self.store.upsert(first)
        self.store.db.execute("UPDATE accounts SET owner='sales',status='contacted' WHERE id=?", (aid,))
        self.store.db.commit()
        second = self.record('Different Brand', run_id='second', customer_type='distributor',
                             evidence_summary='New product information')
        incoming_evidence = second['name_evidence']
        bid, _ = self.store.upsert(second)
        self.assertEqual(aid, bid)
        self.assertEqual(second['company_name'], 'Candidate Brand')
        self.assertEqual(second['name_evidence'], first['name_evidence'])
        self.assertEqual(second['name_status'], 'needs_review')
        self.assertEqual(second['name_observation']['name_evidence'], incoming_evidence)
        observed = self.latest_observation()
        self.assertEqual(observed['company_name'], 'Different Brand')
        self.assertEqual(observed['evidence_summary'], 'New product information')
        self.assertEqual(self.store.db.execute('SELECT owner,status,customer_type FROM accounts').fetchone(),
                         ('sales', 'contacted', 'distributor'))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM observations').fetchone()[0], 2)
        self.assert_name_review(aid)

    def test_same_level_matching_name_accepts_valid_additional_evidence(self):
        first = self.record(name_evidence=None, name_reason='legacy_unverified')
        aid, _ = self.store.upsert(first)
        second = self.record(run_id='second')
        evidence = second['name_evidence']
        bid, _ = self.store.upsert(second)
        self.assertEqual(aid, bid)
        self.assertEqual(second['name_evidence'], evidence)
        self.assertEqual(second['name_status'], 'needs_review')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reviews').fetchone()[0], 0)

    def test_same_level_valid_evidence_corrects_unsupported_old_name(self):
        aid, _ = self.store.upsert(self.record('example', name_evidence=None, name_reason='domain_fallback'))
        second = self.record(run_id='second')
        bid, _ = self.store.upsert(second)
        self.assertEqual(aid, bid)
        self.assertEqual(second['company_name'], 'Candidate Brand')
        self.assertEqual(second['name_status'], 'needs_review')
        self.assertEqual(self.store.db.execute('SELECT name FROM accounts').fetchone()[0], 'Candidate Brand')

    def test_same_level_untrusted_source_cannot_replace_official_candidate(self):
        first = self.record()
        aid, _ = self.store.upsert(first)
        for evidence in [
            {'url': 'https://foreign.example/', 'quote': 'Different Brand is a distributor.'},
            {'url': 'https://example.com/products', 'quote': 'No company name in this excerpt.'},
            {'url': 'file:///example.com', 'quote': 'Different Brand'},
        ]:
            with self.subTest(evidence=evidence):
                second = self.record('Different Brand', run_id='second', name_evidence=evidence)
                bid, _ = self.store.upsert(second)
                self.assertEqual(aid, bid)
                self.assertEqual(second['company_name'], 'Candidate Brand')
                self.assertEqual(second['name_evidence'], first['name_evidence'])
                self.assertEqual(self.latest_observation()['name_evidence'], evidence)

    def test_same_level_fallback_cannot_claim_strength_from_a_quote(self):
        first = self.record()
        self.store.upsert(first)
        second = self.record('example', run_id='second', name_reason='domain_fallback')
        self.store.upsert(second)
        self.assertEqual(second['company_name'], first['company_name'])
        self.assertEqual(second['name_evidence'], first['name_evidence'])

    def test_same_level_unsupported_conflict_requires_review(self):
        first = self.record(name_evidence=None, name_reason='legacy_unverified')
        aid, _ = self.store.upsert(first)
        second = self.record('Different Brand', run_id='second', name_evidence=None,
                             name_reason='legacy_unverified')
        self.store.upsert(second)
        self.assertEqual(second['company_name'], 'Candidate Brand')
        self.assert_name_review(aid)

    def test_higher_level_official_evidence_corrects_lower_level_name(self):
        aid, _ = self.store.upsert(self.record('Old Product Title', name_evidence=None,
                                               name_reason='legacy_unverified'))
        second = self.record(run_id='second', name_status='official_observed', name_reason='self_declaration')
        bid, _ = self.store.upsert(second)
        self.assertEqual(aid, bid)
        self.assertEqual(second['company_name'], 'Candidate Brand')
        self.assertEqual(second['name_status'], 'official_observed')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reviews').fetchone()[0], 0)

    def test_higher_level_label_without_valid_official_evidence_cannot_replace_name(self):
        first = self.record()
        aid, _ = self.store.upsert(first)
        second = self.record('Different Brand', run_id='second', name_status='official_observed',
                             name_evidence={'url': 'https://foreign.example/', 'quote': 'Different Brand'})
        self.store.upsert(second)
        self.assertEqual(second['company_name'], first['company_name'])
        self.assertEqual(second['name_evidence'], first['name_evidence'])
        self.assertEqual(second['name_status'], 'needs_review')
        self.assert_name_review(aid)
