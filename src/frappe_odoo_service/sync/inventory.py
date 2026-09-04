import logging
from typing import Dict, Any, List
from frappe_odoo_service.config import TenantConfig
from frappe_odoo_service.clients.frappe_client import FrappeClient
from frappe_odoo_service.clients.odoo_client import OdooClient
from frappe_odoo_service.db.state_store import StateStore

logger = logging.getLogger(__name__)


class InventorySyncer:
    """
    Synchronizes stock inventory quantities from Frappe to Odoo (stock.quant).
    """

    def __init__(self, tenant_config: TenantConfig, frappe: FrappeClient, odoo: OdooClient, state: StateStore):
        self.tenant_id = tenant_config.get_tenant_id()
        self.frappe = frappe
        self.odoo = odoo
        self.state = state

    def sync(self) -> int:
        log_id = self.state.log_sync_start(self.tenant_id, "Inventory")
        synced_count = 0
        try:
            self.odoo.connect()

            # Check if POS Desk custom model havanoposdesk.stock.valuation exists
            if self.odoo.model_exists("havanoposdesk.stock.valuation"):
                logger.info(f"[{self.tenant_id}] Using POS Desk target model 'havanoposdesk.stock.valuation'")
                return self._sync_posdesk_inventory(log_id)

            # Fallback to standard Odoo stock.quant
            if not self.odoo.model_exists("stock.location"):
                logger.warning(f"[{self.tenant_id}] Neither havanoposdesk.stock.valuation nor stock.location models are available in Odoo")
                self.state.log_sync_complete(log_id, 0, "SKIPPED", "No stock models available in Odoo")
                return 0

            locations = self.odoo.search_read(
                "stock.location", [("usage", "=", "internal")], fields=["id"], limit=1
            )
            location_id = locations[0]["id"] if locations else False
            if not location_id:
                logger.warning(f"[{self.tenant_id}] No internal stock location found in Odoo")
                self.state.log_sync_complete(log_id, 0, "SKIPPED", "No internal location in Odoo")
                return 0

            inv_data = self.frappe.get_inventory()
            logger.info(f"[{self.tenant_id}] Fetched {len(inv_data)} inventory entries from Frappe")

            for entry in inv_data:
                if self._sync_single_inventory(entry, location_id):
                    synced_count += 1

            self.state.log_sync_complete(log_id, synced_count, "SUCCESS")
            return synced_count
        except Exception as e:
            logger.error(f"[{self.tenant_id}] Error syncing inventory: {e}")
            self.state.log_sync_complete(log_id, synced_count, "FAILED", str(e))
            return 0

    def _sync_posdesk_inventory(self, log_id: int) -> int:
        # Fetch store name
        stores = self.odoo.search_read("havanoposdesk.store", [("tenant_id", "=", self.odoo.tenant_id)], fields=["id", "name"]) if self.odoo.tenant_id else []
        store_name = stores[0]["name"] if stores else "Default Store"

        # Fetch products from Frappe with warehouse inventory data
        products = self.frappe.get_products()
        synced_count = 0

        # Build map of item_code -> total stock on hand
        stock_map: Dict[str, float] = {}
        for p in products:
            item_code = p.get("itemcode") or p.get("item_code") or p.get("name")
            if not item_code:
                continue

            wh_list = p.get("warehouses", [])
            if wh_list:
                total_qty = sum(float(wh.get("qtyOnHand") or wh.get("actual_qty") or 0.0) for wh in wh_list)
            else:
                total_qty = float(p.get("actual_qty") or p.get("opening_stock") or 0.0)

            stock_map[item_code] = total_qty

        # Supplement with get_inventory() if needed
        try:
            inv_entries = self.frappe.get_inventory()
            for entry in inv_entries:
                item_code = entry.get("item_code") or entry.get("itemcode")
                if item_code and item_code not in stock_map:
                    qty = float(entry.get("actual_qty") or entry.get("qty") or 0.0)
                    stock_map[item_code] = stock_map.get(item_code, 0.0) + qty
        except Exception as e:
            logger.debug(f"get_inventory fallback debug: {e}")

        logger.info(f"[{self.tenant_id}] Processing stock on hand for {len(stock_map)} products in store '{store_name}'")

        for item_code, total_qty in stock_map.items():
            # Get Odoo Product ID
            odoo_prod_id = self.state.get_odoo_id(self.tenant_id, "Item", item_code)
            if not odoo_prod_id:
                search_domain = [("item_code", "=", item_code)]
                if self.odoo.tenant_id:
                    search_domain.append(("tenant_id", "=", self.odoo.tenant_id))
                matched = self.odoo.search_read("havanoposdesk.product", search_domain, fields=["id"])
                if matched:
                    odoo_prod_id = matched[0]["id"]

            if not odoo_prod_id:
                logger.warning(f"Product '{item_code}' not found in Odoo for stock on hand sync")
                continue

            try:
                # Update product opening_stock
                self.odoo.write("havanoposdesk.product", odoo_prod_id, {"opening_stock": total_qty})

                # Check existing valuation
                val_domain = [("product_id", "=", odoo_prod_id)]
                if self.odoo.tenant_id:
                    val_domain.append(("tenant_id", "=", self.odoo.tenant_id))

                valuations = self.odoo.search_read("havanoposdesk.stock.valuation", val_domain, fields=["id"])
                if valuations:
                    val_id = valuations[0]["id"]
                    self.odoo.write("havanoposdesk.stock.valuation", val_id, {"on_hand_qty": total_qty, "store": store_name})
                else:
                    vals = {
                        "product_id": odoo_prod_id,
                        "store": store_name,
                        "on_hand_qty": total_qty,
                    }
                    if self.odoo.tenant_id:
                        vals["tenant_id"] = self.odoo.tenant_id
                    self.odoo.create("havanoposdesk.stock.valuation", vals)

                synced_count += 1
            except Exception as e:
                logger.error(f"Failed updating stock valuation for '{item_code}': {e}")

        self.state.log_sync_complete(log_id, synced_count, "SUCCESS")
        return synced_count

    def _sync_single_inventory(self, entry: Dict[str, Any], location_id: int) -> bool:
        item_code = entry.get("item_code")
        if not item_code:
            return False

        qty = float(entry.get("actual_qty") or entry.get("qty") or 0.0)

        # Get Odoo Product ID
        odoo_prod_id = self.state.get_odoo_id(self.tenant_id, "Item", item_code)
        if not odoo_prod_id:
            matched = self.odoo.search_read("product.product", [("default_code", "=", item_code)], fields=["id"])
            if matched:
                odoo_prod_id = matched[0]["id"]

        if not odoo_prod_id:
            logger.warning(f"Product '{item_code}' not found in Odoo for inventory sync")
            return False

        # Apply stock quant adjustment in Odoo
        try:
            quants = self.odoo.search_read(
                "stock.quant",
                [("product_id", "=", odoo_prod_id), ("location_id", "=", location_id)],
                fields=["id", "inventory_quantity"]
            )
            if quants:
                quant_id = quants[0]["id"]
                self.odoo.write("stock.quant", quant_id, {"inventory_quantity": qty})
                try:
                    self.odoo.execute_kw("stock.quant", "action_apply_inventory", [[quant_id]])
                except Exception:
                    pass
            else:
                quant_id = self.odoo.create("stock.quant", {
                    "product_id": odoo_prod_id,
                    "location_id": location_id,
                    "inventory_quantity": qty,
                })
                try:
                    self.odoo.execute_kw("stock.quant", "action_apply_inventory", [[quant_id]])
                except Exception:
                    pass
            return True
        except Exception as e:
            logger.error(f"Failed updating stock quant for item '{item_code}': {e}")
            return False
