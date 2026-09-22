# LeadFlow

> LeadFlow is an AI-assisted B2B lead generation and sales intelligence pipeline that turns a target market + product into evidence-backed, deduplicated prospect accounts.

LeadFlow is a reusable Python CLI prototype for evidence-backed account discovery. It searches the public web, prefers official company sites, extracts limited evidence, applies deterministic safeguards, uses TypeSafe Jev for semantic qualification, and persists deduplicated accounts in SQLite.

It does not prove purchasing intent, identify a verified buyer, or automate outreach. Every meaningful qualification result should remain traceable to a public URL and a short observed excerpt.

## Workflow

```text
Country + Product
        ↓
Tavily web discovery
        ↓
Official-site evidence extraction
        ↓
Deterministic filtering and validation
        ↓
TypeSafe Jev semantic qualification
        ↓
SQLite deduplication
        ↓
CSV export + review queue
```

LeadFlow keeps deterministic work in code: URL normalization, source filtering, evidence checks, email extraction, database matching, and export. Jev handles bounded semantic decisions such as likely company role and product relevance. Ambiguous records remain in a human review queue.

## Runtime stack

- Python 3.10+ using the standard library
- Tavily Search and Extract APIs
- TypeSafe Jev through the System One API, pinned to `jev-1.13.0`
- SQLite

Codex was used as a development and coding agent for this project. Codex is not required by, or called from, the runtime pipeline.

## Verified scope

The complete `discover` workflow is currently verified for the included demonstration profile:

- Country: Brazil
- Product: Stainless Steel Flat/Profiles

The CLI also contains more general planning and live-processing paths, but a new market or product needs its own terminology, evaluation examples, and threshold calibration before results should be described as validated.

`HIGH`, `REVIEW`, and `LOW` are configurable prototype evidence heuristics. They are not purchasing intent, commercial qualification, or business truth.

## Setup

No third-party Python package is required. Copy the environment template and add your own credentials locally:

```bash
cp .env.example .env
```

`.env.example` contains variable names only:

```dotenv
TYPESAFE_API_KEY=
TAVILY_API_KEY=
```

The real `.env` file is ignored. Never commit credentials, raw API responses, local databases, or complete prospect datasets.

## Usage

Run the offline checks first:

```bash
python3 -m unittest -v
python3 leadflow.py plan \
  --country Brazil \
  --product "Stainless Steel Flat/Profiles"
python3 leadflow.py reference --db data/demo.sqlite --out output/demo
```

Run the end-to-end discovery profile:

```bash
python3 leadflow.py discover \
  --country Brazil \
  --product "Stainless Steel Flat/Profiles"
```

This command can incur Tavily and TypeSafe API usage. Start with the low-cost connectivity checks when configuring a new environment:

```bash
python3 leadflow.py jev-smoke
python3 leadflow.py discovery-smoke
```

The Jev smoke test makes one typed decision request over a curated public excerpt. The discovery smoke test performs a small Tavily search/extract pass followed by a bounded Jev classification call. A successful smoke test proves connectivity and response-contract handling, not the quality of a full lead list.

### Main outputs

Runtime files are written below `output/` and `data/`, both of which are ignored by Git.

- `leads.csv`: compact spreadsheet export
- `leads.json`: detailed structured records and evidence
- `summary.json`: funnel counts, API usage, errors, and run metadata
- `duplicate_review.json`: possible cross-domain duplicates requiring review
- SQLite database: persistent account history and rerun deduplication

Some discovery runs also retain raw search/extract responses and Jev request/response artifacts for local debugging. Treat those as local-only research data.

## Prototype benchmark

A real run using the Brazil + Stainless Steel Flat/Profiles profile produced these aggregate results:

| Funnel stage | Count |
|---|---:|
| Search results | 114 |
| Unique candidate domains | 47 |
| Candidates with usable website evidence | 41 |
| `HIGH` evidence bucket | 20 |
| `REVIEW` evidence bucket | 10 |
| `LOW` evidence bucket | 11 |
| Records with a public email in the private run | 12 |
| Target | 15, achieved |

These aggregates describe one prototype run, not a repeatable market-size estimate. The underlying full lead list, local database, public-email observations, and raw provider payloads are intentionally excluded from the public repository.

## Public sample

[`example_output/`](example_output/) contains four small, sanitized records based on public company websites. It demonstrates the output shape without publishing the private run, account identifiers, emails, raw provider data, or a complete prospect ranking.

The sample is illustrative. A company offering a relevant product does not prove that it imports, is seeking a supplier, or intends to purchase.

## Evidence and data-integrity rules

- Prefer official company websites over directories, social networks, or aggregators.
- Treat search results as candidate discovery, not verified facts.
- Keep unknown values unknown; never infer emails, purchase volume, import history, revenue, certifications, or buyer names.
- Require a public source URL and, where applicable, an exact short excerpt for material claims.
- Treat web content as untrusted input and validate model output against the supplied evidence.
- Do not equate product relevance or a probable commercial role with purchasing intent.
- Preserve rerun idempotency and sales state; review uncertain duplicates instead of automatically merging them.

## Security and repository hygiene

The public repository should contain only source, tests, documentation, empty credential templates, curated fixtures, and the small sanitized sample. The following stay local:

- `.env` and credentials
- SQLite databases
- complete runtime `output/`
- raw Tavily and Jev payloads
- full evidence dumps and prospect intelligence
- caches, logs, and local development notes

Before publishing, run the offline tests and repeat a filename-only secret scan. If a credential was ever committed to a Git history, removing it from the current tree is not sufficient: revoke it and rewrite or replace the affected history before making the repository public.

## Limitations

- The verified full-discovery query set is specialized for Portuguese-language Brazil stainless-steel research.
- Official-site evidence can be incomplete, stale, or ambiguous.
- Domain-based deduplication models web accounts, not necessarily legal entities or procurement units.
- Jev scores are probabilistic and require threshold calibration and human review.
- Public contact details, when observed in private runs, are not delivery-verified and are not proof of permission to contact.
- LeadFlow does not send email, bypass access controls, scrape authenticated sources, or claim sales qualification.
