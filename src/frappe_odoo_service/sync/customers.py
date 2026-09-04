import logging
from typing import Dict, Any, List
from frappe_odoo_service.config import TenantConfig
from frappe_odoo_service.clients.frappe_client import FrappeClient
from frappe_odoo_service.clients.odoo_client import OdooClient
from frappe_odoo_service.db.state_store import StateStore

logger = logging.getLogger(__name__)


class CustomerSyncer:
    """
    Synchronizes Customers from Frappe to Odoo (res.partner).
    """

    def __init__(self, tenant_config: TenantConfig, frappe: FrappeClient, odoo: OdooClient, state: StateStore):
        self.tenant_id = tenant_config.get_tenant_id()
        self.frappe = frappe
        self.odoo = odoo
        self.state = state

    def sync(self) -> int:
        log_id = self.state.log_sync_start(self.tenant_id, "Customer")
        synced_count = 0
        try:
            customers = self.frappe.get_customers()
            logger.info(f"[{self.tenant_id}] Fetched {len(customers)} customers from Frappe")

            for cust_data in customers:
                if self._sync_single_customer(cust_data):
                    synced_count += 1

            self.state.log_sync_complete(log_id, synced_count, "SUCCESS")
            return synced_count
        except Exception as e:
            logger.error(f"[{self.tenant_id}] Error syncing customers: {e}")
            self.state.log_sync_complete(log_id, synced_count, "FAILED", str(e))
            raise

    def _sync_single_customer(self, cust_data: Dict[str, Any]) -> bool:
        frappe_id = cust_data.get("name") or cust_data.get("customer_name")
        if not frappe_id:
            return False

        customer_name = cust_data.get("customer_name") or frappe_id
        email = cust_data.get("email_id") or cust_data.get("email")
        phone = cust_data.get("mobile_no") or cust_data.get("phone")
        tax_id = cust_data.get("tax_id")
        customer_type = cust_data.get("customer_type", "Company")

        self.odoo.connect()
        odoo_vals: Dict[str, Any] = {
            "name": customer_name,
            "email": email or False,
            "phone": phone or False,
            "vat": tax_id or False,
            "is_company": customer_type == "Company",
        }

        # Check existing mapping
        existing_odoo_id = self.state.get_odoo_id(self.tenant_id, "Customer", frappe_id)
        if existing_odoo_id:
            self.odoo.write("res.partner", existing_odoo_id, odoo_vals)
            self.state.save_mapping(self.tenant_id, "Customer", frappe_id, "res.partner", existing_odoo_id)
            return True

        # Search by email or name in Odoo if not mapped
        domain = []
        if email:
            domain = [("email", "=", email)]
        else:
            domain = [("name", "=", customer_name)]

        matched = self.odoo.search_read("res.partner", domain, fields=["id"])
        if matched:
            odoo_id = matched[0]["id"]
            self.odoo.write("res.partner", odoo_id, odoo_vals)
            self.state.save_mapping(self.tenant_id, "Customer", frappe_id, "res.partner", odoo_id)
            return True

        # Create new partner in Odoo
        odoo_id = self.odoo.create("res.partner", odoo_vals)
        self.state.save_mapping(self.tenant_id, "Customer", frappe_id, "res.partner", odoo_id)
        return True
