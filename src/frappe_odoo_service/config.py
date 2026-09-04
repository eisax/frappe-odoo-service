import os
import yaml
from typing import List, Optional
from pydantic import BaseModel, Field


class FrappeConfig(BaseModel):
    base_url: str
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    use_havano_api: bool = True
    use_saas_api: bool = True

    @property
    def auth_header(self) -> Optional[str]:
        if self.api_key and self.api_secret:
            return f"token {self.api_key}:{self.api_secret}"
        return None


class OdooConfig(BaseModel):
    url: str
    db: str
    username: str
    password: str
    protocol: str = "xmlrpc"


class TenantConfig(BaseModel):
    tenant_id: Optional[str] = None
    enabled: bool = True
    frappe: FrappeConfig
    odoo: OdooConfig

    def get_tenant_id(self) -> str:
        if self.tenant_id:
            return self.tenant_id
        # Default derived tenant id from frappe base_url host and odoo db
        from urllib.parse import urlparse
        host = urlparse(self.frappe.base_url).netloc.replace(":", "_")
        return f"{host}_{self.odoo.db}"


class SyncEngineConfig(BaseModel):
    poll_interval_seconds: int = 300
    state_db_path: str = "sync_state.db"
    batch_size: int = 50
    log_level: str = "INFO"


class AppConfig(BaseModel):
    version: str = "1.0"
    sync_engine: SyncEngineConfig = Field(default_factory=SyncEngineConfig)
    tenants: List[TenantConfig] = Field(default_factory=list)

    @classmethod
    def load_from_yaml(cls, filepath: str) -> "AppConfig":
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Configuration file not found: {filepath}")

        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return cls(**data)
