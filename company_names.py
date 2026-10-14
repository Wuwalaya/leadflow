"""Conservative, offline company display names from saved official-page evidence.

official_observed describes automated source evidence, never human confirmation.
Search snippets and domain labels are display hints requiring review.
"""
import json
import re
import unicodedata
from html import unescape
from urllib.parse import urlparse


def _host(value):
    hostname = (urlparse(value if '://' in value else 'https://' + value).hostname or '').lower().rstrip('.')
    return hostname.removeprefix('www.')


def _key(value):
    value = unicodedata.normalize('NFKD', value.lower().replace('&', 'and'))
    return re.sub(r'[^a-z0-9]', '', value)


def _identity_key(value):
    value = re.sub(r'(?:\s+(?:incorporated|corporation|limited|ltda|inc|corp|co|ltd)\.?)+$', '', value, flags=re.I)
    return _key(value)


def _plausible(value):
    if not isinstance(value, str) or not 2 <= len(value) <= 100:
        return False
    if any(char in value for char in '|:<>\n') or re.search(r'https?://|@|\b(?:supplier|products?|locations?|information|headquarters)\b', value, re.I):
        return False
    # Generic product and navigation labels are not an entity declaration.
    tokens = re.findall(r'[\w]+', value.lower())
    if tokens and tokens[0] in {'it', 'we', 'our', 'they', 'you', 'this', 'these', 'today', 'as', 'since', 'with'}:
        return False
    generic = {'stainless', 'steel', 'flat', 'bar', 'sheet', 'plate', 'coil', 'metal', 'metals',
               'products', 'product', 'supplier', 'home', 'about', 'us', 'contact', 'services',
               'aluminum', 'carbon', 'profiles', 'alloys'}
    return bool(tokens) and any(token not in generic for token in tokens)


def _hint(title, search_content, domain):
    label = domain.split('.', 1)[0]
    for source in (title, search_content):
        words = re.findall(r'[A-Za-zÀ-ÿ0-9]+', source or '')
        for size in range(1, min(5, len(words) + 1)):
            for start in range(len(words) - size + 1):
                phrase = ' '.join(words[start:start + size])
                if _key(phrase) == _key(label):
                    return phrase, 'search_hint_only'
    return label[:100] or 'unknown', 'domain_fallback' if label else 'no_name_evidence'


def identify_company_name(pages, domain, title='', search_content=''):
    """Require same-domain entity declarations; preserve exact source quotes.

    Callers already filter company sites. Known directories are rejected by the
    LeadFlow adapter. Organization JSON-LD must carry its own matching URL;
    related organizations and product-schema names cannot establish site identity.
    """
    domain = _host(domain)
    candidates = []
    ambiguous = False
    official_content = False
    label = _key(domain.split('.', 1)[0])

    def add(name, url, quote, method):
        name = name.strip().rstrip('®™').strip()
        if _plausible(name):
            item = {'name': name, 'url': url, 'quote': quote, 'method': method}
            if item not in candidates:
                candidates.append(item)

    for page in pages:
        url, text = page.get('url', ''), page.get('text', '') or ''
        if _host(url) != domain:
            continue
        official_content = official_content or bool(text.strip())
        # A group landing page naming holdings and operating companies is not
        # enough to choose which account entity should receive a display name.
        if re.search(r'our companies', text, re.I) and re.search(r'holdings|headquarters', text, re.I):
            ambiguous = True
        for match in re.finditer(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.I | re.S):
            try:
                data = json.loads(match.group(1))
            except (ValueError, TypeError):
                continue
            nodes = data if isinstance(data, list) else [data]
            for node in list(nodes):
                if isinstance(node, dict):
                    nodes.extend(node.get('@graph', []) if isinstance(node.get('@graph'), list) else [])
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                types = node.get('@type', [])
                types = [types] if isinstance(types, str) else types
                if not isinstance(types, list) or not any(isinstance(t, str) and t in {'Organization', 'Corporation', 'LocalBusiness'} for t in types):
                    continue
                entity_url = node.get('url')
                if not isinstance(entity_url, str) or _host(entity_url) != domain:
                    continue
                name = node.get('name')
                if isinstance(name, str):
                    add(name, url, match.group(1), 'organization_schema')
        # Only explicit self-descriptions, not every capitalized mention, qualify.
        # Keep apostrophes, ampersands and hyphens in observed brand spellings.
        for line in text.splitlines():
            clean = re.sub(r'^\s*#+\s*', '', line).strip()
            declaration = re.compile(
                r'(?:^|[.!?]\s+|\[\.\.\.\]\s+)(?:At\s+)?'
                r'([A-ZÀ-Ý][\wÀ-ÿ’\'&®™.-]*(?:\s+(?:[A-ZÀ-Ý0-9][\wÀ-ÿ’\'&®™.-]*|&|and)){0,8})'
                r'(?:\s+(?:is|are|offers|supplies|stocks|provides|serves|crafts|has been)\b|,\s+we\b)')
            for match in declaration.finditer(clean):
                name = match.group(1)
                # A statement about a named parent/customer is not self identity.
                prefix = clean[:match.start(1)].lower()
                if re.search(r'parent|subsidiar|division|customer|partner', prefix):
                    continue
                add(name, url, line.strip()[:max(400, match.end())], 'self_declaration')
            if clean.startswith('At ') and ', ' in clean:
                name = clean[3:].split(', ', 1)[0]
                if re.search(r'\b(?:we|our)\b', clean[len(name) + 3:]):
                    add(name, url, line.strip()[:400], 'self_declaration')
            # A domain-supported heading/standalone brand can establish spelling,
            # but a domain abbreviation alone cannot expand itself to a full name.
            if _key(clean) == label and _plausible(clean) and not re.fullmatch(r'[a-z0-9-]+', clean):
                add(clean, url, line.strip(), 'official_brand_heading')
        for match in re.finditer(r'<title[^>]*>(.*?)</title>', text, re.I | re.S):
            for part in re.split(r'\s*[|–—]\s*', unescape(match.group(1))):
                if _key(part) == label and not re.fullmatch(r'[a-z0-9-]+', part.strip()):
                    add(part, url, match.group(0), 'official_title')

    keys = {_identity_key(item['name']) for item in candidates}
    if candidates:
        # Equivalent legal suffixes do not force the brand to become a legal name.
        priority = {'organization_schema': 0, 'self_declaration': 1, 'official_brand_heading': 2, 'official_title': 3}
        chosen = min(candidates, key=lambda item: (len(item['name']), priority[item['method']]))
        # A named company on a product page might be a supplier/customer rather
        # than the site operator. Require a site anchor or repeated declarations.
        anchored = any(
            item['method'] == 'organization_schema' or _key(item['name']) == label
            or urlparse(item['url']).path.rstrip('/') == ''
            or re.search(r'(?:^|/)(?:about(?:-us)?|empresa|quem-somos|sobre)(?:/|$)', urlparse(item['url']).path, re.I)
            for item in candidates)
        anchored = anchored or len({item['url'] for item in candidates}) > 1
        status = 'needs_review' if ambiguous or len(keys) > 1 or not anchored else 'official_observed'
        reason = ('group_entity_ambiguity' if ambiguous else 'conflicting_names' if len(keys) > 1
                  else 'site_subject_unresolved' if not anchored else chosen['method'])
        return {'company_name': chosen['name'], 'canonical_company_name': chosen['name'],
                'name_status': status, 'name_reason': reason,
                'name_evidence': {k: chosen[k] for k in ('url', 'quote')},
                'name_candidates': candidates}
    hint, reason = _hint(title, search_content, domain)
    return {'company_name': hint, 'canonical_company_name': hint,
            'name_status': 'unknown' if not official_content or hint == 'unknown' else 'needs_review',
            'name_reason': reason, 'name_evidence': None, 'name_candidates': []}
