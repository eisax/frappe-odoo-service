import logging
from typing import Dict, Any, List
from frappe_odoo_service.config import TenantConfig
from frappe_odoo_service.clients.frappe_client import FrappeClient
from frappe_odoo_service.clients.odoo_client import OdooClient
from frappe_odoo_service.db.state_store import StateStore

logger = logging.getLogger(__name__)


class ProductSyncer:
    """
    Synchronizes Products / Items from Frappe to Odoo (product.product).
    """

    def __init__(self, tenant_config: TenantConfig, frappe: FrappeClient, odoo: OdooClient, state: StateStore):
        self.tenant_id = tenant_config.get_tenant_id()
        self.frappe = frappe
        self.odoo = odoo
        self.state = state

    def sync(self) -> int:
        log_id = self.state.log_sync_start(self.tenant_id, "Product")
        synced_count = 0
        try:
            # Ensure Odoo connection and tenant resolution
            self.odoo.connect()

            products = self.frappe.get_products()
            logger.info(f"[{self.tenant_id}] Fetched {len(products)} products from Frappe")

            # Check if havanoposdesk.product exists
            self.use_custom_pos_model = self.odoo.model_exists("havanoposdesk.product")
            if self.use_custom_pos_model:
                logger.info(f"[{self.tenant_id}] Using target model 'havanoposdesk.product' with tenant_id={self.odoo.tenant_id}")
            else:
                logger.info(f"[{self.tenant_id}] Using standard target model 'product.product'")

            for prod_data in products:
                if self._sync_single_product(prod_data):
                    synced_count += 1

            self.state.log_sync_complete(log_id, synced_count, "SUCCESS")
            return synced_count
        except Exception as e:
            logger.error(f"[{self.tenant_id}] Error syncing products: {e}")
            self.state.log_sync_complete(log_id, synced_count, "FAILED", str(e))
            raise

    def _sync_single_product(self, prod_data: Dict[str, Any]) -> bool:
        item_code = prod_data.get("itemcode") or prod_data.get("item_code") or prod_data.get("name")
        if not item_code:
            return False

        item_name = prod_data.get("itemname") or prod_data.get("item_name") or item_code
        barcode = prod_data.get("barcode") or prod_data.get("simple_code")
        description = prod_data.get("description")

        selling_price = 0.0
        buying_price = 0.0
        for p in prod_data.get("prices", []):
            ptype = p.get("type")
            if ptype == "selling" and not selling_price:
                selling_price = float(p.get("price") or 0.0)
            elif ptype == "buying" and not buying_price:
                buying_price = float(p.get("price") or 0.0)

        if not selling_price:
            selling_price = float(prod_data.get("standard_rate") or prod_data.get("rate") or 0.0)
        if not buying_price:
            buying_price = selling_price

        if self.use_custom_pos_model:
            model_name = "havanoposdesk.product"
            odoo_vals: Dict[str, Any] = {
                "name": item_name,
                "item_code": item_code,
                "selling_price": selling_price,
                "buying_price": buying_price,
                "all_stores": True,
                "track_qty": True,
            }
            if self.odoo.tenant_id:
                odoo_vals["tenant_id"] = self.odoo.tenant_id
            if self.odoo.store_ids:
                odoo_vals["store_ids"] = [(6, 0, self.odoo.store_ids)]
            if barcode:
                odoo_vals["barcode"] = barcode

            search_domain = [("item_code", "=", item_code)]
            if self.odoo.tenant_id:
                search_domain.append(("tenant_id", "=", self.odoo.tenant_id))
        else:
            model_name = "product.product"
            is_stock_item = bool(prod_data.get("is_stock_item", True))
            odoo_vals = {
                "name": item_name,
                "default_code": item_code,
                "list_price": selling_price,
                "type": "consu" if is_stock_item else "service",
                "description_sale": description or False,
            }
            if barcode:
                odoo_vals["barcode"] = barcode
            search_domain = [("default_code", "=", item_code)]

        # Save and sync advanced pricelists/prices
        odoo_id = None
        existing_odoo_id = self.state.get_odoo_id(self.tenant_id, "Item", item_code)
        if existing_odoo_id:
            try:
                self.odoo.write(model_name, existing_odoo_id, odoo_vals)
                self.state.save_mapping(self.tenant_id, "Item", item_code, model_name, existing_odoo_id)
                odoo_id = existing_odoo_id
            except Exception as e:
                logger.warning(f"Failed to update {model_name} ID {existing_odoo_id}: {e}")

        if not odoo_id:
            matched = self.odoo.search_read(model_name, search_domain, fields=["id"])
            if matched:
                odoo_id = matched[0]["id"]
                self.odoo.write(model_name, odoo_id, odoo_vals)
                self.state.save_mapping(self.tenant_id, "Item", item_code, model_name, odoo_id)

        if not odoo_id:
            try:
                odoo_id = self.odoo.create(model_name, odoo_vals)
                self.state.save_mapping(self.tenant_id, "Item", item_code, model_name, odoo_id)
            except Exception as e:
                logger.error(f"Failed to create product '{item_code}' in Odoo ({model_name}): {e}")
                return False

        # Sync Advanced Prices (havanoposdesk.product.uom.price) for this product
        if odoo_id and self.use_custom_pos_model:
            try:
                from frappe_odoo_service.sync.pricelists import PricelistSyncer
                pricelist_syncer = PricelistSyncer(TenantConfig(frappe=self.frappe.config, odoo=self.odoo.config, tenant_id=self.tenant_id), self.frappe, self.odoo, self.state)
                pricelist_syncer.sync_product_prices(prod_data, odoo_id)
            except Exception as e:
                logger.warning(f"Failed syncing advanced prices for item '{item_code}': {e}")

        return True
