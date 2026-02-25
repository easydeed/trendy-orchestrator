# TrendyReports — Product Bible

> This document is the authoritative reference for all AI agents working on TrendyReports.
> Every code change, every feature, every decision must align with this document.
> Updated automatically as agents ship work.

---

## 1. What Is TrendyReports?

TrendyReports is a **multi-tenant SaaS platform** that transforms live MLS data into branded PDF reports and email campaigns for real estate agents. Agents use it to answer the #1 question their clients ask: **"Is it a good time to sell/buy?"**

### Target Users
- **Individual real estate agents** — create reports for their client lists
- **Industry affiliates** (title companies, lenders) — white-label reports for sponsored agents

### Revenue Model
- Free tier: 5 reports/month
- Pro: $29/month (50 reports)
- Team: $99/month (200 reports)
- Affiliate: custom pricing (500 reports)
- Sponsored Free: $0 (affiliate pays, 10 reports)

---

## 2. Core Product Principles

### Aesthetics Are Non-Negotiable
This product serves real estate agents. Image is everything. A mediocre-looking PDF is worse than no PDF. Every visual output must look like it came from a design agency.

### Data Accuracy Over Speed
Reports contain market data that agents present to clients. Wrong numbers destroy trust. Always validate data, handle edge cases, and show confidence levels.

### White-Label Everything
Every customer-facing output (PDFs, emails, landing pages) must respect the agent's branding — their logo, colors, photo, company name. No TrendyReports branding leaks to end consumers.

### Mobile-First Consumer Experience
Consumers (homeowners) interact via SMS links and QR codes on their phones. The mobile report viewer is their first impression of the agent.

---

## 3. Architecture (Do Not Violate)

### Three Services
```
Frontend (Next.js 16) → API (FastAPI) → Worker (Celery)
     Vercel                Render          Render
```

### Database
- PostgreSQL 15 on Render
- Row-Level Security (RLS) for multi-tenant isolation
- `app.current_account_id` set per request via middleware
- 42 migration files in `db/migrations/`

### Key Infrastructure
- Redis: Celery broker + caching + rate limiting
- Cloudflare R2: PDF storage, images, assets
- PDFShift: HTML → PDF conversion (production)

### DO NOT
- Add new services without explicit approval
- Bypass RLS — every tenant-scoped query must respect account isolation
- Store secrets in code — all credentials via environment variables
- Add external dependencies without checking if an internal solution exists first

---

## 4. Codebase Conventions

### Backend (Python / FastAPI)
- **Location:** `apps/api/src/api/`
- **Routes:** `routes/*.py` — 26 route modules, prefixed `/v1/`
- **Services:** `services/*.py` — business logic, never in route handlers
- **Middleware:** Auth → RateLimit → RLS (registration order matters — Starlette LIFO)
- **DB access:** `db_conn()` context manager with `set_rls()` 
- **Error handling:** `HTTPException` with status codes and detail messages
- **Patterns:** Dependency injection via `Depends()`, async where possible

### Worker (Python / Celery)
- **Location:** `apps/worker/src/worker/`
- **Tasks:** Always `bind=True, max_retries=3`
- **PDF pipeline:** PropertyReportBuilder → HTML string → PDFShift → R2 upload
- **Templates:** Jinja2 inheritance — `base.jinja2` + `_macros.jinja2` + theme files
- **Rate limiting:** Token-bucket for SimplyRETS (60 RPM)
- **Filters:** Market-adaptive via `filter_resolver.py` — never hardcode prices

### Frontend (TypeScript / Next.js)
- **Location:** `apps/web/`
- **Framework:** Next.js 16 with App Router, React 19
- **Styling:** Tailwind CSS v4 — utility-first, no custom CSS files
- **Components:** shadcn/ui (Radix-based) — 75+ primitives in `components/ui/`
- **State:** TanStack React Query v5 (5min stale time, no refetch on focus)
- **Forms:** React Hook Form + Zod validation
- **API calls:** Client-side through `/api/proxy/` routes, server-side direct to backend
- **Auth:** JWT in `mr_token` HttpOnly cookie

### Naming Conventions
- Python: snake_case for everything
- TypeScript: camelCase for variables/functions, PascalCase for components/types
- Database: snake_case, plural table names
- API routes: `/v1/resource` pattern, kebab-case for multi-word
- Files: kebab-case for TS/TSX, snake_case for Python

---

## 5. Report Types & Templates

### Market Reports (8 types)
| Key | Name | Description |
|-----|------|-------------|
| `market_snapshot` | Market Update | Overview KPIs |
| `new_listings` | New Listings | Active listings |
| `new_listings_gallery` | New Listings Gallery | Photo-rich |
| `closed_sales` | Closed Sales | Recent sales |
| `inventory` | Inventory | Supply analysis |
| `price_bands` | Price Bands | Price segmentation |
| `open_houses` | Open Houses | Upcoming schedule |
| `featured_listings` | Featured Listings | Curated showcase |

### Property Report Themes (5)
| ID | Name | Fonts | Colors |
|----|------|-------|--------|
| classic | Classic | Merriweather / Source Sans Pro | Navy + Sky |
| modern | Modern | Space Grotesk / DM Sans | Coral + Midnight |
| elegant | Elegant | Playfair Display / Montserrat | Burgundy + Gold |
| teal | Teal (default) | Montserrat / Montserrat | Teal + Navy |
| bold | Bold | Oswald / Montserrat | Navy + Gold |

### Property Report Pages (7)
1. Cover — hero photo, address, agent branding
2. Contents — table of contents
3. Aerial — Google Maps satellite view
4. Property — beds, baths, sqft, APN, owner, taxes
5. Analysis — area sales analysis, comparison table
6. Comparables — up to 12 comps in grid
7. Range — price range chart

---

## 6. External APIs & Constraints

### SimplyRETS (MLS Data)
- Rate limit: 60 RPM per credential pair, burst 10
- Max 500 results per page, paginated via Link headers
- Worker has local rate limiter; API layer does NOT
- Vendor credentials can be overridden per affiliate

### SiteX Pro (Property Assessor Data)
- OAuth2 client credentials, 10-min token TTL
- In-memory cache (24h) for address/APN lookups
- UseCode field drives property type mapping
- Multi-match returns require APN fallback

### PDFShift (PDF Generation)
- Production: margins 0 (CSS controls everything via `@page`)
- Templates use `--pad` CSS variable for internal spacing
- `delay: 5000` + `wait_for_network: true` for image loading
- No caching — fresh render every time

### Stripe (Billing)
- Webhook events: checkout.session.completed, subscription.updated/deleted, invoice.paid/failed
- Plan catalog should be cached (currently makes live Stripe calls — known issue)

---

## 7. Known Issues & Technical Debt

### Performance (from audit)
- [C1] No connection pooling — new TCP connection per DB call
- [C2] Middleware opens 3-5 raw connections per request
- [C3] Live Stripe API calls on every page load
- [H4] Middleware ordering inverted — rate limiting disabled
- [M3] SQL injection risk in set_rls() — uses f-string interpolation

### PDF Templates
- `--pad` varies across themes (0.5–0.6in) — should be standardized
- Footer position varies (0.3in vs 0.4in)
- Playwright margins (0.5in) vs PDFShift (0) — output differs between local/prod
- Google Fonts may not load before PDF capture

### Frontend
- Navigation requires 4-second reloads between sections
- No client-side caching with React Query for instant revisits
- Wizard preview shows CSS mockup only — live preview API exists but isn't wired

### DO NOT introduce new instances of these patterns
- Raw `psycopg.connect()` calls — use `db_conn()` or future pool
- Bare `except` clauses that swallow errors silently
- Duplicate function calls (e.g., calling same service twice in one endpoint)
- N+1 query patterns

---

## 8. Testing Requirements

### Before Any Change Ships
1. `pytest tests/test_property_templates.py -v` must pass (5 themes × 6 test classes)
2. Build must succeed (`cd apps/web && npm run build`)
3. No TypeScript errors (`cd apps/web && npx tsc --noEmit`)
4. No new `any` types in TypeScript without justification

### Test Infrastructure Available
- pytest for template rendering tests
- Playwright for E2E (auth, affiliate, billing, Stripe)
- Smoke scripts in `scripts/` for API integration testing
- QA delivery tool: `qa_deliver_reports.py`

---

## 9. File Location Quick Reference

| What | Where |
|------|-------|
| API routes | `apps/api/src/api/routes/` |
| API services | `apps/api/src/api/services/` |
| API middleware | `apps/api/src/api/middleware/` |
| Celery tasks | `apps/worker/src/worker/tasks.py` |
| PDF engine | `apps/worker/src/worker/pdf_engine.py` |
| Property builder | `apps/worker/src/worker/property_builder.py` |
| Jinja2 templates | `apps/worker/src/worker/templates/property/` |
| Frontend pages | `apps/web/src/app/` |
| Frontend components | `apps/web/src/components/` |
| UI primitives | `apps/web/src/components/ui/` |
| API proxy routes | `apps/web/src/app/api/proxy/` |
| Database migrations | `db/migrations/` |
| Tests | `tests/` and `e2e/` |
| Scripts | `scripts/` |

---

## 10. Deployment

| Service | Platform | Deploy Method |
|---------|----------|---------------|
| Frontend | Vercel | Auto-deploy on push to `main` |
| API | Render | Auto-deploy on push to `main` |
| Worker | Render | Auto-deploy on push to `main` |
| Database | Render | Managed PostgreSQL |
| Redis | Render | Managed Redis |

### Environment Variables
All secrets in Render/Vercel env vars. See `.env.example` for full list.
Key ones: DATABASE_URL, REDIS_URL, JWT_SECRET, SIMPLYRETS_*, SITEX_*, PDFSHIFT_*, STRIPE_*, RESEND_API_KEY, R2_*

---

*Last updated: {{LAST_UPDATED}}*
*Auto-maintained by TrendyReports Agent Orchestrator*
