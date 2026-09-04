import logging
from typing import Dict, Any, List
from frappe_odoo_service.config import TenantConfig
from frappe_odoo_service.clients.frappe_client import FrappeClient
from frappe_odoo_service.clients.odoo_client import OdooClient
from frappe_odoo_service.db.state_store import StateStore

logger = logging.getLogger(__name__)


class SalesSyncer:
    """
    Synchronizes Sales Invoices from Frappe to Odoo (havanoposdesk.sale / account.move).
    """

    def __init__(self, tenant_config: TenantConfig, frappe: FrappeClient, odoo: OdooClient, state: StateStore):
        self.tenant_id = tenant_config.get_tenant_id()
        self.frappe = frappe
        self.odoo = odoo
        self.state = state

    def sync(self) -> int:
        log_id = self.state.log_sync_start(self.tenant_id, "SalesInvoice")
        synced_count = 0
        try:
            self.odoo.connect()

            # Check if POS Desk custom model havanoposdesk.sale exists
            if self.odoo.model_exists("havanoposdesk.sale"):
                logger.info(f"[{self.tenant_id}] Using POS Desk target model 'havanoposdesk.sale'")
                return self._sync_posdesk_sales(log_id)

            # Fallback to standard Odoo account.move
            if not self.odoo.model_exists("account.move"):
                logger.warning(f"[{self.tenant_id}] account.move model unavailable on target Odoo tenant")
                self.state.log_sync_complete(log_id, 0, "SKIPPED", "account.move model unavailable")
                return 0

            invoices = self.frappe.get_sales_invoices()
            logger.info(f"[{self.tenant_id}] Fetched {len(invoices)} sales invoices from Frappe")

            for inv_data in invoices:
                if self._sync_single_invoice(inv_data):
                    synced_count += 1

            self.state.log_sync_complete(log_id, synced_count, "SUCCESS")
            return synced_count
        except Exception as e:
            logger.error(f"[{self.tenant_id}] Error syncing sales invoices: {e}")
            self.state.log_sync_complete(log_id, synced_count, "FAILED", str(e))
            return 0

    def _sync_posdesk_sales(self, log_id: int) -> int:
        invoices = self.frappe.get_sales_invoices()
        logger.info(f"[{self.tenant_id}] Fetched {len(invoices)} sales invoices from Frappe for POS Desk sync")
        synced_count = 0

        # Pre-fetch default store & currency
        stores = self.odoo.search_read("havanoposdesk.store", [("tenant_id", "=", self.odoo.tenant_id)], fields=["id", "name", "currency_id"]) if self.odoo.tenant_id else []
        default_store_id = stores[0]["id"] if stores else False
        currency_id = stores[0]["currency_id"][0] if (stores and stores[0].get("currency_id")) else (self.odoo.currency_id or 1)

        # Store cache: store_name -> store_id
        store_cache = {s["name"].lower(): s["id"] for s in stores}

        for inv_data in invoices:
            if self._sync_single_posdesk_sale(inv_data, default_store_id, currency_id, store_cache):
                synced_count += 1

        self.state.log_sync_complete(log_id, synced_count, "SUCCESS")
        return synced_count

    def _sync_single_posdesk_sale(
        self, inv_data: Dict[str, Any], default_store_id: Any, currency_id: Any, store_cache: Dict[str, int]
    ) -> bool:
        inv_name = inv_data.get("name")
        if not inv_name:
            return False

        existing_odoo_id = self.state.get_odoo_id(self.tenant_id, "Sales Invoice", inv_name)
        if existing_odoo_id:
            return True

        # Check existing on Odoo
        domain = [("local_invoice_id", "=", inv_name)]
        if self.odoo.tenant_id:
            domain.append(("tenant_id", "=", self.odoo.tenant_id))

        matched = self.odoo.search_read("havanoposdesk.sale", domain, fields=["id"])
        if matched:
            self.state.save_mapping(self.tenant_id, "Sales Invoice", inv_name, "havanoposdesk.sale", matched[0]["id"])
            return True

        # Resolve Customer
        cust_name = inv_data.get("customer_name") or inv_data.get("customer") or "Cash Customer"
        cust_domain = [("name", "=ilike", cust_name.strip())]
        if self.odoo.tenant_id:
            cust_domain.append(("tenant_id", "=", self.odoo.tenant_id))

        cust_matched = self.odoo.search_read("havanoposdesk.customer", cust_domain, fields=["id"])
        if cust_matched:
            cust_id = cust_matched[0]["id"]
        else:
            c_vals = {"name": cust_name.strip()}
            if self.odoo.tenant_id:
                c_vals["tenant_id"] = self.odoo.tenant_id
            cust_id = self.odoo.create("havanoposdesk.customer", c_vals)

        # Resolve Store from Cost Center / Warehouse
        raw_cc = inv_data.get("cost_center") or inv_data.get("set_warehouse") or ""
        clean_cc = raw_cc.split("-")[0].strip() if "-" in raw_cc else raw_cc.strip()
        store_id = default_store_id

        if clean_cc:
            if clean_cc.lower() in store_cache:
                store_id = store_cache[clean_cc.lower()]
            else:
                st_domain = [("name", "=ilike", clean_cc)]
                if self.odoo.tenant_id:
                    st_domain.append(("tenant_id", "=", self.odoo.tenant_id))
                st_matched = self.odoo.search_read("havanoposdesk.store", st_domain, fields=["id"])
                if st_matched:
                    store_id = st_matched[0]["id"]
                    store_cache[clean_cc.lower()] = store_id
                else:
                    try:
                        s_vals = {"name": clean_cc, "auto_populate_data": False}
                        if self.odoo.tenant_id:
                            s_vals["tenant_id"] = self.odoo.tenant_id
                        store_id = self.odoo.create("havanoposdesk.store", s_vals)
                        store_cache[clean_cc.lower()] = store_id
                        logger.info(f"Auto-created Odoo store '{clean_cc}' with ID {store_id} for invoice '{inv_name}'")
                    except Exception as e:
                        logger.warning(f"Failed creating store for '{clean_cc}': {e}")

        # Build Sale Lines
        doc = self.frappe.get_resource_doc("Sales Invoice", inv_name) if "items" not in inv_data else inv_data
        line_ids = []
        for item in doc.get("items", []):
            item_code = item.get("item_code") or item.get("itemcode") or item.get("item_name")
            if not item_code:
                continue

            prod_id = self.state.get_odoo_id(self.tenant_id, "Item", item_code)
            if not prod_id:
                p_domain = [("item_code", "=", item_code)]
                if self.odoo.tenant_id:
                    p_domain.append(("tenant_id", "=", self.odoo.tenant_id))
                p_matched = self.odoo.search_read("havanoposdesk.product", p_domain, fields=["id"])
                if p_matched:
                    prod_id = p_matched[0]["id"]

            if not prod_id:
                continue

            line_vals = {
                "product_id": prod_id,
                "accepted_qty": float(item.get("qty", 1.0)),
                "rate": float(item.get("rate", 0.0)),
            }
            if self.odoo.tenant_id:
                line_vals["tenant_id"] = self.odoo.tenant_id
            line_ids.append((0, 0, line_vals))

        if not line_ids:
            logger.warning(f"No valid items found for Sales Invoice '{inv_name}', skipping")
            return False

        sale_vals: Dict[str, Any] = {
            "local_invoice_id": inv_name,
            "customer": cust_id,
            "posting_date": doc.get("posting_date"),
            "currency_id": currency_id,
            "payment_status": "cash",
            "state": "confirmed",
            "line_ids": line_ids,
        }
        if store_id:
            sale_vals["store_id"] = store_id
        if self.odoo.tenant_id:
            sale_vals["tenant_id"] = self.odoo.tenant_id

        try:
            odoo_id = self.odoo.create("havanoposdesk.sale", sale_vals)
            self.state.save_mapping(self.tenant_id, "Sales Invoice", inv_name, "havanoposdesk.sale", odoo_id)
            logger.info(f"Created Odoo POS sale '{inv_name}' with ID {odoo_id}")
            return True
        except Exception as e:
            if "Local Invoice ID must be unique" in str(e):
                matched_dup = self.odoo.search_read("havanoposdesk.sale", [("local_invoice_id", "=", inv_name)], fields=["id"])
                if matched_dup:
                    self.state.save_mapping(self.tenant_id, "Sales Invoice", inv_name, "havanoposdesk.sale", matched_dup[0]["id"])
                    logger.info(f"Mapped existing Odoo POS sale '{inv_name}' with ID {matched_dup[0]['id']}")
                    return True
            logger.error(f"Failed creating Odoo POS sale for '{inv_name}': {e}")
            return False

    def _sync_single_invoice(self, inv_data: Dict[str, Any]) -> bool:
        inv_name = inv_data.get("name")
        if not inv_name:
            return False

        existing_odoo_id = self.state.get_odoo_id(self.tenant_id, "Sales Invoice", inv_name)
        if existing_odoo_id:
            return True

        customer_id = inv_data.get("customer")
        partner_id = False
        if customer_id:
            partner_id = self.state.get_odoo_id(self.tenant_id, "Customer", customer_id)
            if not partner_id:
                matched = self.odoo.search_read("res.partner", [("name", "=", customer_id)], fields=["id"])
                if matched:
                    partner_id = matched[0]["id"]

        if not partner_id:
            partners = self.odoo.search_read("res.partner", [("active", "=", True)], fields=["id"], limit=1)
            partner_id = partners[0]["id"] if partners else 1

        doc = self.frappe.get_resource_doc("Sales Invoice", inv_name) if "items" not in inv_data else inv_data

        invoice_lines = []
        for item in doc.get("items", []):
            item_code = item.get("item_code")
            prod_id = self.state.get_odoo_id(self.tenant_id, "Item", item_code)
            if not prod_id and item_code:
                matched = self.odoo.search_read("product.product", [("default_code", "=", item_code)], fields=["id"])
                if matched:
                    prod_id = matched[0]["id"]

            line_vals = {
                "name": item.get("item_name") or item_code or "Sales Item",
                "quantity": float(item.get("qty", 1)),
                "price_unit": float(item.get("rate", 0)),
            }
            if prod_id:
                line_vals["product_id"] = prod_id

            invoice_lines.append((0, 0, line_vals))

        move_vals = {
            "move_type": "out_invoice",
            "partner_id": partner_id,
            "ref": inv_name,
            "invoice_date": doc.get("posting_date"),
            "invoice_line_ids": invoice_lines,
        }

        try:
            odoo_id = self.odoo.create("account.move", move_vals)
            self.state.save_mapping(self.tenant_id, "Sales Invoice", inv_name, "account.move", odoo_id)
            return True
        except Exception as e:
            logger.error(f"Failed creating account.move in Odoo for Frappe invoice '{inv_name}': {e}")
            return False
