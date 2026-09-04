# Frappe to Odoo Sync Service (`frappe-odoo-service`)

A standalone, high-performance Python microservice for synchronizing data **one-way (Frappe/ERPNext -> Odoo)**.

---

## Key Highlights
- **One-Way Sync**: Strictly fetches from Frappe and writes to Odoo. No mutations on Frappe.
- **Zero Code Modification**: Requires **no changes** to Frappe apps (`saas_api`, `havano_pos_integration`, `erpnext`) or Odoo modules (`havanoposdesk_odoo`).
- **Multi-Tenant Ready**: Configure multiple site/database pairs without any hardcoded URLs or credentials.
- **Dynamic Authentication**: Supports both API Keys (`api_key:api_secret`) and username/password session login.
- **Idempotence & State Mapping**: Uses a local SQLite database (`sync_state.db`) to map IDs (`frappe_id <-> odoo_id`) to ensure zero duplicate records.

---

## Quick Start Guide

### 1. Installation

```bash
# Clone or navigate to service directory
cd /Users/josphatndhlovu/Documents/WORK/Showline/SERVICE/frappe-odoo-service

# Install package in editable mode (or inside virtualenv)
pip install -e .
```

If using `frappe-bench` Python environment:
```bash
/Users/josphatndhlovu/frappe-bench/env/bin/pip install -e .
```

---

### 2. Configuration (`config.yaml`)

Create `config.yaml` based on `config.example.yaml`:

```yaml
version: "1.0"
sync_engine:
  poll_interval_seconds: 300
  state_db_path: "sync_state.db"
  batch_size: 50
  log_level: "INFO"

tenants:
  - tenant_id: "erp34_to_saas"
    enabled: true
    frappe:
      base_url: "https://erp34.havano.cloud"
      username: "shurugwi1@lowbic.com"
      password: "shurugwi@123"
      # Or specify api_key and api_secret:
      # api_key: "YOUR_API_KEY"
      # api_secret: "YOUR_API_SECRET"
      use_havano_api: true
      use_saas_api: true
    odoo:
      url: "https://backoffice.havano.pro"
      db: "saas"
      username: "nijotest@gmail.com"
      password: "Pass@123"
      protocol: "xmlrpc"
```

---

### 3. How to Run Sync Commands

#### Way 1: Direct Python Module (Recommended - No PATH setup required)
```bash
PYTHONPATH=src /Users/josphatndhlovu/frappe-bench/env/bin/python3 -m frappe_odoo_service.cli -c config.yaml sync
```

#### Way 2: Virtualenv Binary
```bash
/Users/josphatndhlovu/frappe-bench/env/bin/frappe-odoo-sync -c config.yaml sync
```

#### Way 3: Export PATH (optional for `frappe-odoo-sync` command)
```bash
export PATH="/Users/josphatndhlovu/frappe-bench/env/bin:$PATH"
frappe-odoo-sync -c config.yaml sync
```

#### B. Sync Specific Entity
You can target a specific entity (`users`, `customers`, `products`, `inventory`, `sales`):

```bash
# Sync Users only
frappe-odoo-sync -c config.yaml sync --entity users

# Sync Customers only
frappe-odoo-sync -c config.yaml sync --entity customers

# Sync Products only
frappe-odoo-sync -c config.yaml sync --entity products

# Sync Inventory only
frappe-odoo-sync -c config.yaml sync --entity inventory

# Sync Sales Invoices only
frappe-odoo-sync -c config.yaml sync --entity sales
```

#### C. Sync Specific Tenant
If you have multiple tenants defined in `config.yaml`, run sync for a single tenant:
```bash
frappe-odoo-sync -c config.yaml sync --tenant erp34_to_saas
```

#### D. Check Sync Status & Mapping Database
View mapped counts and recent execution logs:
```bash
frappe-odoo-sync -c config.yaml status
```

#### E. Run Continuous Background Daemon Mode
Runs continuously, polling Frappe every `poll_interval_seconds` (default: 300s):
```bash
frappe-odoo-sync -c config.yaml daemon
```

---

## Running Unit Tests

Run the automated test suite:
```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```
Or with frappe-bench python env:
```bash
PYTHONPATH=src /Users/josphatndhlovu/frappe-bench/env/bin/python3 -m unittest discover -s tests
```
# frappe-odoo-service
# frappe-odoo-service
