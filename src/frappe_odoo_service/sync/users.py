import logging
from typing import Dict, Any, List
from frappe_odoo_service.config import TenantConfig
from frappe_odoo_service.clients.frappe_client import FrappeClient
from frappe_odoo_service.clients.odoo_client import OdooClient
from frappe_odoo_service.db.state_store import StateStore

logger = logging.getLogger(__name__)


class UserSyncer:
    """
    Synchronizes Users from Frappe to Odoo (res.users).
    """

    def __init__(self, tenant_config: TenantConfig, frappe: FrappeClient, odoo: OdooClient, state: StateStore):
        self.tenant_id = tenant_config.get_tenant_id()
        self.frappe = frappe
        self.odoo = odoo
        self.state = state

    def sync(self) -> int:
        log_id = self.state.log_sync_start(self.tenant_id, "User")
        synced_count = 0
        try:
            users = self.frappe.get_users()
            logger.info(f"[{self.tenant_id}] Fetched {len(users)} users from Frappe")

            for user_data in users:
                if self._sync_single_user(user_data):
                    synced_count += 1

            self.state.log_sync_complete(log_id, synced_count, "SUCCESS")
            return synced_count
        except Exception as e:
            logger.error(f"[{self.tenant_id}] Error syncing users: {e}")
            self.state.log_sync_complete(log_id, synced_count, "FAILED", str(e))
            raise

    def _sync_single_user(self, user_data: Dict[str, Any]) -> bool:
        frappe_id = user_data.get("name") or user_data.get("email")
        if not frappe_id or frappe_id in ("Administrator", "Guest"):
            return False

        email = user_data.get("email") or frappe_id
        first_name = user_data.get("first_name", "")
        last_name = user_data.get("last_name", "")
        name = f"{first_name} {last_name}".strip() or email

        odoo_vals = {
            "name": name,
            "login": email,
            "email": email,
            "phone": user_data.get("phone") or user_data.get("mobile_no"),
            "active": bool(user_data.get("enabled", True)),
        }

        # Check existing mapping
        existing_odoo_id = self.state.get_odoo_id(self.tenant_id, "User", frappe_id)

        if existing_odoo_id:
            # Update existing
            self.odoo.write("res.users", existing_odoo_id, odoo_vals)
            self.state.save_mapping(self.tenant_id, "User", frappe_id, "res.users", existing_odoo_id)
            return True

        # Check in Odoo by login or email
        matched = self.odoo.search_read("res.users", ["|", ("login", "=", email), ("email", "=", email)], fields=["id"])
        if matched:
            odoo_id = matched[0]["id"]
            try:
                self.odoo.write("res.users", odoo_id, odoo_vals)
            except Exception as e:
                logger.warning(f"Could not update res.users ID {odoo_id}: {e}")
            self.state.save_mapping(self.tenant_id, "User", frappe_id, "res.users", odoo_id)
            return True

        # Create new user in Odoo
        try:
            odoo_id = self.odoo.create("res.users", odoo_vals)
            self.state.save_mapping(self.tenant_id, "User", frappe_id, "res.users", odoo_id)
            return True
        except Exception as e:
            if "already in use" in str(e).lower() or "unique" in str(e).lower():
                logger.warning(f"Skipping user '{email}': Email is already in use in Odoo: {e}")
                # Try finding existing ID to save mapping
                dup = self.odoo.search_read("res.users", ["|", ("login", "=", email), ("email", "=", email)], fields=["id"])
                if dup:
                    self.state.save_mapping(self.tenant_id, "User", frappe_id, "res.users", dup[0]["id"])
                return False
            logger.error(f"Failed creating user '{email}' in Odoo: {e}")
            return False
