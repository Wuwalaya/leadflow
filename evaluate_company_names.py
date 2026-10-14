"""Replay selected local name samples without APIs or original-file writes.

These are development/regression samples, not an independent test set.
Built-in answers describe synthetic fixtures, not live evidence or human gold.
"""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from leadflow import host, name_details, norm, write_json


# Explicitly synthetic development samples; no private run directories or companies.
SAMPLES = {
    'synthetic-name-cases': {
        'alloy.example': 'Example Alloy Supply',
        'ambiguous.example': None,
    },
}


def load_samples(root):
    samples = []
    for folder, domains in SAMPLES.items():
        source = root / folder
        leads_path = source / 'leads.json'
        leads = json.loads(leads_path.read_text(encoding='utf-8'))
        with (source / 'leads.csv').open(encoding='utf-8-sig', newline='') as handle:
            csv_rows = list(csv.DictReader(handle))
        extraction_files = sorted((source / 'extraction').glob('*.json'))
        if extraction_files:
            batches = [json.loads(path.read_text(encoding='utf-8')) for path in extraction_files]
        else:
            extraction_files = [source / 'tavily_extract_responses.json']
            batches = json.loads(extraction_files[0].read_text(encoding='utf-8'))
        pages = [{'url': row['url'], 'text': row.get('raw_content') or ''}
                 for batch in batches for row in batch.get('response', batch).get('results', [])]
        inputs = [leads_path, source / 'leads.csv'] + extraction_files
        hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
        for domain, expected in domains.items():
            lead = next(row for row in leads if row['domain'] == domain)
            csv_row = next(row for row in csv_rows if host(row['Website']) == domain)
            if csv_row['Company Name'] != lead['company_name']:
                raise ValueError(f'CSV/JSON name mismatch in {folder}: {domain}')
            official_pages = [page for page in pages if host(page['url']) == domain]
            if not official_pages:
                raise ValueError(f'Missing saved extraction for {folder}: {domain}')
            # Supply saved name only as a compatibility hint. It cannot confirm
            # itself without the official-page evidence.
            result = name_details(official_pages, domain, title=lead['company_name'])
            if expected and not any(expected.replace("'", '’') in page['text'] or expected in page['text'] for page in official_pages):
                raise ValueError(f'Developer reference absent from saved evidence: {domain}')
            samples.append({'sample_id': folder + ':' + domain, 'domain': domain,
                            'before_name': lead['company_name'], 'after': result,
                            'developer_expected_name': expected, 'pages': official_pages,
                            'source_sha256': hashes})
    return samples


def evaluate(samples, human_labels=None):
    """Explicit labels carry expected_name, reviewer, and exact name_evidence.

    Unsupported labels fail rather than silently becoming gold answers. Name
    correctness compares spelling with case/punctuation normalization; legal
    entities and brand aliases are never silently treated as equivalent.
    """
    labels = human_labels or {}
    known_ids = {sample['sample_id'] for sample in samples}
    if set(labels) - known_ids:
        raise ValueError('Human labels refer to samples outside this development set')
    counters = {kind: {'total': 0, 'before_correct': 0, 'after_correct': 0}
                for kind in ('human_gold', 'developer_evidence_review')}
    statuses = Counter()
    unlabeled_statuses = Counter()
    rows = []
    for sample in samples:
        expected = sample['developer_expected_name']
        kind = 'developer_evidence_review'
        if sample['sample_id'] in labels:
            label = labels[sample['sample_id']]
            if not isinstance(label, dict):
                raise ValueError('Human label must be an object')
            expected = label.get('expected_name')
            evidence = label.get('name_evidence') or {}
            quote = evidence.get('quote', '')
            if (sample['after'].get('name_reason') == 'third_party_subject_not_verified'
                    or not isinstance(expected, str) or not expected.strip() or not label.get('reviewer')
                    or not isinstance(quote, str) or expected not in quote
                    or not any(page['url'] == evidence.get('url') and quote in page['text'] for page in sample['pages'])):
                raise ValueError('Human label requires reviewer and exact saved official name evidence')
            kind = 'human_gold'
        result = sample['after']
        statuses[result['name_status']] += 1
        before_correct = after_correct = None
        if expected:
            before_correct = norm(sample['before_name']) == norm(expected)
            after_correct = (norm(result['company_name']) == norm(expected)
                             and result['name_status'] in {'official_observed', 'human_confirmed'})
            counter = counters[kind]
            counter['total'] += 1
            counter['before_correct'] += int(before_correct)
            counter['after_correct'] += int(after_correct)
        else:
            unlabeled_statuses[result['name_status']] += 1
        rows.append({key: sample[key] for key in ('sample_id', 'domain', 'before_name', 'source_sha256')} | {
            'after': result, 'expected_name': expected,
            'label_source': kind if expected else 'unlabeled',
            'before_correct': before_correct, 'after_correct': after_correct,
        })
    return {'dataset_role': 'development_regression_not_independent_test',
            'sample_count': len(samples), **counters,
            'after_statuses': dict(statuses), 'unlabeled_after_statuses': dict(unlabeled_statuses),
            'api_calls': 0, 'samples': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-root', type=Path, default=Path('fixtures/name_evaluation'))
    parser.add_argument('--human-labels', type=Path, help='Optional local JSON keyed by sample_id; never assume automated output is human gold')
    parser.add_argument('--out', type=Path, required=True, help='New evaluation directory; must not exist')
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Evaluation output must be a new directory; originals cannot be overwritten')
    samples = load_samples(args.input_root)
    labels = json.loads(args.human_labels.read_text(encoding='utf-8')) if args.human_labels else None
    report = evaluate(samples, labels)
    # Validate first, then write only the new directory; no SQLite opened here.
    args.out.mkdir(parents=True, exist_ok=False)
    write_json(args.out / 'name_evaluation.json', report)
    print(json.dumps({key: value for key, value in report.items() if key != 'samples'}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
