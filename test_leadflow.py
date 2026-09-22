import copy, json, tempfile, unittest
from pathlib import Path
from leadflow import JEV_ENDPOINT, JEV_MODEL, Store, candidate_rejection, contacts, csv_safe, export_discovery, format_top_candidates, host, market_discovery_queries, name_key, observed_company_name, qualification_bucket, relevant_evidence, rule_classify, query_plan, llm_classify, jev_smoke
from unittest.mock import patch

class LeadFlowTests(unittest.TestCase):
    def test_url_canonicalization_preserves_company(self):
        self.assertEqual(host('HTTP://WWW.Example.COM.BR/catalog?q=x'), 'example.com.br')
        self.assertNotEqual(host('https://a.com.br'), host('https://b.com.br'))
    def test_names_accents_and_suffixes(self):
        self.assertEqual(name_key('Açotubo LTDA'), name_key('ACOTUBO'))
    def test_email_only_observed_company_domain(self):
        ps = [{'url': 'https://inox.example.br', 'text': 'vendas@inox.example.br agency@webdesign.example.br dpo@inox.example.br example@example.com'}]
        got = contacts(ps)
        self.assertEqual([x['value'] for x in got], ['vendas@inox.example.br'])
        self.assertEqual(got[0]['role'], 'sales')
    def test_missing_email_stays_empty(self):
        self.assertEqual(contacts([{'url':'https://inox.example.br', 'text':'contact form only [email protected]'}]), [])
    def test_bars_do_not_imply_flat_bar(self):
        x = rule_classify([{'url':'https://inox.example.br', 'text':'Distribuidora Aço Inox\nBarras\nSão Paulo'}], 'Stainless Steel', 'Brazil', 'profiles', 'Example')
        self.assertEqual(x['product_matches'], [])
    def test_br_domain_not_country_evidence(self):
        x = rule_classify([{'url':'https://inox.example.br', 'text':'Stainless Steel'}], 'Stainless Steel', 'Brazil', 'both', 'Example')
        self.assertFalse(x['country_match'])
    def test_scope_queries(self):
        self.assertEqual(len(query_plan('stainless steel','Brazil','flat')), 4)
        self.assertEqual(len(query_plan('stainless steel','Brazil','both')), 8)
    def test_csv_formula_guard(self):
        self.assertTrue(csv_safe(' =HYPERLINK("bad")').startswith("'"))
    def new_record(self, **kw):
        x = {'company_name': 'Example Inox', 'domain':'example.com.br', 'run_id':'test-run'}
        x.update(kw); return x
    def test_rerun_is_idempotent_and_preserves_sales_state(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(str(Path(d)/'db.sqlite'))
            a, action = s.upsert(self.new_record())
            s.db.execute("UPDATE accounts SET owner='sales-1',status='contacted' WHERE id=?", (a,)); s.db.commit()
            b, action2 = s.upsert(self.new_record(company_name='EXAMPLE INOX LTDA'))
            self.assertEqual(a,b); self.assertEqual(action2, 'existing_updated'); self.assertEqual(s.count(),1)
            self.assertEqual(s.db.execute('SELECT owner,status FROM accounts WHERE id=?',(a,)).fetchone(), ('sales-1','contacted'))
            s.db.close()
    def test_cross_domain_same_name_requires_review(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(str(Path(d)/'db.sqlite'))
            s.upsert(self.new_record()); s.upsert(self.new_record(domain='another.com.br'))
            self.assertEqual(s.count(),2)
            self.assertEqual(s.db.execute('SELECT count(*) FROM reviews').fetchone()[0],1)
            s.db.close()
    def test_subsidiaries_not_merged_on_same_domain(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(str(Path(d)/'db.sqlite'))
            s.upsert(self.new_record(human_verified_registry_id='ABC001'))
            s.upsert(self.new_record(company_name='Subsidiary',human_verified_registry_id='ABC002'))
            self.assertEqual(s.count(),2); s.db.close()
    def test_approved_domain_alias(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(str(Path(d)/'db.sqlite'))
            a,_ = s.upsert(self.new_record())
            b,act = s.upsert(self.new_record(domain='verified-alias.com'), {'verified-alias.com': a})
            self.assertEqual(a,b); self.assertEqual(s.count(),1); s.db.close()
    def test_invalid_llm_quotes_are_removed(self):
        class FakeAPI:
            def post(self,*args):
                obj = {'company_name':'Invented', 'name_evidence':{'url':'https://example.com.br','quote':'not there'},'customer_type':'distributor','type_evidence':{'url':'https://example.com.br','quote':'not there'},'country_match': True, 'country_evidence':None, 'material_evidence':None,'product_matches':[{'form':'flat_bar','evidence':{'url':'https://example.com.br','quote':'invented'}}]}
                return {'choices':[{'message':{'content':json.dumps(obj)}}]}
        with patch.dict('os.environ', {'LLM_MODEL':'test', 'LLM_API_KEY':'fake-not-live'}):
            x = llm_classify(FakeAPI(), [{'url':'https://example.com.br','text':'actual page'}], 'Stainless Steel','Brazil','both')
        self.assertEqual(x['customer_type'], 'unknown'); self.assertFalse(x['country_match']); self.assertEqual(x['product_matches'], [])
    def test_jev_smoke_uses_pinned_model_and_validates_typed_answers(self):
        class FakeAPI:
            def post(self, endpoint, key, payload):
                self.endpoint, self.key, self.payload = endpoint, key, payload
                return {
                    'id': 'decision-test',
                    'model': 'typesafe/jev-1.13-test-snapshot',
                    'answers': {
                        'buyer_role': {
                            'type': 'choice',
                            'choice': 'distributor',
                            'probabilities': {'distributor': 0.91, 'fabricator': 0.04, 'mill': 0.01, 'unknown': 0.04},
                            'confidence': 0.91,
                        },
                        'stainless_product_relevance': {'type': 'noul', 'noul': 0.98},
                    },
                    'usage': {'input_tokens': 123, 'output_tokens': 0},
                }
        fixture = {
            'companies': [{
                'company_name': 'Example Inox',
                'website': 'https://example.com.br/',
                'pages': [{'url': 'https://example.com.br/', 'text': 'Distribuidora de aço inox e chapas'}],
            }]
        }
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'reference.json'
            path.write_text(json.dumps(fixture), encoding='utf-8')
            api = FakeAPI()
            with patch.dict('os.environ', {'TYPESAFE_API_KEY': 'fake-not-live'}):
                result = jev_smoke(api, str(path))
        self.assertEqual(api.endpoint, JEV_ENDPOINT)
        self.assertEqual(api.payload['model'], JEV_MODEL)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['answers']['buyer_role']['choice'], 'distributor')
    def test_discovery_filter_rejects_non_company_results(self):
        self.assertEqual(candidate_rejection({'url':'https://www.linkedin.com/company/inox', 'title':'Inox'}), 'invalid_or_blocked_url')
        self.assertEqual(candidate_rejection({'url':'https://example.com.br/blog/noticia-inox', 'title':'Notícia'}), 'search_news_or_blog_page')
        self.assertEqual(candidate_rejection({'url':'https://evento.com.br/expositor/inox', 'title':'Inox'}), 'search_news_or_blog_page')
        self.assertEqual(candidate_rejection({'url':'https://www.turkishexporter.com.tr/en/companies/inox', 'title':'Inox'}), 'directory_marketplace_or_search_host')
        self.assertIsNone(candidate_rejection({'url':'https://example.com.br/produtos/aco-inox', 'title':'Example Inox'}))
    def test_relevant_evidence_keeps_product_lines_and_drops_noise(self):
        text = 'Política de cookies e navegação\nDistribuímos aço inoxidável em chapas e bobinas no Brasil.\nTermos gerais do website'
        self.assertEqual(relevant_evidence(text), 'Distribuímos aço inoxidável em chapas e bobinas no Brasil.')
    def test_observed_company_name_uses_page_heading(self):
        self.assertEqual(observed_company_name('Product | Search title', '# Inox Brasil\n## Produtos', 'inoxbrasil.com.br'), 'Inox Brasil')
        self.assertEqual(observed_company_name('About us', '', 'lminox.com.br', 'LM INOX is a distributor'), 'LM INOX')
    def test_market_discovery_queries_cover_requested_roles_and_products(self):
        queries = ' '.join(market_discovery_queries()).lower()
        self.assertGreaterEqual(len(market_discovery_queries()), 10)
        for term in ('distribuidor', 'centro de serviços', 'chapas', 'bobinas', 'barras chatas', 'cantoneiras', 'perfis', 'service center'):
            self.assertIn(term, queries)
    def test_qualification_buckets_are_configurable_heuristics(self):
        self.assertEqual(qualification_bucket(.9, .9, .8, 'distributor')[0], 'HIGH')
        self.assertEqual(qualification_bucket(.8, .9, .6, 'distributor')[0], 'REVIEW')
        self.assertEqual(qualification_bucket(.9, .9, .2, 'distributor')[0], 'LOW')
        self.assertEqual(qualification_bucket(.9, .9, .9, 'unknown')[0], 'REVIEW')
    def test_domain_dedup_updates_discovery_columns_and_export(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(str(Path(d) / 'db.sqlite'))
            first = self.new_record(company_name='Example Inox', website='https://example.com.br/', country='Brazil', customer_type='distributor', emails=['sales@example.com.br'], product_relevance=.9, qualification_status='HIGH', evidence_urls=['https://example.com.br/inox'])
            aid, action = store.upsert(first)
            second = dict(first, company_name='Example Inox Group', run_id='run-2')
            bid, action2 = store.upsert(second)
            self.assertEqual((aid, action, bid, action2), (aid, 'new', aid, 'existing_updated'))
            row = store.db.execute('SELECT canonical_company_name,website,country,customer_type,product_relevance,qualification_status FROM accounts WHERE id=?', (aid,)).fetchone()
            self.assertEqual(row, ('Example Inox Group','https://example.com.br/','Brazil','distributor',.9,'HIGH'))
            record = dict(second, account_id=aid, dedup_action=action2, canonical_company_name='Example Inox Group', product_match=['sheet_plate'], evidence_summary='Official evidence', review_notes='Prototype heuristic')
            summary = {'run_id':'run-2'}
            export_discovery(Path(d), [record], summary, store)
            header = (Path(d) / 'leads.csv').read_text(encoding='utf-8-sig').splitlines()[0]
            self.assertEqual(header, 'Company Name,Website,Country,Customer Type,Public Email,Product Match,Product Relevance Probability,Qualification Status,Evidence URL,Evidence Summary,Review Notes')
            store.db.close()
    def test_top_candidates_table_is_ranked_limited_and_honest_about_email(self):
        records = []
        for index in range(12):
            records.append({
                'company_name': f'Company {index:02d}', 'customer_type': 'distributor',
                'qualification_status': 'HIGH' if index < 5 else 'REVIEW',
                'product_relevance': 1 - index / 20, 'relevance_probability': .9,
                'emails': ['sales@example.com'] if index in {0, 2, 4} else [],
            })
        table = format_top_candidates(records)
        self.assertEqual(sum(line.startswith('Company ') and not line.startswith('Company    ') for line in table.splitlines()), 10)
        self.assertIn('Company 00', table)
        self.assertNotIn('Company 11', table)
        self.assertIn('HIGH: 5 | REVIEW: 7 | Public email: 3', table)
        self.assertIn('sales@example.com', table)

if __name__ == '__main__': unittest.main(verbosity=2)
