# SocialSense AI

**AI-Powered Multimodal Social Media Content Intelligence Platform**

SocialSense AI analyzes YouTube videos and Reddit posts for spam, toxicity, sentiment, bot-like behavior, entity and channel context, transcripts, and — via V11–V13 — authenticity, threat/narrative, and historical/explainable intelligence. Results are heuristic, explainable, and bounded by design.

## Overview

SocialSense AI ingests YouTube (Data API v3) and Reddit (OAuth, optional) content, stores comments and transcripts, and runs a staged intelligence pipeline:

```
YouTube / Reddit → Comments + Transcript → Entity/Channel/Transcript Intelligence → V11 Authenticity → V12 Threat & Narrative → V13 Historical/Explainable → Exports/Reports
```

All intelligence after V4 is heuristic/analytical, not a production threat-detection system. Unavailable data is shown as *Unavailable*, never as zero. Historical work uses real timestamps/evidence only; no causal claims are made.

## Key Capabilities

- **Multi-Platform** — YouTube (primary, live) and Reddit (live when credentials present, otherwise realistic demo data; never required)
- **Core Analysis** — sentiment, toxicity, spam, bot/AI-like, duplicate, risk scoring, transcript relevance, entity/channel context
- **Transcripts** — YouTube transcript fetch with language fallbacks, demo/fallback generation, segment storage
- **Authenticity (V11)** — media authenticity indicators (heuristic, see V11)
- **Threat & Narrative (V12)** — threat assessment, narrative recurrence, coordination, propagation, temporal growth
- **Historical & Explainable (V13)** — bounded baselines, cross-analysis, narrative evolution, evidence chains
- **Security & Lifecycle (V14)** — roles, rate limiting, password lifecycle, deletion, bounded performance
- **Modern UI (V15)** — dark SaaS design system, global shell, dashboard and analysis result redesign
- **Exports/Reports** — CSV, JSON, XLSX, PDF, DOCX + scheduled reports (JSON/CSV/HTML)

## Intelligence Pipeline

```
Browser → Flask Routes → Services → Repositories → SQLAlchemy → PostgreSQL (SQLite for tests)
                ↓
        V11 Authenticity (thumbnail/audio/frame/metadata → MediaAnalysis)
        V12 Threat & Narrative (narrative/coordination/propagation/temporal/threat → 5 tables)
        V13 Historical (baselines/cross-analysis/evolution + evidence_refs on threat_assessments)
        V14 Security/Performance (roles, rate limiting, deletion, bounded queries)
        V15 UI (design-system + shell + page redesigns)
```

## V11 — Authenticity Intelligence

Heuristic, explainable media assessment persisted as one `media_analyses` row per analysis (`v11_001`).

- **Components (weights)**: Thumbnail 25% (Pillow pixel heuristics or demo CRC32), Audio 25% (transcript-based text inference only, never waveform, `analysis_mode: transcript`), Frame 30% (gracefully unavailable when no frames; Pillow pipeline exists internally), Metadata 20% (title/description/camera/timestamp/aspect).
- **Outputs**: `overall_ai_probability`, `overall_authenticity_score = 100 - ai`, `confidence` (coverage + agreement), `deepfake_score` (frame), `synthetic_voice_score` (audio), `reasons` (JSON list), `summary` with disclaimer. 60+ flags risk buckets.
- **UI**: Dashboard Authenticity section (AI Videos, Authentic Videos, Deepfake, Voice Clones, Avg Authenticity), Result Authenticity card, CSV/JSON `media_analysis`, scheduled report aggregates, progress stages 98–99%.
- **Limitation**: Probabilities, not proof; audio is text inference; no video download; thumbnails may be unavailable; demo clearly labeled.

## V12 — Threat & Narrative Intelligence

Heuristic/analytical layer on 5 tables (`v12_001`):

| Table | Purpose |
|---|---|
| `narratives` | Recurring themes per user (normalized name, risk, confidence, growth) |
| `narrative_occurrences` | Narrative ↔ analysis links (relevance, risk, evidence) |
| `coordination_signals` | Potential coordinated-behavior signals per analysis |
| `propagation_events` | Observed relationships between analyses |
| `threat_assessments` | Consolidated per-analysis assessment (1:1, renormalized weighted components) |

- **Threat**: 6 components (authenticity, coordination, narrative, propagation, temporal, entity) renormalized over available signals; `NULL` = unavailable (excluded, not zero); `confidence`, `evidence_coverage`, `reasons/indicators/limitations`, `heuristic_weighted` + non-causal disclaimer.
- **Narrative/Coordination/Propagation/Temporal**: bounded (e.g., `MAX_NARRATIVES_PER_ANALYSIS 12`, `COORDINATION_COMPARISON_BUDGET 2000`, `PROPAGATION_MAX_EVENTS 25`, `TEMPORAL_MAX_NARRATIVES 50`), `ondelete` FK rules, JSON evidence with snippet bounds.
- **UI**: `v12_context_service` single source of truth for result + exports; dashboard V12 sections and result intelligence cards with `Unavailable` handling.

## V13 — Historical Context & Explainable Intelligence

Major missing-section prior to this README; now implemented on `v13_001` (`threat_assessments.evidence_refs` nullable JSON).

### Historical Context
- Bounded per-user history: `V13_MAX_HISTORY_ANALYSES 20` (cap 500) inside `V13_HISTORY_WINDOW_DAYS 90` (`isnot(None)` + `>= since`, deterministic `created_at DESC, id DESC`), `V13_MIN_HISTORY_SAMPLE 3`. Scope is per-user only.

### Cross-Analysis Comparison
- Current vs bounded history via `TemporalRepository.get_user_analyses_in_window` + `HistoricalContextService.get_metric_series` (shared, no duplication); provenance-tagged `history` points and `current` refs.

### Narrative Evolution
- Deterministic states from positioned `occurred_at` only (NULL never fabricated): `emerging` (no prior), `reappearing` (gap, only prior half), `declining` (recent rate < 0.5× prior), `persistent` otherwise, `insufficient_history`/`unavailable` when unpositionable.

### Explainability / Evidence Chain
- `V13EvidenceChainService` persists `{claim, component, source_table, source_id, ref, snippet, score, evidence_type, verified}` links on `threat_assessments.evidence_refs` (max 25, snippet 160, deterministic ordering). Every `source_id` verified against DB; unverifiable refs kept with `verified: false`; read path `_reverify_batch` checks via batched `IN` queries. No invented IDs/snippets/timestamps.

### UI / Exports / Reports
- Integrated via `v13_context_service.build_v13_context()` (request-local prefetch, no cross-request cache): result UI (Historical Context table, Cross-Analysis table, Narrative Evolution cards, Evidence accordion with Verified/Unverified), JSON (`v13: {historical_context, cross_analysis, narrative_evolution, evidence}`), CSV/XLSX/PDF/DOCX via `_all_sections` and `_write_v13_csv`.

## V14 — Security, Account Lifecycle & Performance

### Authentication & Authorization
- `users.role` (`user`/`admin`, `v14_001`, server_default `user`, `is_admin` property, never from client input); `admin_required` on all 6 `/admin/*` routes; 403 page.
- Session: `SESSION_COOKIE_HTTPONLY`, `SAMESITE Lax`, `SESSION_COOKIE_SECURE` + `REMEMBER_COOKIE_SECURE` via env (production `ProductionConfig` forces Secure and requires `SECRET_KEY`, else `RuntimeError`); `safe_redirect_target` / `is_safe_url` prevents open redirects.
- Login rate limiting: `RedisService.rate_limit()` (sliding-window sorted set, unique members) on `login_attempts:<email>` and `password_reset:<email>` separately, 10/300s and 5/3600s, Redis-optional (degrade-open with warning), `TESTING` bypass for suite stability.

### Password Security
- `werkzeug` `pbkdf2` hashing, policy ≥8 chars + upper/lower/digit, reuse prevention, `logout_user()` after change/reset, signed `itsdangerous` timed tokens (`PASSWORD_RESET_TOKEN_MAX_AGE 3600`, `sha256(password_hash)` binding, single-use via hash rotation), uniform forgot-password response (no enumeration), dev-only token exposure (`debug or TESTING`), rate-limited reset.

### Account Lifecycle
- **Per-analysis deletion** (`POST /analysis/<id>/delete`, `current_user` only, RUNNING/PENDING guard via `result_analysis_id`, leaf-first FK-safe order, `UPLOAD_FOLDER` containment, missing-file tolerant, no unscoped DELETE).
- **Account deletion** (`POST /auth/account/delete`, password + exact username, sole-admin guard via `User.role`, active jobs cancelled/failed, leaf-first purge of all 14 user-owned tables + analysis subtrees + scheduled reports/files, post-commit filesystem cleanup, `logout_user()`, no admin-driven arbitrary deletion, no soft-delete.

### Performance (V14-D1–D7)
- **D1 Dashboard/History**: `get_by_user_id(limit, offset)` SQL caps + `TrendService`-style `GROUP BY` aggregates (8 fixed queries regardless of history size vs ~7*N before).
- **D2 Export**: batched `IN` comment maps (3 queries, chunk 500) + `include_comments=False` for XLSX/PDF/DOCX; ~99% fewer queries at 500 comments.
- **D3 Reports**: entity/authenticity aggregates (`GROUP BY` + `COUNT`/`AVG`), `REPORT_MAX_DUE_BATCH 20` oldest-first.
- **D4 V13 dedup**: request-local `build_prefetch` (threat/media/series once) + evidence `_reverify_batch` (≤4 IN queries vs per-link).
- **D5 Aggregates**: `get_comment_metric_means` (`AVG`/`COUNT` per analysis) + `get_cross_platform_narrative_count` (`COUNT(DISTINCT platform) HAVING >1`).
- **D6 Template/pagination**: `comment_contexts` batch (no `c.context` N+1), SQL-level pagination on history/notifications/activity/reports/schedules/job logs.
- **D7 Cleanup**: `COUNT()` not `len(all())`, stuck-job age threshold (`2*MAX_JOB_RUNTIME`), bounded `delete_old_*` batches, `days=0` clamped to 30.

## V15 — UI/UX Modernization

Staying on `Flask + Jinja2 + Bootstrap 5.3.3 + Chart.js + custom CSS` (no React).

### Design System (V15-A)
`static/css/design-system.css` + `static/css/components.css` centralize tokens:
`--ss-bg #0f1115`, `--ss-surface #171a21`, `--ss-surface-raised #1e232e`, `--ss-border #262b36`, `--ss-text #e9ecef`, `--ss-text-muted #8a9099`, `--ss-primary #4f6ef7`, `--ss-success #1a9e6a`, `--ss-warning #e6a817`, `--ss-danger #e5484d`, `--ss-info #0ea5e9`, `--ss-risk-*`, `--ss-threat #7c3aed`, `--ss-authenticity #06b6d4`, `--ss-font-sans/mono` (Inter/JetBrains Mono fallbacks), spacing 4–48, radius 6–16/pill, shadows `0 1px 2px`/`0 4px 12px`, focus ring `0 0 0 3px rgba(79,110,247,0.4)`. Reusable `ss-card`, `ss-kpi`, `ss-badge--risk-*`, table/form/alert/pagination/nav foundations; `prefers-reduced-motion` respected.

### Global Shell (V15-B)
`templates/base.html`: `ss-navbar` (`ss-surface`, `ss-border`), `ss-shell` max 1280, `ss-brand-icon`, `More` dropdown (Reports/Jobs/Schedules/Activity), notification `ss-icon-btn`, user menu with admin section (only `is_admin`), `active` underline + `aria-current`, `ss-page-header` + `ss-breadcrumb` blocks, `ss-footer`, `skip link` + `main#main-content`, CDN Bootstrap/Icons/FA/Chart.js.

### Dashboard (V15-C)
`templates/dashboard/dashboard.html`: page header + platform pills, 4 `ss-kpi` (Total Analyses, Comments Processed, Avg Risk + health, Critical), Risk Overview + Authenticity 6-up, V12 disclaimer, Threat (level/score/confidence/coverage + 6 components), 2×2 Narratives/Coordination/Propagation/Temporal (bounded, `Unavailable` preserved), Entities/Channel, Infra (counts-only), 4 pie/bar + 2 doughnut charts (dark, legend `#8a9099`), Recent Analyses table, `View All`.

### Analysis Result (V15-D)
`templates/analysis/result.html` (sticky `Overview/Risk/Threat/Authenticity/Historical/Evidence/Comments` nav): header (platform/Demo/Live, ID/date, title/subtitle, Export dropdown + History + Delete), Overall Risk elevated card, Primary Scores (6), Risk/Threat/Authenticity/Historical/Cross-Analysis/Evolution/Evidence (accordion, 25 links max, Verified/Unverified, stale handling, non-causal disclaimers), Detailed Findings (Narratives/Coordination/Propagation/Temporal), Distributions + Top Spam/Toxic + Suspicious, Comments (batched `comment_contexts`, `table-responsive`), Media/Transcript, Channel Context, Limitations, Recommendations, export/delete preserved, no per-comment queries, presentation-only.

### Operational Pages (V15-E)
`history`, `reports`, `jobs`, `notifications` redesigned with `ss-page-header` + breadcrumb, `ss-card` headers with counts, `table-responsive` or `list-group`, status `badge` semantics, `table` `scope="col"`, `btn-group` with `aria-label`/`csrf_token`/`confirm()`, empty states (`ss-card--elevated` + CTA), pagination `Previous / Page X of Y / Next`.

### Auth/Profile (V15-F)
Centered `ss-card--elevated` (48px brand icon): `login` (Sign In, forgot link inline, Remember me, Create Account), `register` (4 fields, help text), `forgot` (anti-enumeration), `reset` (2 fields, `minlength 8`), `profile` 2-col (account card + 4 read-only fields + Recent Analyses list, `Change Password` card (3 fields, cancel), `Danger Zone` (`border ss-danger`, alert, password + username confirm, `confirm()`)). All POST forms have `csrf_token`.

### Responsive & Accessibility (V15-G)
`table-responsive -webkit-overflow-scrolling`, `overflow-wrap:anywhere` on `td`, `section[id] scroll-margin-top 80px`, `ss-icon-btn`/`page-link` min 36px, `skip link`, `main` landmark, `th scope`, icon `aria-label`, `focus-visible` ring retained, status via text+ badge, no color-only meaning.

## Architecture

```
Browser
  ↓
Flask Routes (auth, dashboard, analysis, export, report, trend, job, notification, admin, health)
  ↓
Services (analysis, youtube/reddit, transcript, entity, channel, authenticity, narrative, coordination, propagation, temporal, threat, historical, v13_context, export, report, job, notification, activity, scheduler, health)
  ↓
Repositories (analysis, comment_result, entity, channel, narrative, propagation, temporal, threat, user, job, scheduled_*, notification, activity, worker_health)
  ↓
SQLAlchemy
  ↓
PostgreSQL (production) / SQLite (tests)
```

V11 `media_analyses`, V12 5 tables, V13 `evidence_refs`, V14 `users.role`.

## Technology Stack

**Backend**: Python 3.14, Flask 3.1, Flask-SQLAlchemy 3, Flask-Migrate/Alembic, Flask-WTF, Flask-Login, Werkzeug, python-dotenv, WTForms

**Database**: PostgreSQL + `psycopg2-binary`, SQLAlchemy 2, SQLite for tests

**Frontend**: Jinja2, Bootstrap 5.3.3, Bootstrap Icons 1.11.3, Font Awesome 6.5.1, Chart.js 4.4.4, custom `design-system.css`/`components.css`/`style.css`, minimal `main.js` (tooltips, alert auto-hide, smooth scroll, sticky nav observer)

**APIs / Sources**: YouTube Data API v3 (optional), Reddit OAuth (optional, demo fallback), `google-api-python-client`, `youtube-transcript-api`, `Pillow`/`requests` (thumbnail, optional)

**AI/ML/NLP**: `nltk`, `scikit-learn`, `pandas`, `numpy` (heuristic engines; no LLM/vector DB)

**Infra**: `redis`, `celery`/`flower`, `gunicorn`, `openpyxl`, `reportlab`, `python-docx`

**Testing**: `pytest`, `pytest-flask`

## Data Sources / Integrations

- **YouTube** — primary live source (`YOUTUBE_API_KEY` optional; demo mode when missing)
- **Reddit** — optional live source (`REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` optional; demo posts/comments when missing; never blocks the app)
- No other social platforms, no vector DB, no LLM.

## Installation

```bash
git clone https://github.com/anomalyco/socialsense-ai.git
cd socialsense-ai
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # edit secrets
flask db upgrade
flask run  # or python app.py
```

Docker (production-like):

```bash
docker-compose up --build  # web + db + redis + celery-worker + celery-beat
```

## Configuration

See `.env.example` for all keys (safe placeholders). Important groups:

- **Flask**: `SECRET_KEY` (required in production, `dev-secret-key…` fallback only for dev), `FLASK_ENV`
- **Session**: `SESSION_COOKIE_SECURE`, `REMEMBER_COOKIE_SECURE` (production `true`)
- **Database**: `DATABASE_URL`, `POSTGRES_*`, `DATABASE_POOL_*`, `DATABASE_ECHO`
- **Redis/Celery**: `REDIS_URL`, `CELERY_*`, `CELERY_WORKER_CONCURRENCY`, `USE_CELERY`
- **YouTube/Reddit**: `YOUTUBE_API_KEY`, `REDDIT_CLIENT_ID/SECRET/USER_AGENT`
- **Jobs**: `MAX_CONCURRENT_JOBS`, `MAX_JOBS_PER_USER`, `MAX_JOB_RUNTIME`, `MAX_JOB_RETRIES`, `JOB_*_RETENTION_DAYS`, `REPORT_MAX_DUE_BATCH`
- **Transcripts/Entities/Channel**: `ENABLE_*`, `TRANSCRIPT_*`, `MAX_ENTITIES_PER_ANALYSIS`, `MAX_HISTORY_VIDEOS`, etc.
- **V11–V13**: all `ENABLE_*` and `*_MAX_*` bounds (e.g., `V13_MAX_HISTORY_ANALYSES 20`, `V13_HISTORY_WINDOW_DAYS 90`)
- **V14**: `LOGIN_RATE_LIMIT_*`, `PASSWORD_RESET_*`

## Database Setup

```bash
flask db current  # should be v14_001 (head), single head
flask db heads    # v14_001
flask db upgrade  # apply pending
# Hermetic migration tests use subprocess-isolated SQLite; never run destructive downgrades against live PG
```

## Running the Application

```bash
flask run --host 0.0.0.0 --port 5000
# or
gunicorn "app:create_app()" --bind 0.0.0.0:5000
```

Visit `http://localhost:5000`.

## Testing

```bash
pytest -q
pytest tests/test_auth.py tests/test_v14_phase_b.py -q
pytest tests/test_v12_dashboard.py tests/test_v12_result.py -q
pytest tests/test_v13_phase_b.py tests/test_v14_phase_d6.py -q
```

Current suite: `find tests -name "test_*.py" | wc -l` → 58 files, `grep -r "def test_" | wc -l` → ~1250 tests. Prefer `pytest -q` over a hard-coded count.

Known pre-existing intermittent hang: `tests/test_services.py::test_analysis_service_with_different_limits` (outside V15, not hidden if it reappears; `pytest --deselect` it if needed).

## Project Structure

```
app.py / config/ / database/ / celery_app.py / celery_tasks.py
models/          # 27 tables (users ... worker_health)
repositories/    # 17 repos (analysis, comment_result, narrative, propagation, temporal, threat, user, job, scheduled_*, notification, activity, worker_health)
services/        # 57 services (analysis, youtube/reddit, transcript, entity, channel, authenticity, narrative, coordination, propagation, temporal, threat, historical, export, report, job, notification, trend, health, v13/*, v14/*)
routes/          # 14 blueprints (auth, dashboard, analysis, export, job, report, schedule, trend, notification, activity, admin, health, monitoring)
templates/       # 27 templates (base, dashboard, analysis/result/history, reports, jobs, notifications, activity, schedules, trends, auth/*, admin/*, errors)
static/css/      # style.css + design-system.css + components.css
static/js/       # main.js
migrations/      # 12 versions (v6_001 ... v14_001)
tests/           # 58 files
```

## Version History

| Version | Focus |
|---|---|
| V1 | Initial analysis scaffolding |
| V2 | YouTube integration |
| V4 | AI Analysis Engine (sentiment, toxicity, spam, bot, risk, summary) |
| V5 | Background jobs (thread + Celery, progress, cancel/retry) |
| V6 | Monitoring & scheduling (health, scheduler, reports) |
| V7 | Transcript intelligence (fetch, language fallback, segments, fallback generation) |
| V8 | Entity intelligence (extraction, resolution, sentiment, risk, history) |
| V9 | Channel context intelligence (channel stats, video/topic history, comparison) |
| V10 | PostgreSQL & Redis infrastructure (migrations, pooling, Celery/Beat, Docker) |
| V11 | Authenticity Intelligence (thumbnail/audio/frame/metadata → MediaAnalysis, heuristic) |
| V12 | Threat & Narrative Intelligence (5 V12 tables, threat assessment, narrative/coordination/propagation/temporal) |
| V13 | Historical Context & Explainable Intelligence (bounded baselines, cross-analysis, evolution, evidence_refs) |
| V14 | Security, Account Lifecycle & Performance Hardening (roles, rate limiting, password lifecycle, deletion, D1–D7 bounded queries) |
| V15 | UI/UX Modernization & Release Readiness (design system, global shell, dashboard/result/history/reports/jobs/notifications/auth/profile, responsive/a11y, polish) |

V1–V10 rows use the existing README/history as source; no fabricated detail beyond what the repository already documented.

## Limitations

- All intelligence after V4 (authenticity, threat, narrative, historical) is **heuristic/analytical** and carries explicit non-causal disclaimers; `Unavailable` is never conflated with zero.
- **Authenticity** outputs are probabilities/indicators, not proof of synthetic media; audio is transcript-based text inference, not waveform analysis; frame analysis is gracefully unavailable without decoded frames.
- **Historical** work is per-user, bounded (`V13_MAX_HISTORY_ANALYSES 20` / `90` days), and uses real timestamps/evidence only; deviation labels are associative, not causal.
- **External APIs** may require credentials; missing `YOUTUBE_API_KEY` / `REDDIT_*` falls back to demo data; IP blocks from YouTube may require `youtube-transcript-api` workarounds.
- **Reports** are JSON/CSV/HTML file + in-app notification; no email/push delivery.

## Security Notes

- `admin_required` on all `/admin/*`; `user.is_admin` from `users.role` (never client input); sole-admin deletion blocked.
- `SESSION_COOKIE_SECURE`/`REMEMBER_COOKIE_SECURE` via env, production enforces `SECRET_KEY`.
- Login/reset rate limiting via `RedisService.rate_limit()` (sorted set, windowed, degrade-open; `TESTING` bypass).
- Safe `next` redirection (`is_safe_url`).
- All destructive forms have `csrf_token()`; account/analysis deletion are POST-only with confirmations.

## Development Notes

- Python 3.14, `venv/`, `pip install -r requirements.txt`, `flask db upgrade`, `flask run`.
- Keep `V14` bounded-query conventions (`LIMIT/OFFSET` before `all()`, `COUNT()` not `len(all())`, batch `IN` with chunking, `COUNT(DISTINCT)` for cross-platform).
- Keep `V15` tokens (`--ss-*`) and `ss-card`/`ss-page-header` patterns; do not reintroduce hard-coded colors or per-row DB calls from templates.

## License

MIT (if applicable; add `LICENSE` file if missing).
