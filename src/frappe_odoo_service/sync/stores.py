import logging
from typing import Dict, Any, List
from frappe_odoo_service.config import TenantConfig
from frappe_odoo_service.clients.frappe_client import FrappeClient
from frappe_odoo_service.clients.odoo_client import OdooClient
from frappe_odoo_service.db.state_store import StateStore

logger = logging.getLogger(__name__)


class StoreSyncer:
    """
    Synchronizes Cost Centers / Warehouses from Frappe to Odoo Stores (havanoposdesk.store).
    """

    def __init__(self, tenant_config: TenantConfig, frappe: FrappeClient, odoo: OdooClient, state: StateStore):
        self.tenant_id = tenant_config.get_tenant_id()
        self.frappe = frappe
        self.odoo = odoo
        self.state = state

    def sync(self) -> int:
        log_id = self.state.log_sync_start(self.tenant_id, "Store")
        synced_count = 0
        try:
            self.odoo.connect()
            if not self.odoo.model_exists("havanoposdesk.store"):
                logger.warning(f"[{self.tenant_id}] havanoposdesk.store model unavailable on target Odoo tenant")
                self.state.log_sync_complete(log_id, 0, "SKIPPED", "havanoposdesk.store model unavailable")
                return 0

            # 1. Fetch Cost Centers from Frappe
            store_names = set()
            try:
                ccs = self.frappe.get_resource_list(
                    "Cost Center", fields=["name", "cost_center_name", "is_group"]
                )
                for cc in ccs:
                    if not cc.get("is_group"):
                        sname = cc.get("cost_center_name") or cc.get("name")
                        if sname:
                            store_names.add(sname)
            except Exception as e:
                logger.warning(f"Failed fetching Cost Centers: {e}")

            # 2. Fetch Warehouses from Frappe
            try:
                whs = self.frappe.get_resource_list("Warehouse", fields=["name", "warehouse_name", "is_group"])
                for wh in whs:
                    if not wh.get("is_group"):
                        wname = wh.get("warehouse_name") or wh.get("name")
                        if wname:
                            store_names.add(wname)
            except Exception as e:
                logger.warning(f"Failed fetching Warehouses: {e}")

            logger.info(f"[{self.tenant_id}] Found {len(store_names)} unique cost centers/shops from Frappe: {store_names}")

            for sname in store_names:
                if self._sync_single_store(sname):
                    synced_count += 1

            self.state.log_sync_complete(log_id, synced_count, "SUCCESS")
            return synced_count
        except Exception as e:
            logger.error(f"[{self.tenant_id}] Error syncing stores: {e}")
            self.state.log_sync_complete(log_id, synced_count, "FAILED", str(e))
            raise

    def _sync_single_store(self, store_name: str) -> bool:
        if not store_name:
            return False

        # Search existing store in Odoo
        domain = [("name", "=ilike", store_name.strip())]
        if self.odoo.tenant_id:
            domain.append(("tenant_id", "=", self.odoo.tenant_id))

        matched = self.odoo.search_read("havanoposdesk.store", domain, fields=["id", "name"])
        if matched:
            store_id = matched[0]["id"]
            self.state.save_mapping(self.tenant_id, "Cost Center", store_name, "havanoposdesk.store", store_id)
            return True

        # Create new store in Odoo
        try:
            vals = {
                "name": store_name.strip(),
                "auto_populate_data": False,
            }
            if self.odoo.tenant_id:
                vals["tenant_id"] = self.odoo.tenant_id

            store_id = self.odoo.create("havanoposdesk.store", vals)
            self.state.save_mapping(self.tenant_id, "Cost Center", store_name, "havanoposdesk.store", store_id)
            logger.info(f"Created Odoo store '{store_name}' with ID {store_id}")
            return True
        except Exception as e:
            logger.error(f"Failed creating Odoo store '{store_name}': {e}")
            return False
