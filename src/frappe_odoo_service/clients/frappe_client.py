import requests
import logging
from typing import Any, Dict, List, Optional
from frappe_odoo_service.config import FrappeConfig

logger = logging.getLogger(__name__)


class FrappeClient:
    """
    HTTP Client to interact with Frappe/ERPNext via REST API and custom whitelisted methods.
    Does NOT modify any code on the Frappe server.
    """

    def __init__(self, config: FrappeConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if config.auth_header:
            self.session.headers["Authorization"] = config.auth_header
        elif config.username and config.password:
            self.login(config.username, config.password)

    def login(self, username: str, password: str):
        # Try custom login via havano_pos_integration or saas_api first
        if self.config.use_havano_api or self.config.use_saas_api:
            for ep in ["api/method/havano_pos_integration.auth.login", "api/method/saas_api.www.api.login"]:
                url = self._url(ep)
                try:
                    res = self.session.post(url, json={"usr": username, "pwd": password, "timezone": "Africa/Harare"})
                    if res.status_code == 200:
                        data = res.json()
                        msg = data.get("message") or data
                        token_str = data.get("token_string") or (msg.get("token_string") if isinstance(msg, dict) else None)
                        if token_str:
                            self.session.headers["Authorization"] = f"token {token_str}"
                        logger.info(f"Successfully logged in via {ep} to Frappe site {self.base_url} as {username}")
                        return
                except Exception as e:
                    logger.debug(f"Login attempt at {ep} failed: {e}")

        # Fall back to standard Frappe login
        url = self._url("api/method/login")
        res = self.session.post(url, json={"usr": username, "pwd": password})
        res.raise_for_status()
        logger.info(f"Successfully logged in to Frappe site {self.base_url} as {username}")

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def get_resource_list(
        self,
        doctype: str,
        fields: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit_start: int = 0,
        limit_page_length: int = 100,
        order_by: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        url = self._url(f"api/resource/{doctype}")
        params: Dict[str, Any] = {
            "limit_start": limit_start,
            "limit_page_length": limit_page_length,
        }
        if fields:
            import json
            params["fields"] = json.dumps(fields)
        if filters:
            import json
            params["filters"] = json.dumps(filters)
        if order_by:
            params["order_by"] = order_by

        res = self.session.get(url, params=params)
        res.raise_for_status()
        return res.json().get("data", [])

    def get_resource_doc(self, doctype: str, name: str) -> Dict[str, Any]:
        url = self._url(f"api/resource/{doctype}/{name}")
        res = self.session.get(url)
        res.raise_for_status()
        return res.json().get("data", {})

    def call_method(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = self._url(f"api/method/{method}")
        if params:
            res = self.session.post(url, json=params)
        else:
            res = self.session.get(url)
        res.raise_for_status()
        data = res.json()
        return data.get("message", data.get("data"))

    # Custom endpoints wrappers for havano_pos_integration and saas_api
    def get_users(self) -> List[Dict[str, Any]]:
        """Fetch users via custom API or fall back to User resource"""
        if self.config.use_havano_api:
            try:
                res = self.call_method("havano_pos_integration.api.get_user")
                if isinstance(res, list):
                    return res
                if isinstance(res, dict):
                    return res.get("users") or res.get("data") or []
            except Exception as e:
                logger.warning(f"Failed to fetch users via havano_pos_integration: {e}, falling back to standard REST API")

        return self.get_resource_list("User", fields=["name", "email", "first_name", "last_name", "enabled", "user_type", "mobile_no", "phone"])

    def get_customers(self) -> List[Dict[str, Any]]:
        """Fetch customers via custom API or fall back to Customer resource"""
        if self.config.use_havano_api:
            try:
                res = self.call_method("havano_pos_integration.api.get_customer")
                if isinstance(res, list):
                    return res
                if isinstance(res, dict):
                    return res.get("customers") or res.get("data") or []
            except Exception as e:
                logger.warning(f"Failed to fetch customers via havano_pos_integration: {e}, falling back to standard REST API")

        return self.get_resource_list(
            "Customer",
            fields=["name", "customer_name", "customer_type", "customer_group", "territory", "email_id", "mobile_no", "tax_id"]
        )

    def get_products(self) -> List[Dict[str, Any]]:
        """Fetch products/items via custom API or standard REST API"""
        if self.config.use_havano_api:
            try:
                res = self.call_method("havano_pos_integration.api.get_products")
                if isinstance(res, list):
                    return res
                if isinstance(res, dict):
                    prods = res.get("products") or res.get("data")
                    if isinstance(prods, list):
                        return prods
            except Exception as e:
                logger.warning(f"Failed to fetch products via havano_pos_integration: {e}, falling back to standard REST API")

        try:
            return self.get_resource_list(
                "Item",
                fields=["name", "item_code", "item_name", "item_group", "stock_uom", "standard_rate", "is_stock_item", "description"]
            )
        except Exception as e:
            logger.warning(f"Failed fetching items with expanded fields: {e}, retrying with basic fields")
            return self.get_resource_list("Item", fields=["name", "item_code", "item_name", "standard_rate"])

    def get_inventory(self) -> List[Dict[str, Any]]:
        """Fetch inventory levels"""
        if self.config.use_havano_api:
            try:
                res = self.call_method("havano_pos_integration.api.get_inventory")
                if isinstance(res, list):
                    return res
                if isinstance(res, dict):
                    return res.get("inventory") or res.get("data") or []
            except Exception as e:
                logger.warning(f"Failed to fetch inventory via havano_pos_integration: {e}, falling back to Bin REST API")

        return self.get_resource_list("Bin", fields=["item_code", "warehouse", "actual_qty", "projected_qty"])

    def get_sales_invoices(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Fetch submitted sales invoices with cost center & warehouse information"""
        rest_invoices = []
        try:
            rest_invoices = self.get_resource_list(
                "Sales Invoice",
                fields=["name", "customer", "customer_name", "cost_center", "set_warehouse", "posting_date", "due_date", "grand_total", "net_total", "docstatus", "currency", "owner"],
                filters={"docstatus": 1},
                limit_page_length=limit
            )
        except Exception as e:
            logger.warning(f"Failed fetching Sales Invoice REST list with cost_center: {e}")

        rest_map = {inv["name"]: inv for inv in rest_invoices}

        if self.config.use_havano_api:
            try:
                invoices = self.call_method("havano_pos_integration.api.get_sales_invoice")
                if isinstance(invoices, list):
                    # Enrich custom API response with cost_center & set_warehouse from REST API
                    for inv in invoices:
                        inv_name = inv.get("name")
                        if inv_name and inv_name in rest_map:
                            rest_item = rest_map[inv_name]
                            if not inv.get("cost_center") and rest_item.get("cost_center"):
                                inv["cost_center"] = rest_item["cost_center"]
                            if not inv.get("set_warehouse") and rest_item.get("set_warehouse"):
                                inv["set_warehouse"] = rest_item["set_warehouse"]
                    return invoices
            except Exception as e:
                logger.warning(f"Failed to fetch sales invoices via havano_pos_integration: {e}, falling back to REST API")

        return rest_invoices
