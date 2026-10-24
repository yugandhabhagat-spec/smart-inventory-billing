# smart-inventory-billing

Smart Inventory & Billing Management System: Flask + SQLAlchemy + SQLite, Bootstrap 5, Chart.js, ReportLab.
Built as an MCM project / CSE portfolio piece for small retail shops.

## Run locally (Windows PowerShell)
```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # paste into SECRET_KEY in .env
flask --app run init-db
flask --app run create-admin          # prompts for username + password (no default credentials)
flask --app run seed-demo             # OPTIONAL fictional demo data (dev only)
flask --app run run --debug           # open http://127.0.0.1:5000
pytest -q                             # run the tests
```
macOS/Linux: use `python3`, `source venv/bin/activate`, `cp .env.example .env`.

## Put it on GitHub
```bash
git init
git add .
git commit -m "Initial commit: Smart Inventory & Billing System"
git branch -M main
# create an EMPTY repo named smart-inventory-billing on github.com (no README), then:
git remote add origin https://github.com/<your-username>/smart-inventory-billing.git
git push -u origin main
```
- **Tests on GitHub:** `.github/workflows/tests.yml` runs `pytest` on every push (see the repo's *Actions* tab).
- **Run in the browser:** repo > Code > Codespaces > Create. In its terminal run the commands above, then open the forwarded port 5000.
- `.env`, `instance/` and `*.db` are git-ignored. Never commit secrets.

## Roles
| Role | Can |
|---|---|
| admin | everything, incl. employees, audit log |
| manager | products, categories, inventory, suppliers, purchases, customers, sales, dashboard |
| cashier | POS, own invoices, read-only products (no cost) |
All checks are enforced server-side (`roles_required`).

## Business rules
- Money/quantities use `Decimal` stored as scaled integers (money 2 dp, cost 4 dp, qty 3 dp). Rounding: half-up to 2 dp.
- Server recomputes prices/totals; client totals are ignored.
- Line total = qty x price. Net sales = subtotal - invoice discount (ex-tax). Total = net + tax (`TAX_RATE` %, tax-exclusive).
- Stock: conditional `UPDATE ... WHERE stock >= qty` + DB CHECK (no negative stock) + `inventory_movements` ledger, all in one transaction.
- Purchases: draft -> received (once, atomic guard). Receiving updates stock and weighted-average cost.
- COGS stored per sale from the cost at sale time; Gross profit = net sales - COGS.
- Duplicate checkout blocked by an idempotency key (unique column).
- Walk-in customers must pay in full; registered customers may pay partially and settle later.

## NOT implemented (honest scope)
Returns/refunds, expenses and net profit, reports page and CSV/PDF reports (only invoice PDF exists), settings UI
(use `.env`), supplier-payment tracking, login rate limiting per IP (only per-account lockout), concurrency
testing beyond SQLite's single-writer behaviour, ER/use-case diagrams, viva Q&A, screenshots.
SQLite: wrap-up for PostgreSQL = set `DATABASE_URL`; migrations (`flask --app run db init/migrate/upgrade`) are
untested with the custom `Dec` type; if the generated migration errors on `app.models.Dec`, add `import app.models`
to the top of the migration file.

## Verification status
Verified in the authoring sandbox: Python syntax of all files, money/rounding/validation logic, PDF generation with
ReportLab, Jinja syntax of all templates. NOT verified there (no network to install SQLAlchemy/pytest): the app
starting, database behaviour, routes, and the pytest suite in `tests/`. Run `pytest -q` (or check GitHub Actions)
and fix any failure before trusting a feature.
