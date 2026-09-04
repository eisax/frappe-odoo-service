import os
import tempfile
import unittest
from frappe_odoo_service.db.state_store import StateStore


class TestStateStore(unittest.TestCase):
    def test_state_store_mapping(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            db_path = f.name

        try:
            store = StateStore(db_path)

            # Test initially empty
            self.assertIsNone(store.get_odoo_id("tenant1", "User", "user1@example.com"))

            # Save mapping
            store.save_mapping("tenant1", "User", "user1@example.com", "res.users", 42)
            self.assertEqual(store.get_odoo_id("tenant1", "User", "user1@example.com"), 42)
            self.assertEqual(store.get_frappe_id("tenant1", "res.users", 42), "user1@example.com")

            # Update mapping
            store.save_mapping("tenant1", "User", "user1@example.com", "res.users", 99)
            self.assertEqual(store.get_odoo_id("tenant1", "User", "user1@example.com"), 99)

            # Log sync
            log_id = store.log_sync_start("tenant1", "User")
            self.assertGreater(log_id, 0)
            store.log_sync_complete(log_id, 5, "SUCCESS")
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)


if __name__ == "__main__":
    unittest.main()
