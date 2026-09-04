import logging
from typing import Dict, Any, List, Optional
from frappe_odoo_service.config import TenantConfig
from frappe_odoo_service.clients.frappe_client import FrappeClient
from frappe_odoo_service.clients.odoo_client import OdooClient
from frappe_odoo_service.db.state_store import StateStore

logger = logging.getLogger(__name__)


class PricelistSyncer:
    """
    Synchronizes Pricelists and Advanced Prices (havanoposdesk.pricelist, havanoposdesk.product.uom.price) from Frappe to Odoo.
    """

    def __init__(self, tenant_config: TenantConfig, frappe: FrappeClient, odoo: OdooClient, state: StateStore):
        self.tenant_id = tenant_config.get_tenant_id()
        self.frappe = frappe
        self.odoo = odoo
        self.state = state

    def sync(self) -> int:
        log_id = self.state.log_sync_start(self.tenant_id, "Pricelist")
        synced_count = 0
        try:
            self.odoo.connect()
            products = self.frappe.get_products()
            logger.info(f"[{self.tenant_id}] Syncing pricelists and advanced prices for {len(products)} products")

            for prod in products:
                synced_count += self.sync_product_prices(prod)

            self.state.log_sync_complete(log_id, synced_count, "SUCCESS")
            return synced_count
        except Exception as e:
            logger.error(f"[{self.tenant_id}] Error syncing pricelists: {e}")
            self.state.log_sync_complete(log_id, synced_count, "FAILED", str(e))
            raise

    def get_or_create_pricelist(self, name: str, ptype: str = "selling") -> Optional[int]:
        if not name:
            name = "Retail"
        if not self.odoo.model_exists("havanoposdesk.pricelist"):
            return None

        domain = [("name", "=ilike", name.strip())]
        if self.odoo.tenant_id:
            domain.append(("tenant_id", "=", self.odoo.tenant_id))

        matched = self.odoo.search_read("havanoposdesk.pricelist", domain, fields=["id"])
        if matched:
            return matched[0]["id"]

        try:
            vals = {
                "name": name,
                "type": ptype if ptype in ("selling", "buying") else "selling",
            }
            if self.odoo.tenant_id:
                vals["tenant_id"] = self.odoo.tenant_id
            return self.odoo.create("havanoposdesk.pricelist", vals)
        except Exception as e:
            logger.warning(f"Failed creating pricelist '{name}': {e}")
            return None

    def get_or_create_uom(self, uom_name: str) -> Optional[int]:
        if not uom_name:
            uom_name = "Each"
        if not self.odoo.model_exists("havanoposdesk.uom"):
            return None

        domain = [("name", "=ilike", uom_name.strip())]
        if self.odoo.tenant_id:
            domain.append(("tenant_id", "=", self.odoo.tenant_id))

        matched = self.odoo.search_read("havanoposdesk.uom", domain, fields=["id"])
        if matched:
            return matched[0]["id"]

        try:
            vals = {"name": uom_name}
            if self.odoo.tenant_id:
                vals["tenant_id"] = self.odoo.tenant_id
            return self.odoo.create("havanoposdesk.uom", vals)
        except Exception as e:
            logger.warning(f"Failed creating UOM '{uom_name}': {e}")
            return None

    def sync_product_prices(self, prod_data: Dict[str, Any], odoo_product_id: Optional[int] = None) -> int:
        if not self.odoo.model_exists("havanoposdesk.product.uom.price"):
            return 0

        item_code = prod_data.get("itemcode") or prod_data.get("item_code") or prod_data.get("name")
        if not item_code:
            return 0

        if not odoo_product_id:
            odoo_product_id = self.state.get_odoo_id(self.tenant_id, "Item", item_code)

        if not odoo_product_id:
            domain = [("item_code", "=", item_code)]
            if self.odoo.tenant_id:
                domain.append(("tenant_id", "=", self.odoo.tenant_id))
            matched = self.odoo.search_read("havanoposdesk.product", domain, fields=["id"])
            if matched:
                odoo_product_id = matched[0]["id"]

        if not odoo_product_id:
            return 0

        prices_list = prod_data.get("prices", [])
        if not prices_list:
            # Create default Retail price entry if list is empty
            default_rate = float(prod_data.get("standard_rate") or prod_data.get("rate") or 0.0)
            stock_uom = (prod_data.get("uom") or {}).get("stock_uom") or prod_data.get("stock_uom") or "Each"
            prices_list = [{"priceName": "Retail", "price": default_rate, "uom": stock_uom, "type": "selling"}]

        count = 0
        stores = self.odoo.store_ids or [False]

        for p in prices_list:
            p_name = p.get("priceName") or p.get("price_list_name") or "Retail"
            p_price = float(p.get("price") or 0.0)
            p_uom = p.get("uom") or (prod_data.get("uom") or {}).get("stock_uom") or "Each"
            p_type = p.get("type") or "selling"

            pricelist_id = self.get_or_create_pricelist(p_name, p_type)
            uom_id = self.get_or_create_uom(p_uom)

            if not pricelist_id or not uom_id:
                continue

            for store_id in stores:
                search_domain = [
                    ("product_id", "=", odoo_product_id),
                    ("pricelist_id", "=", pricelist_id),
                    ("uom_id", "=", uom_id),
                ]
                if store_id:
                    search_domain.append(("store_id", "=", store_id))
                if self.odoo.tenant_id:
                    search_domain.append(("tenant_id", "=", self.odoo.tenant_id))

                matched = self.odoo.search_read("havanoposdesk.product.uom.price", search_domain, fields=["id"])
                vals = {
                    "product_id": odoo_product_id,
                    "pricelist_id": pricelist_id,
                    "uom_id": uom_id,
                    "price": p_price,
                    "qty_to_be_sold": 1.0,
                }
                if store_id:
                    vals["store_id"] = store_id
                if self.odoo.tenant_id:
                    vals["tenant_id"] = self.odoo.tenant_id

                if matched:
                    price_id = matched[0]["id"]
                    self.odoo.write("havanoposdesk.product.uom.price", price_id, {"price": p_price})
                else:
                    self.odoo.create("havanoposdesk.product.uom.price", vals)
                count += 1

        return count
