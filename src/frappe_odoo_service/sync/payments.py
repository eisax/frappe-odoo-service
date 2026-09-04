import logging
from typing import Dict, Any, List
from frappe_odoo_service.config import TenantConfig
from frappe_odoo_service.clients.frappe_client import FrappeClient
from frappe_odoo_service.clients.odoo_client import OdooClient
from frappe_odoo_service.db.state_store import StateStore

logger = logging.getLogger(__name__)


class PaymentSyncer:
    """
    Synchronizes Payment Entries from Frappe to Odoo (havanoposdesk.payment / account.payment).
    """

    def __init__(self, tenant_config: TenantConfig, frappe: FrappeClient, odoo: OdooClient, state: StateStore):
        self.tenant_id = tenant_config.get_tenant_id()
        self.frappe = frappe
        self.odoo = odoo
        self.state = state

    def sync(self) -> int:
        log_id = self.state.log_sync_start(self.tenant_id, "Payment")
        synced_count = 0
        try:
            self.odoo.connect()
            if not self.odoo.model_exists("havanoposdesk.payment"):
                logger.warning(f"[{self.tenant_id}] havanoposdesk.payment model unavailable on target Odoo tenant")
                self.state.log_sync_complete(log_id, 0, "SKIPPED", "havanoposdesk.payment model unavailable")
                return 0

            # Fetch submitted Payment Entries from Frappe
            try:
                payments = self.frappe.get_resource_list(
                    "Payment Entry",
                    fields=["name", "party_type", "party", "paid_amount", "received_amount", "posting_date", "mode_of_payment"],
                    filters={"docstatus": 1},
                    limit_page_length=200
                )
            except Exception as e:
                logger.warning(f"[{self.tenant_id}] Failed to fetch payment entries: {e}")
                payments = []

            logger.info(f"[{self.tenant_id}] Fetched {len(payments)} payment entries from Frappe")

            for pay_data in payments:
                if self._sync_single_payment(pay_data):
                    synced_count += 1

            self.state.log_sync_complete(log_id, synced_count, "SUCCESS")
            return synced_count
        except Exception as e:
            logger.error(f"[{self.tenant_id}] Error syncing payments: {e}")
            self.state.log_sync_complete(log_id, synced_count, "FAILED", str(e))
            raise

    def _sync_single_payment(self, pay_data: Dict[str, Any]) -> bool:
        pay_name = pay_data.get("name")
        if not pay_name:
            return False

        existing_odoo_id = self.state.get_odoo_id(self.tenant_id, "Payment Entry", pay_name)
        if existing_odoo_id:
            return True

        # Check existing on Odoo
        domain = [("name", "=", pay_name)]
        if self.odoo.tenant_id:
            domain.append(("tenant_id", "=", self.odoo.tenant_id))

        matched = self.odoo.search_read("havanoposdesk.payment", domain, fields=["id"])
        if matched:
            self.state.save_mapping(self.tenant_id, "Payment Entry", pay_name, "havanoposdesk.payment", matched[0]["id"])
            return True

        party_name = pay_data.get("party") or "Cash Customer"
        party_type = pay_data.get("party_type", "Customer")
        amount = float(pay_data.get("paid_amount") or pay_data.get("received_amount") or 0.0)

        # Get or create customer
        cust_id = None
        if party_type == "Customer":
            c_matched = self.odoo.search_read("havanoposdesk.customer", [("name", "=ilike", party_name.strip())], fields=["id"])
            if c_matched:
                cust_id = c_matched[0]["id"]
            else:
                c_vals = {"name": party_name.strip()}
                if self.odoo.tenant_id:
                    c_vals["tenant_id"] = self.odoo.tenant_id
                cust_id = self.odoo.create("havanoposdesk.customer", c_vals)

        # Get cash/bank account
        accounts = self.odoo.search_read("havanoposdesk.account", [("tenant_id", "=", self.odoo.tenant_id)], fields=["id"], limit=1) if self.odoo.tenant_id else []
        account_id = accounts[0]["id"] if accounts else False

        # Get currency
        stores = self.odoo.search_read("havanoposdesk.store", [("tenant_id", "=", self.odoo.tenant_id)], fields=["currency_id"], limit=1) if self.odoo.tenant_id else []
        currency_id = stores[0]["currency_id"][0] if (stores and stores[0].get("currency_id")) else 1

        pay_vals: Dict[str, Any] = {
            "name": pay_name,
            "payment_type": "receipt" if party_type == "Customer" else "payment",
            "partner_type": "customer" if party_type == "Customer" else "supplier",
            "date": pay_data.get("posting_date"),
        }
        if cust_id:
            pay_vals["customer_id"] = cust_id
        if account_id:
            pay_vals["account_id"] = account_id
        if currency_id:
            pay_vals["currency_id"] = currency_id
        if self.odoo.tenant_id:
            pay_vals["tenant_id"] = self.odoo.tenant_id

        try:
            odoo_id = self.odoo.create("havanoposdesk.payment", pay_vals)
            self.state.save_mapping(self.tenant_id, "Payment Entry", pay_name, "havanoposdesk.payment", odoo_id)
            logger.info(f"Created Odoo payment '{pay_name}' with ID {odoo_id}")
            return True
        except Exception as e:
            logger.error(f"Failed creating Odoo payment '{pay_name}': {e}")
            return False
