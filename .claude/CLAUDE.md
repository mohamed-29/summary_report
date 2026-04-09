# iVend Summary Report - Ops Dashboard

## Project Overview
Django-based operations dashboard for managing iVend vending machine logistics. Tracks operator visits, machine health, car/driver routes, and generates AI-powered monthly summaries. Full bilingual support (English/Arabic) with RTL layout.

**Project path**: `C:\Users\Bashar\Desktop\ivend\summary_report`
**Django project**: `ops_dashboard` | **App**: `logistics`

## Tech Stack
- **Framework**: Django 6.0.2, SQLite (dev) / PostgreSQL (prod-ready)
- **Python deps**: pandas, openpyxl (Excel), rapidfuzz (fuzzy matching), openai (OpenRouter API), Pillow (images), django-import-export, python-decouple
- **Frontend**: Server-rendered Django templates, Chart.js 4 (CDN), custom dark-theme CSS
- **Tunnel**: Cloudflare Trycloudflare for external access
- **Config**: `.env` file (SECRET_KEY, OPENROUTER_API_KEY, DEBUG)

## Project Structure
```
summary_report/
  manage.py
  ops_dashboard/              # Django project config
    settings.py               # TEMPLATES context_processors includes language_context
    urls.py
  logistics/                  # Main app - ALL logic here
    models.py                 # 14 models (see below)
    views.py                  # ~1800 lines, all view functions
    urls.py                   # All URL patterns under app_name='logistics'
    admin.py
    forms.py
    utils.py                  # auto_checkout_stale_visits, etc.
    translations.py           # Bilingual dict: TRANSLATIONS['en'] & TRANSLATIONS['ar'] (282 keys)
    context_processors.py     # Injects t, lang, is_rtl into all templates
    management/commands/
      ingest_logs.py          # Excel import CLI
    templatetags/
      form_tags.py
    templates/logistics/      # 14 HTML templates (all bilingual)
      base.html               # Base layout: dark theme, Chart.js CDN, sidebar nav, RTL support
      dashboard.html           # Monthly machine stats, charts, AI summaries, CSV upload
      machine_detail.html      # Per-machine visit logs and trend chart
      operator_detail.html     # Per-operator stats, ratings, visit history, CSV download
      operator_list.html       # All operators with monthly stats and filters
      daily_machine_summary.html # Daily view: all machines, operator/car visits, photos
      visit_form.html          # Operator check-in/check-out form (standalone, mobile)
      car_log_form.html        # Driver trip log form (standalone, mobile)
      operator_login.html      # Code-based operator login (standalone)
      dashboard_login.html     # Admin login (standalone)
      supervisor_login.html    # Supervisor code login
      supervisor_dashboard.html # Supervisor oversight: attendance, operator reviews
      supervisor_daily_form.html # Supervisor daily report form
      supervisor_history.html   # Past supervisor reports
  media/                      # Uploaded images
    visit_log_images/
    car_log_images/
    supervisor_images/
  static/
  db.sqlite3
  .claude/CLAUDE.md           # This file
```

## Models (logistics/models.py)
| Model | Purpose |
|-------|---------|
| **Operator** | name, code (auto 6-char), is_driver, is_active |
| **Machine** | name, code, location, lat/lon GPS, is_active |
| **MachineAlias** | Maps typos to canonical Machine (source: manual/fuzzy/ai/exact) |
| **VisitLog** | Core record: operator + machine + timestamp + transactions/voids + checklist booleans + ratings + issues + comments. is_check_in, is_completed for draft workflow |
| **VisitLogImage** | Photos attached to visit logs |
| **CarLog** | Driver trip: driver + trip_date + checklist + issues + exit/return times |
| **CarLogImage** | Photos for car logs |
| **CarLogStop** | Ordered machine stops within a trip |
| **MonthlyReport** | Cached AI summaries per machine per month |
| **SupervisorProfile** | Links Django User to supervisor role |
| **SupervisorDailyReport** | Daily supervisor report with location, issues, comments |
| **SupervisorOperatorReview** | Per-operator review within supervisor report (attended, rating, location, comments) |
| **SupervisorReportImage** | Images in supervisor reports |
| **OperatorDailyRating** | Manual 0-10 daily ratings for operators |

## URL Patterns (logistics/urls.py)
All routes under `app_name='logistics'`:
```
/                           dashboard (main monthly view)
/set-language/              set_language (toggle EN/AR via ?lang=en|ar)
/machine/<id>/              machine_detail
/generate-summaries/        generate_summaries (AI via OpenRouter)
/upload/onsite/             upload_onsite_logs (Excel)
/upload/car/                upload_car_logs (Excel)
/operator/<id>/             operator_detail
/operators/                 operator_list
/visit-log/<id>/download/   download_visit_log (CSV, ?lang=ar for Arabic)
/daily-summary/             daily_machine_summary
/form/login/                operator_login
/form/                      visit_log_form (check-in/check-out)
/form/auto-save/            visit_auto_save (AJAX draft save)
/form/car/                  car_log_form
/form/logout/               operator_logout
/auth/login/                dashboard_login_view
/auth/logout/               dashboard_logout_view
/supervisor/login/          supervisor_login
/supervisor/                supervisor_dashboard
/supervisor/form/           supervisor_daily_form
/supervisor/history/        supervisor_report_history
/supervisor/logout/         supervisor_logout
```

## Authentication
- **Admin dashboard**: Django auth (username/password) via `dashboard_login_view`
- **Operators**: Code-based login, stored in `request.session['operator_id']`
- **Supervisors**: Code-based login, stored in `request.session['supervisor_id']`

## Bilingual System (i18n)
**NOT using Django's built-in gettext.** Uses a custom session-based approach:

### How it works
1. **`logistics/translations.py`** — Single Python dict `TRANSLATIONS` with `'en'` and `'ar'` sub-dicts, 282 keys each
2. **`logistics/context_processors.py`** — Registered in `settings.py TEMPLATES`, injects into every template:
   - `t` — the translation dict for current language (e.g., `{{ t.dashboard_title }}`)
   - `lang` — current language code (`'en'` or `'ar'`)
   - `is_rtl` — boolean (`True` when Arabic)
3. **`set_language` view** — Toggles `request.session['lang']`, redirects back to referrer
4. **Templates** — All 14 templates use `{{ t.key }}` for user-facing strings
5. **RTL support** — `base.html` sets `<html dir="{{ t.dir }}">` and includes CSS overrides for RTL (sidebar flips right, margins swap, text alignment)

### Language toggle locations
- **base.html sidebar footer** — Toggle button for all pages extending base.html
- **Standalone pages** (operator_login, dashboard_login, visit_form, car_log_form) — Inline toggle link

### Adding new translations
1. Add key to **both** `TRANSLATIONS['en']` and `TRANSLATIONS['ar']` in `translations.py`
2. Use `{{ t.your_key }}` in templates
3. For JS strings, use `'{{ t.your_key }}'` (Django renders before JS executes)

### Important notes
- Radio button values (`نعم`/`لا`) in visit_form.html and car_log_form.html are **form data values stored in DB** — do NOT translate these
- CSV downloads support `?lang=ar` param for Arabic column headers with BOM prefix
- Form field labels come from Django's `{{ form.field.label }}` (defined in models/forms with Arabic help_text)

## Frontend Patterns

### Theme
Dark theme with CSS variables in `base.html`:
- `--bg-base: #0a0e17`, `--bg-card: #111827`, `--primary: #22d3ee`
- Command-center aesthetic with monospace accents

### Charts (Chart.js 4)
- Loaded globally via CDN in `base.html`
- **CRITICAL**: Chart containers MUST have `position: relative; height: Xpx` wrapper div — without this, charts expand infinitely when `maintainAspectRatio: false`
- Used on: dashboard (bar), machine_detail (line), operator_detail (bar+line), supervisor_dashboard (doughnut)

### Client-side utilities (defined in base.html)
- `window.initTableSearch(inputId, tableId)` — Filters table rows by text input
- CSV export functions use `\uFEFF` BOM prefix for Arabic Excel compatibility
- Filter chips with `data-filter` attributes for category filtering

### Standalone pages
`visit_form.html`, `car_log_form.html`, `operator_login.html`, `dashboard_login.html` do NOT extend `base.html`. They have:
- Their own complete HTML/CSS
- `<html lang="{{ t.lang_code }}" dir="{{ t.dir }}">`
- Inline language toggle links
- Mobile-optimized layouts

## Key View Logic

### Machine name resolution pipeline (in upload views)
1. Exact match against `Machine.name`
2. Alias lookup in `MachineAlias`
3. Fuzzy match via `rapidfuzz` (threshold-based)
4. Batch AI resolution via OpenRouter API

### AI Summary generation
- Uses OpenRouter API with model rotation (handles rate limits)
- Caches results in `MonthlyReport` model
- Triggered manually via dashboard button

### Daily machine summary
- Haversine distance calculation between machine GPS and operator visit GPS
- Aggregates operator visits + car stops per machine per day
- Photo display with expandable rows

### Visit form workflow
- Draft auto-save via AJAX (`visit_auto_save` endpoint)
- Check-in / check-out distinction
- GPS location capture
- Photo upload with client-side preview
- Progress bar tracking form completion

## Conventions
- All dashboard views use `@login_required`
- Month selection via `?month=YYYY-MM` query param throughout
- Date selection via `?date=YYYY-MM-DD` on daily views
- Template variables: `selected_month`, `available_months` are standard
- Form field help_text in Arabic for operator-facing fields
- Excel upload handles both English and Arabic column headers
