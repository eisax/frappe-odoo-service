import xmlrpc.client
import logging
from typing import Any, Dict, List, Optional, Union
from frappe_odoo_service.config import OdooConfig

logger = logging.getLogger(__name__)


class OdooClient:
    """
    Client for interacting with Odoo via XML-RPC.
    Does NOT modify any code on the Odoo server.
    """

    def __init__(self, config: OdooConfig):
        self.config = config
        self.url = config.url.rstrip("/")
        self.db = config.db
        self.username = config.username
        self.password = config.password
        self._uid: Optional[int] = None
        self._common: Optional[xmlrpc.client.ServerProxy] = None
        self._models: Optional[xmlrpc.client.ServerProxy] = None
        self.tenant_id: Optional[int] = None
        self.store_ids: List[int] = []

    def _clean_val(self, val: Any) -> Any:
        if val is None:
            return False
        if isinstance(val, dict):
            return {k: self._clean_val(v) for k, v in val.items()}
        if isinstance(val, list):
            return [self._clean_val(v) for v in val]
        return val

    def connect(self) -> int:
        if self._uid is not None:
            return self._uid

        common_url = f"{self.url}/xmlrpc/2/common"
        models_url = f"{self.url}/xmlrpc/2/object"

        self._common = xmlrpc.client.ServerProxy(common_url, allow_none=True)
        self._models = xmlrpc.client.ServerProxy(models_url, allow_none=True)

        try:
            self._uid = self._common.authenticate(
                self.db, self.username, self.password, {}
            )
            if not self._uid:
                raise PermissionError(f"Failed to authenticate Odoo user '{self.username}' against DB '{self.db}'")
            logger.info(f"Successfully authenticated with Odoo DB '{self.db}' as UID {self._uid}")

            # Auto-detect tenant_id and store_ids if havanoposdesk_odoo is installed
            self._resolve_tenant_and_stores()

            return self._uid
        except Exception as e:
            logger.error(f"Error authenticating with Odoo at {self.url}: {e}")
            raise

    def _resolve_tenant_and_stores(self):
        # 1. Try reading tenant_id from res.users
        try:
            user_data = self.search_read("res.users", [("id", "=", self._uid)], fields=["id", "tenant_id"])
            if user_data and user_data[0].get("tenant_id"):
                t = user_data[0]["tenant_id"]
                self.tenant_id = t[0] if isinstance(t, (list, tuple)) else t
                logger.info(f"Resolved Odoo tenant_id from user: {self.tenant_id}")
        except Exception as e:
            logger.debug(f"res.users has no tenant_id: {e}")

        # 2. Fallback to searching havanoposdesk.tenant
        if not self.tenant_id:
            try:
                tenants = self.search_read("havanoposdesk.tenant", [], fields=["id", "name"], limit=1)
                if tenants:
                    self.tenant_id = tenants[0]["id"]
                    logger.info(f"Resolved Odoo tenant_id from havanoposdesk.tenant: {self.tenant_id}")
            except Exception as e:
                logger.debug(f"havanoposdesk.tenant model not available: {e}")

        # 3. Search havanoposdesk.store for store_ids
        if self.tenant_id:
            try:
                stores = self.search_read("havanoposdesk.store", [("tenant_id", "=", self.tenant_id)], fields=["id", "name"])
                self.store_ids = [s["id"] for s in stores]
                logger.info(f"Resolved Odoo store_ids: {self.store_ids}")
            except Exception as e:
                logger.debug(f"havanoposdesk.store model not available: {e}")

    def model_exists(self, model: str) -> bool:
        try:
            self.search_read(model, [], fields=["id"], limit=1)
            return True
        except Exception:
            return False

    def execute_kw(self, model: str, method: str, args: List[Any], kwargs: Optional[Dict[str, Any]] = None) -> Any:
        uid = self.connect()
        clean_args = self._clean_val(args)
        clean_kwargs = self._clean_val(kwargs) if kwargs else {}
        return self._models.execute_kw(
            self.db, uid, self.password, model, method, clean_args, clean_kwargs
        )

    def search_read(
        self,
        model: str,
        domain: List[Any],
        fields: Optional[List[str]] = None,
        offset: int = 0,
        limit: Optional[int] = None,
        order: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        kwargs: Dict[str, Any] = {"offset": offset}
        if fields:
            kwargs["fields"] = fields
        if limit:
            kwargs["limit"] = limit
        if order:
            kwargs["order"] = order

        return self.execute_kw(model, "search_read", [domain], kwargs)

    def search(self, model: str, domain: List[Any]) -> List[int]:
        return self.execute_kw(model, "search", [domain])

    def create(self, model: str, values: Dict[str, Any]) -> int:
        res = self.execute_kw(model, "create", [values])
        logger.info(f"Created {model} record with ID {res}")
        return res

    def write(self, model: str, ids: Union[int, List[int]], values: Dict[str, Any]) -> bool:
        if isinstance(ids, int):
            ids = [ids]
        res = self.execute_kw(model, "write", [ids, values])
        logger.info(f"Updated {model} record(s) {ids}")
        return res
