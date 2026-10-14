"""Budgeted official Contact/Sales evidence, with an offline-first cache replay.

No email generation, delivery check, form submission, or SQLite access.
"""
from __future__ import annotations

import copy
import re
from collections import Counter
from urllib.parse import urldefrag, urljoin, urlparse

from company_names import _identity_key
from leadflow import EMAIL, host, name_details, public_url, candidate_rejection


CONTACT_TERMS = re.compile(r'contact|contato|fale[-_ /]?conosco|sales|comercial|orcamento|orçamento|locations', re.I)
FORM = re.compile(r'<form\b[^>]*>.*?</form>|contact form|formulário de contato|formulario de contato|preencha o formulário', re.I | re.S)
HIDDEN_EMAIL = re.compile(r'\[email\s*protected\]|/cdn-cgi/l/email-protection|data-cfemail|\b[\w.+-]+\s*(?:\[at\]|\(at\))\s*[\w.-]+', re.I)
NON_SALES = {'rh', 'hr', 'dpo', 'privacidade', 'privacy', 'carreiras', 'jobs', 'careers', 'recruiting', 'noreply', 'no-reply'}


def url_key(url):
    parsed = urlparse(url)
    return host(url), parsed.path.rstrip('/') or '/', parsed.query


def email_values(record):
    return [item.get('value', '') if isinstance(item, dict) else item for item in record.get('emails', [])]


def contact_role(email):
    local = email.rsplit('@', 1)[0]
    if any(word in local for word in ('compras', 'purchas', 'procurement')):
        return 'purchasing'
    if any(word in local for word in ('vendas', 'comercial', 'sales')):
        return 'sales'
    if any(word in local for word in ('contato', 'contact', 'info', 'relacionamento')):
        return 'generic'
    return 'unclassified'


def identity_supported(record, pages):
    evidence = record.get('name_evidence') or {}
    if not isinstance(evidence, dict):
        return False
    url, quote = evidence.get('url', ''), evidence.get('quote', '')
    if not isinstance(url, str) or not isinstance(quote, str):
        return False
    return (record.get('name_status') in {'official_observed', 'human_confirmed'}
            and bool(quote) and public_url(url) and host(url) == host(record['domain'])
            and bool(_identity_key(record.get('company_name', '')))
            and _identity_key(record.get('company_name', '')) in _identity_key(quote)
            and any(url_key(page['url']) == url_key(url) and quote in page.get('text', '') for page in pages))


def observe_contacts(record, pages, identity_pages=None):
    """Keep every observed occurrence, including rejected emails and exact quotes.

    A name label alone is insufficient. Require its saved source/quote, and reject
    conflicting page subjects even when an email has the target domain.
    """
    identity_pages = identity_pages if identity_pages is not None else pages
    supported = identity_supported(record, identity_pages)
    rows, page_diagnostics = [], []
    for page in pages:
        url, text = page.get('url', ''), page.get('text') or ''
        official = public_url(url) and host(url) == host(record['domain']) and not candidate_rejection({'url': url})
        page_name = name_details([page], record['domain'])
        conflicts = any(_identity_key(item['name']) != _identity_key(record.get('company_name', ''))
                        for item in page_name.get('name_candidates', []))
        stage = {'url': url, 'content_status': 'fetched' if text.strip() else 'empty_content',
                 'content_scope': 'available_extracted_text_completeness_unknown',
                 'email_matches': 0, 'accepted_emails': 0, 'rejected_emails': 0,
                 'visibility_limits': []}
        for match in HIDDEN_EMAIL.finditer(text):
            stage['visibility_limits'].append({'quote': match.group(), 'reason': 'email_encoding_or_protection_observed'})
        for match in EMAIL.finditer(text):
            email = match.group().strip('.,;:').lower()
            local, domain = email.rsplit('@', 1)
            # Center the quote around the occurrence, preserving raw page text.
            start = max(text.rfind('\n', 0, match.start()) + 1, match.start() - 140)
            end = text.find('\n', match.end())
            end = min(len(text) if end < 0 else end, match.end() + 140)
            quote = text[start:end]
            reasons = []
            if (local.startswith('.') or local.endswith('.') or '..' in local
                    or any(label.startswith('-') or label.endswith('-') for label in domain.split('.'))):
                reasons.append('invalid_email_syntax')
            if not official:
                reasons.append('source_not_target_official_site')
            if domain in {'example.com', 'email.com', 'domain.com'} or domain.endswith(('.png', '.jpg', '.webp', '.svg')):
                reasons.append('placeholder_or_asset')
            if host('https://' + domain) != host(record['domain']):
                reasons.append('email_domain_mismatch_requires_review')
            if any(local == word or local.startswith(word + '.') for word in NON_SALES):
                reasons.append('non_sales_role')
            if re.search(r'website by|site by|powered by|built by|desenvolvid[oa] por|web design|webdesign|privacy|careers|recruitment', quote, re.I):
                reasons.append('agency_or_non_sales_context')
            if re.search(r'partner|customer|subsidiar|parent company|parceiro|cliente', quote, re.I):
                reasons.append('other_entity_context_requires_review')
            if not supported:
                reasons.append('company_identity_unresolved')
            if conflicts:
                reasons.append('page_subject_conflict')
            rows.append({'type': 'email', 'value': email, 'role': contact_role(email),
                         'source_url': url, 'quote': quote, 'observed_text': match.group(),
                         'verification': 'rejected_auto_requires_review' if reasons else 'publicly_observed_not_delivery_verified',
                         'validation_status': 'rejected' if reasons else 'accepted', 'reasons': reasons,
                         'identity_evidence': copy.deepcopy(record.get('name_evidence'))})
            stage['email_matches'] += 1
            stage['rejected_emails' if reasons else 'accepted_emails'] += 1
        if official and CONTACT_TERMS.search(urlparse(url).path + ' ' + text):
            for match in FORM.finditer(text):
                if match.group().lower().startswith('<form'):
                    form_text = match.group()
                    if (not re.search(r'textarea|message|mensagem', form_text, re.I)
                            or re.search(r'newsletter|subscribe|login|password|type=["\']search', form_text, re.I)):
                        continue
                reasons = ([] if supported else ['company_identity_unresolved'])
                if conflicts:
                    reasons.append('page_subject_conflict')
                rows.append({'type': 'contact_form', 'value': url, 'role': 'generic',
                             'source_url': url, 'quote': match.group()[:400], 'observed_text': match.group()[:400],
                             'verification': 'rejected_auto_requires_review' if reasons else 'public_form_observed_not_submission_verified',
                             'validation_status': 'rejected' if reasons else 'accepted', 'reasons': reasons,
                             'identity_evidence': copy.deepcopy(record.get('name_evidence'))})
                break
        page_diagnostics.append(stage)
    return rows, page_diagnostics


def discover_contact_urls(record, pages, hints=()):
    """Only observed URLs/links; never synthesize /contact paths or email patterns."""
    choices = []

    def add(url, origin, quote):
        parsed = urlparse(url)
        contact_location = parsed.path + ' ' + parsed.query + ' ' + parsed.fragment
        url = urldefrag(url)[0]
        if (public_url(url) and host(url) == host(record['domain'])
                and not candidate_rejection({'url': url})
                and CONTACT_TERMS.search(contact_location)
                and url_key(url) not in {url_key(item['url']) for item in choices}):
            choices.append({'url': url, 'origin': origin, 'quote': quote})

    for page in pages:
        add(page['url'], 'cached_page', page['url'])
        text = page.get('text') or ''
        # Markdown, HTML and standalone absolute links are supported.
        for pattern in (r'\[[^\]\n]*\]\(([^\s)]+)', r'href=["\']([^"\']+)["\']', r'https?://[^\s<>"\')]+'):
            for match in re.finditer(pattern, text, re.I):
                value = match.group(1) if match.lastindex else match.group()
                add(urljoin(page['url'], value), 'official_page_link', match.group())
    for hint in hints:
        add(hint.get('url', ''), 'cached_search_url', hint.get('title') or hint.get('url', ''))
    return sorted(choices, key=lambda item: ('locations' in item['url'].lower(), item['origin'] == 'cached_search_url'))


class CachedContactSource:
    mode = 'offline_cache'

    def __init__(self, pages=(), hints=(), searches=(), failures=()):
        self.pages = {url_key(page['url']): page for page in pages}
        self.hints, self.searches = list(hints), list(searches)
        self.failures = {url_key(item['url']): item for item in failures if item.get('url')}
        self.calls = 0
        self.search_lookups, self.fetch_lookups = 0, 0

    def search(self, domain):
        self.search_lookups += 1
        matches = [item for item in self.searches if 'site:' + domain in item.get('query', '')
                   and CONTACT_TERMS.search(item.get('query', ''))]
        if not matches:
            return {'status': 'cache_miss', 'results': []}
        return {'status': 'completed', 'results': [row for item in matches for row in item.get('response', {}).get('results', [])]}

    def fetch(self, url):
        self.fetch_lookups += 1
        page = self.pages.get(url_key(url))
        if page:
            return {'status': 'fetched' if page.get('text', '').strip() else 'empty_content', 'page': page}
        failure = self.failures.get(url_key(url))
        return {'status': 'fetch_failed' if failure else 'cache_miss',
                'reason': 'saved_provider_failure' if failure else 'page_not_in_cache'}

    def usage(self):
        return {'api_calls_including_retries': 0, 'tavily_credits_reported': 0,
                'monetary_cost': 0, 'mode': self.mode,
                'cache_search_lookups': self.search_lookups, 'cache_fetch_lookups': self.fetch_lookups}


class LiveContactSource:
    mode = 'live_tavily'

    def __init__(self, api, max_calls):
        if max_calls < 0:
            raise ValueError('Contact budget must be non-negative')
        self.api = api
        self.start_calls, self.start_usage = api.calls, len(api.usage)
        self.ceiling = min(api.max_calls, api.calls + max_calls)
        self.audit = []
        self.hints = []
        self.search_requests, self.fetch_requests = 0, 0

    def _post(self, endpoint, payload, kind):
        from leadflow import APIBudgetExhausted
        if self.api.calls >= self.ceiling:
            raise APIBudgetExhausted('Contact API attempt budget exhausted')
        old_ceiling = self.api.max_calls
        # API.post also applies this ceiling to every retry.
        self.api.max_calls = self.ceiling
        try:
            import os
            response = self.api.post(endpoint, os.environ.get('TAVILY_API_KEY', ''), payload)
            self.audit.append({'kind': kind, 'request': payload, 'response': response})
            return response
        finally:
            self.api.max_calls = old_ceiling

    def search(self, domain):
        from leadflow import TAVILY_SEARCH_ENDPOINT
        self.search_requests += 1
        response = self._post(TAVILY_SEARCH_ENDPOINT, {
            'query': f'site:{domain} contact sales contato comercial', 'search_depth': 'basic',
            'max_results': 3, 'topic': 'general', 'include_answer': False,
            'include_raw_content': False, 'include_usage': True,
        }, 'search')
        return {'status': 'completed', 'results': response.get('results', [])}

    def fetch(self, url):
        from leadflow import TAVILY_EXTRACT_ENDPOINT
        self.fetch_requests += 1
        # Full contact content: no product query or chunk selection that hides emails.
        response = self._post(TAVILY_EXTRACT_ENDPOINT, {
            'urls': [url], 'extract_depth': 'basic', 'format': 'markdown',
            'include_images': False, 'include_usage': True,
        }, 'extract')
        for result in response.get('results', []):
            if url_key(result.get('url', '')) == url_key(url):
                text = result.get('raw_content') or ''
                return {'status': 'fetched' if text.strip() else 'empty_content',
                        'page': {'url': result['url'], 'text': text}}
        return {'status': 'fetch_failed', 'reason': 'provider_failed_or_missing_result'}

    def usage(self):
        entries = self.api.usage[self.start_usage:]
        credits = [entry.get('usage', {}).get('credits') if isinstance(entry.get('usage'), dict) else None for entry in entries]
        return {'api_calls_including_retries': self.api.calls - self.start_calls,
                'tavily_credits_reported': sum(credits) if credits and all(isinstance(c, (int, float)) for c in credits) else None,
                'successful_responses': len(entries),
                'usage_complete': len(entries) == self.api.calls - self.start_calls and all(c is not None for c in credits),
                'monetary_cost': None, 'mode': self.mode,
                'search_operations': self.search_requests, 'fetch_operations': self.fetch_requests}


def enrich_contacts(records, pages_by_domain, source, candidate_limit=3, pages_per_candidate=2):
    """Enrich a fixed cohort; existing email values and qualification stay intact.

    Unknown legacy names are re-observed from saved primary evidence only. Newly
    discovered Contact pages cannot establish a different company's identity.
    """
    if candidate_limit < 0 or pages_per_candidate < 0:
        raise ValueError('Contact candidate/page limits must be non-negative')
    before = copy.deepcopy(records)
    enriched, selected = [], 0
    for original in before:
        record = copy.deepcopy(original)
        domain = host(record['domain'])
        pages = list(pages_by_domain.get(domain, []))
        identity = dict(record)
        if record.get('name_status') not in {'official_observed', 'human_confirmed'}:
            identity.update(name_details(pages, domain, record.get('company_name', '')))
        # Names stay on their existing persistence/review path. This observation
        # is contact attribution evidence, not an automatic name overwrite.
        record['contact_identity'] = {key: identity.get(key) for key in
                                      ('company_name', 'name_status', 'name_reason', 'name_evidence')}
        rows, baseline_stages = observe_contacts(identity, pages)
        diagnostic = {'eligibility': 'eligible', 'page_discovery': {'status': 'not_attempted', 'urls': []},
                      'fetches': [], 'baseline_pages': baseline_stages, 'supplemental_pages': [],
                      'identity_validation': {'status': 'supported' if identity_supported(identity, pages) else 'unresolved',
                                              'reason': identity.get('name_reason')},
                      'outcome': 'not_attempted', 'not_found_means': 'not_observed_in_available_content',
                      'extraction_omission': 'unknown_requires_raw_page_or_human_evidence',
                      'validation_false_rejection': 'unknown_requires_human_review'}
        if record.get('qualification_status') not in {'HIGH', 'REVIEW'}:
            diagnostic.update(eligibility='not_high_or_review', outcome='not_eligible')
        elif email_values(record):
            diagnostic.update(eligibility='already_has_public_email', outcome='not_eligible')
        elif selected >= candidate_limit:
            diagnostic['outcome'] = 'candidate_budget_exhausted'
        else:
            selected += 1
            choices = discover_contact_urls(identity, pages, source.hints)
            searched = False
            if not choices:
                searched = True
                try:
                    search = source.search(domain)
                    diagnostic['page_discovery']['status'] = search['status']
                    choices = discover_contact_urls(identity, pages, search.get('results', []))
                except Exception as exc:
                    diagnostic['page_discovery']['status'] = 'budget_exhausted' if type(exc).__name__ == 'APIBudgetExhausted' else 'search_failed'
                    diagnostic['page_discovery']['reason'] = diagnostic['page_discovery']['status']
            if choices:
                diagnostic['page_discovery']['status'] = 'found'
            elif diagnostic['page_discovery']['status'] == 'completed':
                diagnostic['page_discovery']['status'] = 'not_found_in_search'
            diagnostic['page_discovery']['urls'] = choices
            extra_pages = []
            known = {url_key(page['url']): page for page in pages}
            for choice in choices[:pages_per_candidate]:
                url = choice['url']
                try:
                    fetched = ({'status': 'fetched', 'page': known[url_key(url)]} if url_key(url) in known else source.fetch(url))
                except Exception as exc:
                    fetched = {'status': 'budget_exhausted' if type(exc).__name__ == 'APIBudgetExhausted' else 'fetch_failed',
                               'reason': 'request_failed'}
                diagnostic['fetches'].append({'url': url, **{k: v for k, v in fetched.items() if k != 'page'}})
                if fetched['status'] == 'fetched' and url_key(url) not in known:
                    extra_pages.append(fetched['page'])
            extra_rows, extra_stages = observe_contacts(identity, extra_pages, pages)
            rows.extend(extra_rows)
            diagnostic['supplemental_pages'] = extra_stages
            accepted_emails = [row['value'] for row in rows if row['type'] == 'email' and row['validation_status'] == 'accepted']
            accepted_forms = [row for row in rows if row['type'] == 'contact_form' and row['validation_status'] == 'accepted']
            failures = {item['status'] for item in diagnostic['fetches'] if item['status'] != 'fetched'}
            if diagnostic['page_discovery']['status'] in {'search_failed', 'budget_exhausted', 'cache_miss'}:
                failures.add(diagnostic['page_discovery']['status'])
            all_stages = baseline_stages + extra_stages
            outcomes = []
            if accepted_emails:
                outcomes.append('email_found')
                record['emails'] = sorted(set(accepted_emails))
            if accepted_forms:
                outcomes.append('form_observed')
            outcomes.extend(sorted(failures))
            if any(stage['visibility_limits'] for stage in all_stages):
                outcomes.append('email_visibility_limited')
            if any(row['type'] == 'email' and row['validation_status'] == 'rejected' for row in rows):
                outcomes.append('emails_rejected')
            if len(choices) > pages_per_candidate:
                outcomes.append('page_budget_exhausted')
            if not outcomes:
                outcomes.append('no_contact_page_observed' if not choices else 'no_email_observed_in_fetched_content')
            diagnostic['outcome'] = outcomes[0]
            diagnostic['outcomes'] = outcomes
            diagnostic['search_performed'] = searched
        record['contact_observations'], record['contact_diagnostics'] = rows, diagnostic
        record['contact_emails_before'] = copy.deepcopy(original.get('emails', []))
        enriched.append(record)
    cohort = [index for index, record in enumerate(before) if record.get('qualification_status') in {'HIGH', 'REVIEW'}]
    coverage = lambda rows: sum(bool(email_values(rows[index])) for index in cohort)
    metrics = {'candidate_count': len(before), 'high_review_denominator': len(cohort),
               'before_with_email': coverage(before), 'after_with_email': coverage(enriched),
               'before_email_coverage': coverage(before) / len(cohort) if cohort else None,
               'after_email_coverage': coverage(enriched) / len(cohort) if cohort else None,
               'selected_for_enrichment': selected,
               'new_email_records': sum(bool(email_values(enriched[index])) and not email_values(before[index]) for index in cohort),
               'legacy_email_records_requiring_review': sum(
                   bool(email_values(before[index])) and not set(email_values(before[index])).issubset(
                       {row['value'] for row in enriched[index]['contact_observations']
                        if row['type'] == 'email' and row['validation_status'] == 'accepted'}) for index in cohort),
               'outcomes': dict(Counter(record['contact_diagnostics']['outcome'] for record in enriched)),
               'additional_cost': source.usage(),
               'quality_note': 'Existing exported emails are carried forward, not upgraded to verified. New additions require identity evidence. Coverage is availability, not HIGH accuracy, buying intent or sales acceptance.'}
    return enriched, metrics
