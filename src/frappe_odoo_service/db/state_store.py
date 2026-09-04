import sqlite3
import datetime
from typing import Optional, Dict, Any


class StateStore:
    def __init__(self, db_path: str = "sync_state.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS entity_mapping (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    frappe_doctype TEXT NOT NULL,
                    frappe_id TEXT NOT NULL,
                    odoo_model TEXT NOT NULL,
                    odoo_id INTEGER NOT NULL,
                    last_synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    checksum TEXT,
                    UNIQUE(tenant_id, frappe_doctype, frappe_id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sync_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    records_synced INTEGER DEFAULT 0,
                    error_message TEXT,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP
                )
            """)
            conn.commit()

    def get_odoo_id(self, tenant_id: str, frappe_doctype: str, frappe_id: str) -> Optional[int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT odoo_id FROM entity_mapping
                WHERE tenant_id = ? AND frappe_doctype = ? AND frappe_id = ?
                """,
                (tenant_id, frappe_doctype, frappe_id),
            )
            row = cursor.fetchone()
            return row["odoo_id"] if row else None

    def get_frappe_id(self, tenant_id: str, odoo_model: str, odoo_id: int) -> Optional[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT frappe_id FROM entity_mapping
                WHERE tenant_id = ? AND odoo_model = ? AND odoo_id = ?
                """,
                (tenant_id, odoo_model, odoo_id),
            )
            row = cursor.fetchone()
            return row["frappe_id"] if row else None

    def save_mapping(
        self,
        tenant_id: str,
        frappe_doctype: str,
        frappe_id: str,
        odoo_model: str,
        odoo_id: int,
        checksum: Optional[str] = None,
    ):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.datetime.utcnow().isoformat()
            cursor.execute(
                """
                INSERT INTO entity_mapping 
                    (tenant_id, frappe_doctype, frappe_id, odoo_model, odoo_id, last_synced_at, checksum)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, frappe_doctype, frappe_id) DO UPDATE SET
                    odoo_model = excluded.odoo_model,
                    odoo_id = excluded.odoo_id,
                    last_synced_at = excluded.last_synced_at,
                    checksum = excluded.checksum
                """,
                (tenant_id, frappe_doctype, frappe_id, odoo_model, odoo_id, now, checksum),
            )
            conn.commit()

    def log_sync_start(self, tenant_id: str, entity_type: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO sync_logs (tenant_id, entity_type, status, started_at)
                VALUES (?, ?, 'RUNNING', ?)
                """,
                (tenant_id, entity_type, datetime.datetime.utcnow().isoformat()),
            )
            conn.commit()
            return cursor.lastrowid

    def log_sync_complete(self, log_id: int, records_synced: int, status: str = "SUCCESS", error_message: Optional[str] = None):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE sync_logs
                SET status = ?, records_synced = ?, error_message = ?, finished_at = ?
                WHERE id = ?
                """,
                (status, records_synced, error_message, datetime.datetime.utcnow().isoformat(), log_id),
            )
            conn.commit()
