import os
import tempfile
import unittest
from frappe_odoo_service.config import AppConfig


class TestConfig(unittest.TestCase):
    def test_config_load(self):
        content = """
version: "1.0"
sync_engine:
  poll_interval_seconds: 60
  state_db_path: "test.db"

tenants:
  - tenant_id: "site_alpha"
    enabled: true
    frappe:
      base_url: "https://frappe.example.com"
      api_key: "key123"
      api_secret: "secret123"
    odoo:
      url: "http://odoo.example.com:8069"
      db: "odoo_db"
      username: "admin"
      password: "pass"
"""
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".yaml") as f:
            f.write(content)
            f_path = f.name

        try:
            config = AppConfig.load_from_yaml(f_path)
            self.assertEqual(config.version, "1.0")
            self.assertEqual(config.sync_engine.poll_interval_seconds, 60)
            self.assertEqual(len(config.tenants), 1)
            t = config.tenants[0]
            self.assertEqual(t.tenant_id, "site_alpha")
            self.assertEqual(t.frappe.auth_header, "token key123:secret123")
            self.assertEqual(t.odoo.db, "odoo_db")
        finally:
            if os.path.exists(f_path):
                os.remove(f_path)


if __name__ == "__main__":
    unittest.main()
