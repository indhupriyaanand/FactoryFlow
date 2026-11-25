# FactoryFlow – Smart Order-to-Audit Management System

> **NIT Tiruchirappalli – Centre for Digital Infrastructure Winter Internship 2026 Challenge**  
> An integrated, real-world ERP system built for small-to-medium South Indian snacks manufacturing businesses.

---

## 📌 Project Overview

**FactoryFlow** is an end-to-end ERP application designed to streamline factory operations from customer order intake to monthly audit reports. It connects:

1. **Customer & Sales Order Management** (with mobile entry for sales reps)
2. **Bill of Materials (BOM)** & Batch Formulation
3. **Purchases & Inventory Ledger** (strictly append-only, zero balance tampering)
4. **Production Run Management** with real-time variance detection (Yield & Oil consumption)
5. **GST Invoice Billing** (Intra-state CGST+SGST vs Inter-state IGST)
6. **Dispatch & Collections**
7. **Weighted Average Costing (WAC)** & Comprehensive Audit Registers

---

## 🛠️ Technology Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.13 + Django 5.1 |
| **Frontend** | HTML5, Modern CSS, Bootstrap 5.3, Bootstrap Icons, JavaScript |
| **Database** | SQLite (development) / PostgreSQL (production ready) |
| **PDF Generation** | ReportLab |
| **Excel/CSV Export** | openpyxl, Python standard `csv` |
| **VCS** | Git |

---

## 🚀 Quickstart & Setup Guide (Windows 11 + VS Code)

### 1. Open Project Folder in VS Code
Open your VS Code and navigate to:
```
C:\Users\indhu\.gemini\antigravity\scratch\factoryflow
```
Or open via terminal:
```powershell
code "C:\Users\indhu\.gemini\antigravity\scratch\factoryflow"
```

### 2. Activate Virtual Environment
Open PowerShell inside VS Code (`Ctrl + ~`):
```powershell
.\venv\Scripts\Activate.ps1
```

*(Note: If PowerShell shows an ExecutionPolicy error, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` and then run the command again).*

### 3. Configure and migrate a local database
Copy `.env.example` to `.env`, generate a unique local `SECRET_KEY`, and review the
database path before use. Back up the SQLite database before applying migrations:

```powershell
python -c "import sqlite3; src=sqlite3.connect('db.sqlite3'); dst=sqlite3.connect('db-backup.sqlite3'); src.backup(dst); dst.close(); src.close()"
python manage.py makemigrations --check --dry-run
python manage.py migrate
python manage.py check
```

Do not point test commands at a production database. The test runner creates and
rolls back its own test database.

### 4. Run Automated Tests
```powershell
python manage.py test
```

### 5. Start the Development Server
```powershell
python manage.py runserver
```
Open your browser and visit: **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)**

## Container deployment

1. Copy `.env.example` to `.env`, set a long random `SECRET_KEY`, and set the
   externally reachable `ALLOWED_HOSTS`. Do not use the example secret.
2. Build and start the application:

   ```powershell
   docker compose build
   docker compose up -d
   docker compose ps
   ```

3. Before upgrading an existing deployment, stop web writers and take a consistent
   SQLite backup. The database is stored in the `factoryflow-data` named volume,
   not in the application image or the repository. For example:

   ```powershell
   docker compose stop web
   docker compose run --rm --entrypoint python web -c "import sqlite3; src=sqlite3.connect('/data/db.sqlite3'); dst=sqlite3.connect('/data/db-backup.sqlite3'); src.backup(dst); dst.close(); src.close()"
   docker compose cp web:/data/db-backup.sqlite3 .\factoryflow-db-backup.sqlite3
   ```

4. Review the migration plan and apply the checked-in migrations explicitly during
   a maintenance window, then restart and verify `/health/`:

   ```powershell
   docker compose run --rm web python manage.py showmigrations
   docker compose run --rm web python manage.py migrate
   docker compose up -d
   ```

   This project deliberately does not auto-run migrations when the container
   starts. Keep the backup until application and data checks pass. To use
   PostgreSQL instead of the default persistent SQLite volume, configure
   `USE_POSTGRES=True` and the `DB_*` values in `.env`.

Static assets are collected during image build and served by WhiteNoise. Uploaded
media is stored in the `factoryflow-media` volume. Configure HTTPS termination and
`CSRF_TRUSTED_ORIGINS` at the reverse proxy for public deployments.

---

## Implementation notes and limitations

- Inventory receipts and raw-material OUT movements use FIFO source allocation;
  legacy stock is separately identified. Production material cost uses the actual
  consumption allocations, including recorded damaged pouches.
- Each completed production run creates a costed finished-goods source lot.
  Dispatches consume those lots FIFO, and profitability reports calculate COGS
  from the quantities and unit costs allocated to each dispatch. Historical
  dispatches without source allocations and uncosted legacy finished stock are
  reported with unavailable COGS rather than estimated.
- Production plans snapshot each material's available weighted-average (or
  standard) unit rate when the BOM quantities are established. Cost variance
  compares that saved planned cost with actual FIFO consumption cost, including
  non-voided damaged pouch consumption. Legacy plans without a saved rate are
  clearly identified as missing a planned cost baseline.
- Older sales approvals without event rows are exported as legacy status snapshots;
  historical approval actions cannot be reconstructed.
- The end-to-end benchmark test uses transactional test data and does not create
  production transactions in the development or deployment database.
- Review all pending application migrations and apply them manually after taking
  a database backup. This task generated costing migrations but did not apply
  migrations to the local SQLite database.
