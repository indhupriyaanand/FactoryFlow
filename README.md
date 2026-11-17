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

### 3. Run Migrations & System Check
```powershell
python manage.py migrate
python manage.py check
```

### 4. Run Automated Tests
```powershell
python manage.py test
```

### 5. Start the Development Server
```powershell
python manage.py runserver
```
Open your browser and visit: **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)**

---

## 📅 Development Roadmap

- [x] **Phase 1**: Project Setup, Environment & Django 5.1 Architecture
- [ ] **Phase 2**: Custom User Model & 5 Distinct Roles (Admin, Sales, Production, Auditor, Owner)
- [ ] **Phase 3**: Master Data (Products, Raw Materials, Customers, Suppliers, BOM)
- [ ] **Phase 4**: Purchases & Append-Only Stock Ledger (WAC Costing)
- [ ] **Phase 5**: Production Runs & Variance Calculations (-6% Yield, +5% Oil)
- [ ] **Phase 6**: Sales Orders & Shortage Auto-Detection (Distributor B 32-packet shortage)
- [ ] **Phase 7**: GST Invoicing, Dispatch & Payment Tracking (Retailer A ₹7,875 invoice)
- [ ] **Phase 8**: Product Costing (Material + Labour + Power) & Report Downloads
- [ ] **Phase 9**: Owner Executive Dashboard & UI Enhancements
- [ ] **Phase 10**: Benchmark Verification Script, Documentation & Docker Configuration
