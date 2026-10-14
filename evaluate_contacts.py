#!/usr/bin/env python3
"""Replay a frozen lead cohort against saved Contact caches; APIs require --live.

Writes a new audit directory only. Never opens SQLite or rewrites source data.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

import leadflow
from contact_enrichment import CachedContactSource, LiveContactSource, email_values, enrich_contacts, url_key
from leadflow import API, csv_safe, host, write_json


def read_json(path, hashes):
    if path.suffix != '.json':
        raise ValueError('Cache inputs must be JSON files')
    content = path.read_bytes()
    hashes[str(path.resolve())] = hashlib.sha256(content).hexdigest()
    return json.loads(content)


def load_saved_cache(root, hashes):
    pages, hints, searches, failures = [], [], [], []
    files = []
    if root.is_file():
        files = [root]
    elif root.is_dir():
        files = [root / name for name in ('tavily_search_responses.json', 'tavily_extract_responses.json',
                                          'accepted_search_candidates.json', 'contact_api_responses.json')
                 if (root / name).is_file()]
        files += sorted((root / 'search').glob('*.json')) + sorted((root / 'extraction').glob('*.json'))
    else:
        raise ValueError('Cache path does not exist')
    for path in files:
        data = read_json(path, hashes)
        if path.name == 'accepted_search_candidates.json':
            for item in data:
                for url in item.get('discovery_urls', [item.get('primary_url', '')]):
                    hints.append({'url': url})
            continue
        if isinstance(data, dict) and 'pages' in data:
            pages.extend(data['pages'])
            searches.extend(data.get('searches', []))
            hints.extend(data.get('hints', []))
            failures.extend(data.get('failures', []))
            continue
        batches = data if isinstance(data, list) else [data]
        for batch in batches:
            response = batch.get('response', batch)
            results = response.get('results', [])
            if (batch.get('kind') == 'search' or batch.get('query')
                    or path.parent.name == 'search' or 'search_responses' in path.name):
                query = batch.get('query') or batch.get('request', {}).get('query', '')
                searches.append({'query': query, 'response': response})
                hints.extend(results)
            else:
                pages.extend({'url': row['url'], 'text': row.get('raw_content') or ''} for row in results)
                failures.extend(response.get('failed_results', []))
    return pages, hints, searches, failures


def baseline_pages(records, pages):
    by_domain = {}
    for record in records:
        urls = record.get('evidence_urls') or [item['url'] for item in record.get('sources', []) if item.get('url')]
        if not urls and record.get('source_url'):
            urls = [record['source_url']]
        if not urls:
            raise ValueError('Cannot freeze baseline without saved source URLs: ' + record['domain'])
        keys = {url_key(url) for url in urls}
        by_domain[host(record['domain'])] = [page for page in pages
                                           if host(page['url']) == host(record['domain']) and url_key(page['url']) in keys]
    return by_domain


def review_template(records, dataset_id):
    """Blank human labels; a HIGH bucket is never substituted for a decision."""
    return {
        'dataset_id': dataset_id,
        'sales_acceptance_definition': 'accepted / (accepted + rejected); hold and unreviewed excluded. Report reviewed/total and HIGH separately.',
        'allowed_sales_decisions': ['accepted', 'rejected', 'hold'],
        'allowed_failure_types': ['wrong_company_identity', 'wrong_country', 'wrong_material_or_form',
                                 'wrong_buyer_role', 'wrong_contact_entity', 'non_sales_contact',
                                 'fetch_failure', 'extraction_omission', 'validation_false_rejection',
                                 'duplicate', 'stale_evidence', 'insufficient_evidence', 'not_contactable'],
        'contact_diagnosis_requirement': 'extraction_omission needs a visible email in raw-page evidence missed by the parser; validation_false_rejection needs a rejected observation and human identity evidence. Unobserved is not absent.',
        'rows': [{
            'sample_id': dataset_id + ':' + str(index) + ':' + record['domain'],
            'account_id': record.get('account_id'), 'domain': record['domain'],
            'company_name': record['company_name'], 'qualification_status': record.get('qualification_status'),
            'name_status': record.get('name_status'), 'contact_identity': record.get('contact_identity'),
            'observed_emails': email_values(record), 'contact_outcome': record['contact_diagnostics']['outcome'],
            'evidence_urls': record.get('evidence_urls', []),
            'reviewer': None, 'reviewed_at': None, 'sales_decision': None,
            'company_identity_correct': None, 'country_correct': None,
            'material_and_form_correct': None, 'buyer_role_correct': None,
            'contact_entity_correct': None, 'failure_types': [],
            'review_evidence': [], 'notes': '',
        } for index, record in enumerate(records)]}


def evaluate(source_path, out, contact_cache=None, candidate_limit=3, pages_per_candidate=2,
             max_api_calls=4, live=False):
    if min(candidate_limit, pages_per_candidate, max_api_calls) < 0:
        raise ValueError('Limits must be non-negative')
    if out.exists():
        raise ValueError('Output directory must not exist; historical files are never overwritten')
    for source in (source_path, contact_cache):
        if source and source.is_dir():
            root = source.resolve()
            if out.resolve().is_relative_to(root):
                raise ValueError('Output must be outside source/cache directories')
    hashes = {}
    lead_path = source_path / 'leads.json' if source_path.is_dir() else source_path
    records = read_json(lead_path, hashes)
    if not isinstance(records, list) or not records:
        raise ValueError('Source must contain a non-empty saved lead list')
    root = source_path if source_path.is_dir() else source_path.parent
    pages, hints, searches, failures = load_saved_cache(root, hashes)
    frozen_pages = baseline_pages(records, pages)
    if contact_cache:
        extra_pages, extra_hints, extra_searches, extra_failures = load_saved_cache(contact_cache, hashes)
        pages.extend(extra_pages)
        hints.extend(extra_hints)
        searches.extend(extra_searches)
        failures.extend(extra_failures)
        hints.extend({'url': page['url']} for page in extra_pages)
    if live:
        # Match discover's allowlisted loader only for explicit live work.
        # Existing environment values retain precedence; offline never reads .env.
        if not os.environ.get('TAVILY_API_KEY'):
            leadflow.load_env_keys(Path('.env'), {'TAVILY_API_KEY'})
        if not os.environ.get('TAVILY_API_KEY'):
            raise ValueError('TAVILY_API_KEY missing for --live; set the environment variable or configure the project-root .env')
        source = LiveContactSource(API(out, max_calls=max_api_calls), max_api_calls)
        source.hints = hints
    else:
        source = CachedContactSource(pages, hints, searches, failures)
    enriched, metrics = enrich_contacts(records, frozen_pages, source, candidate_limit, pages_per_candidate)
    for path, digest in hashes.items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise ValueError('Source changed during evaluation: ' + path)
    dataset_id = hashlib.sha256(lead_path.read_bytes()).hexdigest()[:16]
    report = {'dataset_id': dataset_id, 'source_sha256': hashes, 'metrics': metrics,
              'limits': {'candidate_limit': candidate_limit, 'pages_per_candidate': pages_per_candidate,
                         'max_api_calls': max_api_calls if live else 0},
              'human_review': {'labeled': 0, 'sales_acceptance_rate': None, 'high_accuracy': None},
              'cohort_note': 'Same saved candidates and qualification; archived cache availability does not predict a future live gain.',
              'samples': [{key: record.get(key) for key in
                           ('domain', 'company_name', 'qualification_status', 'contact_identity',
                            'contact_observations', 'contact_diagnostics')} for record in enriched]}
    # Atomic creation refuses existing outputs, including races with another run.
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / 'contact_evaluation.json', report)
    write_json(out / 'leads.json', enriched)
    write_json(out / 'lead_review_template.json', review_template(enriched, dataset_id))
    if live:
        write_json(out / 'contact_api_responses.json', source.audit)
    with (out / 'contact_comparison.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['Domain', 'Qualification', 'Before Emails', 'After Emails', 'Contact Outcome'])
        for before, after in zip(records, enriched):
            writer.writerow([csv_safe(value) for value in
                             (before['domain'], before.get('qualification_status'),
                              '; '.join(email_values(before)), '; '.join(email_values(after)),
                              after['contact_diagnostics']['outcome'])])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--contact-cache', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--candidate-limit', type=int, default=3)
    parser.add_argument('--pages-per-candidate', type=int, default=2)
    parser.add_argument('--max-api-calls', type=int, default=4, help='Only applies with --live; includes retries')
    parser.add_argument('--live', action='store_true', help='Explicit opt-in: paid Tavily Contact smoke; no Jev or SQLite')
    args = parser.parse_args()
    try:
        report = evaluate(args.source, args.out, args.contact_cache, args.candidate_limit,
                          args.pages_per_candidate, args.max_api_calls, args.live)
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')
    print(json.dumps(report['metrics'], ensure_ascii=False, indent=2))
    if args.live:
        failed = any(set(row['contact_diagnostics'].get('outcomes', [])) &
                     {'search_failed', 'fetch_failed', 'empty_content', 'budget_exhausted'}
                     for row in report['samples'])
        return 1 if failed else 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
