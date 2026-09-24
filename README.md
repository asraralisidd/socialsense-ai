# SocialSense AI

**AI-Powered Multimodal Social Media Content Intelligence Platform**

SocialSense AI analyzes social-media content across platforms using multimodal and contextual intelligence to help users understand sentiment, toxicity, spam, authenticity signals, narrative patterns, threat-related indicators, historical context, and the evidence behind each assessment.

## Problem

Social-media analysis is not only about sentiment. A useful intelligence platform needs to understand what people are saying, how they are saying it, whether comments are toxic or spam-like, whether content shows authenticity concerns, what narratives are appearing, whether threat-related signals exist, how current activity compares with historical activity, and what evidence contributed to an assessment — without conflating heuristic signals with proof or causality.

## What SocialSense AI Does

SocialSense AI provides a unified intelligence pipeline that ingests YouTube and Reddit content, enriches it with contextual and multimodal signals, and presents explainable, bounded assessments through a dark, data-focused web interface.

The platform is designed for analysts, moderators, and researchers who need structured insight into social content rather than raw comment dumps.

## Core Features

| Capability | Description |
|---|---|
| Social Content Analysis | Ingest YouTube videos and Reddit posts with metadata, comments, and transcripts |
| Comment Intelligence | Sentiment, toxicity, spam, duplicate/repetition, AI/bot-like indicators, critical-comment detection |
| Transcript Intelligence | YouTube transcript fetch with language fallbacks, relevance scoring, and topic alignment |
| Entity & Channel Context | Entity extraction and channel-level historical context |
| Authenticity Intelligence | Synthetic-media indicators with confidence and evidence (not definitive proof) |
| Threat Intelligence | Overall threat assessment with component scores, confidence, and coverage |
| Narrative Intelligence | Recurring narrative detection and growth signals |
| Historical Context | Bounded baseline comparison against recent analyses |
| Cross-Analysis Comparison | Current vs historical metrics with deviation labels |
| Narrative Evolution | Emerging, reappearing, declining, persistent, and insufficient-history states |
| Explainable Evidence | Claim → evidence links with source, snippet, and verification state |
| Reports & Exports | JSON, CSV, XLSX, PDF, DOCX and scheduled reports |
| Dashboard & Visualization | Risk, authenticity, and narrative overview with Chart.js |
| Account & Access Control | Authentication, roles, rate limiting, and lifecycle management |

## Intelligence Pipeline

```
Data Sources (YouTube / Reddit)
        ↓
Content & Metadata Ingestion
        ↓
Comment & Transcript Processing
        ↓
Content Intelligence (sentiment, toxicity, spam, duplicate, bot signals)
        ↓
Authenticity Intelligence (media signals)
        ↓
Threat & Narrative Intelligence (threat, narrative, coordination, propagation, temporal)
        ↓
Historical Context (baselines, cross-analysis, evolution)
        ↓
Evidence & Explainability (verified/unverified links, provenance)
        ↓
Dashboard / Reports / Exports
```

Each stage is heuristic and explainable; later stages consume only the evidence available from earlier stages.

## Comment Intelligence

Comment-level analysis contributes to broader content intelligence:

- Sentiment (positive/neutral/negative) with confidence
- Toxicity and harassment signals
- Spam and promotional detection
- Duplicate and repetition analysis
- AI/bot-like writing indicators
- Transcript relevance and topic alignment
- Entity-linked sentiment and risk where applicable

Signals are presented per comment and aggregated for overall risk.

## Authenticity Intelligence

The authenticity module assesses whether analyzed media may contain AI-generated or synthetic components. It combines thumbnail, audio (transcript-based inference), frame, and metadata signals into weighted indicators.

Exposed metrics include overall authenticity/AI probability, deepfake and voice-clone indicators, and explainable reasons with confidence. Results are presented as **indicators and signals, not definitive proof**, with explicit limitations and disclaimers.

## Threat & Narrative Intelligence

Threat assessment provides an overall assessment with component contributions, confidence, coverage, and evidence-backed reasons. Narrative analysis identifies recurring themes and their growth, coordination signals capture potential coordinated activity, and temporal analysis tracks narrative change over time.

All outputs include confidence/coverage, evidence, and limitations. The system provides **analytical signals, not deterministic threat prediction**, and does not establish causality.

## Historical Context & Explainable Intelligence

### Historical Context
Current analyses can be compared with a bounded set of recent analyses by the same user. Historical metrics provide context for current values.

### Baselines
Historical values form baselines for comparison. Deviation is described with hedged labels (typical, moderately/substantially above/below) rather than definitive judgments.

### Cross-Analysis Comparison
Analyses are compared using actual historical records with bounded windows and provenance-tagged values.

### Narrative Evolution
Narratives are described as emerging, reappearing, declining, persistent, or insufficient-history/unavailable based on positioned historical evidence. No intent or causality is inferred.

### Evidence Chain
Assessments are connected to supporting evidence via links containing claim, component, source, evidence type, snippet, and verification state (verified, unverified, or stale). Unverifiable references are preserved as unverified, never fabricated.

### Provenance & Unavailable vs Zero
Evidence references preserve source information where available. A metric being unavailable is displayed as *Unavailable* and is never treated as zero.

## Reports & Exports

Supported outputs:

- **Exports** — JSON, CSV, XLSX, PDF, DOCX per analysis, including available intelligence, evidence, and provenance
- **Reports** — Scheduled reports (JSON/CSV/HTML) with bounded history and V13 context

Exports and reports are generated from the same bounded intelligence context used by the UI.

## Dashboard & User Interface

- **Dashboard** — KPI overview, risk and authenticity intelligence, threat and narrative summaries, trend visualizations (Chart.js), entities and channel intelligence, recent analyses, and infrastructure status
- **Analysis Result** — Header with source metadata, overall assessment, score cards, intelligence navigation, detailed findings, evidence, comments with transcript relevance, media/transcript details, and export/delete actions
- **Operational Pages** — History, reports, jobs, notifications, activity, schedules, and trends with responsive tables, pagination, and empty states
- **Auth/Profile** — Login, registration, forgot/reset password, profile with password change and clearly isolated danger zone for account deletion
- **Design** — Dark interface with centralized design tokens, reusable card/badge/table/form components, responsive grid, and Bootstrap 5 foundation. No React or heavy frontend framework.

## Technology Stack

**Backend:** Python 3.14, Flask 3.1, Flask-SQLAlchemy 3, Flask-Migrate/Alembic, Flask-WTF, Flask-Login, Werkzeug, WTForms, python-dotenv, Gunicorn

**Database:** PostgreSQL (production) / SQLite (tests and development), SQLAlchemy 2

**Frontend:** Jinja2, Bootstrap 5.3.3, Bootstrap Icons 1.11.3, Font Awesome 6.5.1, Chart.js 4.4.4, custom CSS (`design-system.css`, `components.css`, `style.css`), minimal JavaScript

**AI/ML/NLP:** NLTK, scikit-learn, pandas, NumPy (heuristic engines; no LLM or vector database)

**APIs / Integrations:** YouTube Data API v3, Reddit OAuth API (optional), `google-api-python-client`, `youtube-transcript-api`, `Pillow`/`requests` (thumbnail handling, optional)

**Infrastructure:** Redis, Celery with Beat, Flower (optional), Docker & Docker Compose

**Testing:** pytest, pytest-flask

## Architecture

```
                ┌──────────────────────┐
                │   Web Interface      │
                │ Flask + Jinja + JS   │
                └──────────┬───────────┘
                           │
                           ▼
                ┌──────────────────────┐
                │     Flask Routes     │
                │  (auth, dashboard,   │
                │  analysis, export,   │
                │  report, trend, job) │
                └──────────┬───────────┘
                           │
                           ▼
                ┌──────────────────────┐
                │      Services        │
                │ Intelligence Logic   │
                │ (content, auth,      │
                │  historical, V13)    │
                └──────────┬───────────┘
                           │
                           ▼
                ┌──────────────────────┐
                │    Repositories      │
                │ Data Access Layer    │
                └──────────┬───────────┘
                           │
                           ▼
                ┌──────────────────────┐
                │ PostgreSQL / SQLite  │
                └──────────────────────┘
```

Intelligence is layered: content → authenticity → threat/narrative → historical/explainable, each consuming only available evidence from prior layers.

## Data Sources & Integrations

**Primary / Active:** YouTube Data API v3 for video metadata, comments, and transcripts. Requires `YOUTUBE_API_KEY` for live data; demo mode with realistic simulated data is used otherwise.

**Optional:** Reddit OAuth for post and comment ingestion (`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`). When not configured, Reddit analysis uses demo data and never blocks the application.

No other social platforms, vector databases, or LLM APIs are required.

## Security

- Authentication with Flask-Login, password hashing (Werkzeug), password policy, and `is_safe_url` next-parameter validation
- Authorization via `users.role` (`user`/`admin`) and `admin_required` on all `/admin/*` routes; sole-admin deletion is blocked
- Secure session cookies (`SESSION_COOKIE_SECURE` in production, `SECRET_KEY` required), CSRF protection on all POST forms, `HttpOnly`/`SameSite` settings
- Login and password-reset rate limiting via Redis (degrade-open when Redis is unavailable, bypassed in tests)
- Signed, timed password-reset tokens bound to the current password hash (single-use via hash rotation)
- Ownership checks on every analysis, job, export, and report; cross-user access returns not-found/forbidden without revealing data
- Account and per-analysis deletion are POST-only, require password (and username confirmation for account), leaf-first FK-safe order, `UPLOAD_FOLDER` containment for file cleanup, and post-commit filesystem handling with logout

## Performance Considerations

The application is designed to remain practical on constrained development hardware and growing datasets:

- Bounded history windows and row caps; SQL-level `LIMIT/OFFSET` pagination (never `all()` then slice)
- Batched `IN` queries for exports and comment contexts; database-side `AVG`/`COUNT`/`COUNT(DISTINCT)` for aggregates
- V13 request-local prefetch and deduplication; batched evidence verification; no per-row service calls from templates
- Bounded report and cleanup batches; stuck-job age thresholds

No Redis caching, Elasticsearch, Spark, vector DB, or microservices are required.

## Installation

```bash
git clone https://github.com/anomalyco/socialsense-ai.git
cd socialsense-ai
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # edit secrets and optional API keys
```

## Configuration

Environment is configured via `.env` (see `.env.example` for the full reference). Important groups:

- `SECRET_KEY` (required in production), `FLASK_ENV`, `SESSION_COOKIE_SECURE`
- `DATABASE_URL`, `POSTGRES_*`, `REDIS_URL`, `CELERY_*`, `USE_CELERY`
- `YOUTUBE_API_KEY`, `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` (both optional)
- Feature flags (e.g., `ENABLE_*`) and bounded limits (e.g., `V13_MAX_HISTORY_ANALYSES`, `REPORT_MAX_DUE_BATCH`)
- Job and retention settings (`MAX_CONCURRENT_JOBS`, `JOB_*_RETENTION_DAYS`)

Do not commit real secrets. Keep `.env` untracked.

## Database Setup

```bash
flask db current   # expect v14_001 (head), single head
flask db upgrade   # apply pending migrations
```

Migrations are hermetic in tests (subprocess-isolated SQLite) and never run destructively against the live database.

## Running the Application

```bash
flask run --host 0.0.0.0 --port 5000
# or
python app.py
# production
gunicorn "app:create_app()" --bind 0.0.0.0:5000
# with Docker
docker-compose up --build
```

Visit `http://localhost:5000`.

## Testing

```bash
pytest -q
pytest tests/test_auth.py tests/test_v14_phase_b.py -q
pytest tests/test_v12_dashboard.py tests/test_v12_result.py -q
```

The suite contains ~1250 tests across 58 files (`find tests -name "test_*.py" | wc -l` and `grep -r "def test_" | wc -l`). Prefer `pytest -q` over a hard-coded count.

Known pre-existing intermittent hang: `tests/test_services.py::test_analysis_service_with_different_limits` — deselect with `--deselect` if needed; do not hide new failures behind it.

## Project Structure

```
socialsense-ai/
├── app.py
├── config/          # settings, env handling
├── models/          # 27 tables (users, analyses, comments, narratives, threat, historical, jobs, etc.)
├── repositories/    # 17 repos (analysis, comment_result, narrative, temporal, threat, user, job, etc.)
├── services/        # 57 services (analysis, youtube/reddit, transcript, entity, channel, authenticity, narrative, coordination, propagation, temporal, threat, historical, export, report, etc.)
├── routes/          # 14 blueprints (auth, dashboard, analysis, export, job, report, trend, notification, activity, admin, health)
├── templates/       # 27 templates (base, dashboard, analysis/result/history, reports, jobs, notifications, activity, auth/*, admin/*)
├── static/          # css/design-system.css, components.css, style.css + js/main.js, Chart.js via CDN
├── migrations/      # 12 versions (v6_001 ... v14_001, head v14_001)
├── tests/           # 58 files
├── requirements.txt
├── .env.example
└── README.md
```

## Limitations

- Heuristic/analytical outputs are not proof of synthetic media, threats, or coordination; they are indicators with confidence, coverage, and limitations.
- No causal inference is made; historical comparisons are descriptive and associative.
- Unavailable data is distinct from zero and is shown as *Unavailable* or *insufficient history*.
- Bounded windows mean older history beyond the configured limit is not considered in baselines.
- Missing or blocked external APIs (e.g., YouTube IP blocks, missing transcript languages) produce graceful *Unavailable* states.
- Reports are file + in-app notification only; no email/push delivery.

## Future Scope

Potential future work includes (not committed):

**Intelligence** — stronger multilingual NLP, improved entity resolution and contextual sentiment, richer temporal intelligence, more advanced narrative analysis, improved multimodal authenticity.

**Data Sources** — additional social platforms or public datasets, richer external datasets.

**Explainability** — deeper evidence visualization, richer provenance, improved analyst-facing explanations.

**Analytics** — long-term trend analysis, channel/entity-specific baselines, advanced comparative analytics.

**Platform** — richer reporting, improved notification/delivery channels, additional deployment/scaling options.

**Engineering** — stronger observability, expanded integration testing, performance improvements for larger datasets.

## Version History

| Milestone | Focus |
|---|---|
| V1–V10 | Core platform development |
| V11 | Authenticity intelligence |
| V12 | Threat and narrative intelligence |
| V13 | Historical context and explainable intelligence |
| V14 | Security, account lifecycle and performance hardening |
| V15 | UI/UX modernization and release readiness |

The README describes the current product; development chronology is summarized here only.

## Security Notes

See **Security** above. Keep `SECRET_KEY` out of version control, use HTTPS in production (`SESSION_COOKIE_SECURE=true`), and rely on the existing rate limiting and ownership checks. Report security issues privately.

## Development Notes

- Python 3.14, `venv`, `pip install -r requirements.txt`, `flask db upgrade`, `flask run`
- Keep bounded-query conventions (`LIMIT/OFFSET` before `all()`, `COUNT()` not `len(all())`, batched `IN`)
- Keep V15 tokens (`--ss-*`) and `ss-card` patterns; avoid hard-coded colors or per-row DB calls from templates
- Keep `Unavailable` distinct from zero and preserve non-causal wording

## License

MIT (add `LICENSE` file if missing).
