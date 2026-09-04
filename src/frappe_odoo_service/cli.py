import sys
import time
import logging
import click
from typing import Optional

from frappe_odoo_service.config import AppConfig, TenantConfig
from frappe_odoo_service.clients.frappe_client import FrappeClient
from frappe_odoo_service.clients.odoo_client import OdooClient
from frappe_odoo_service.db.state_store import StateStore

from frappe_odoo_service.sync.stores import StoreSyncer
from frappe_odoo_service.sync.users import UserSyncer
from frappe_odoo_service.sync.customers import CustomerSyncer
from frappe_odoo_service.sync.products import ProductSyncer
from frappe_odoo_service.sync.pricelists import PricelistSyncer
from frappe_odoo_service.sync.inventory import InventorySyncer
from frappe_odoo_service.sync.sales import SalesSyncer
from frappe_odoo_service.sync.payments import PaymentSyncer


def setup_logging(level_name: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


@click.group()
@click.option("--config", "-c", default="config.yaml", help="Path to config YAML file")
@click.pass_context
def main(ctx, config: str):
    """Frappe to Odoo Sync Service CLI"""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config


def sync_tenant(tenant: TenantConfig, state_store: StateStore, entity: str = "all"):
    logger = logging.getLogger("frappe_odoo_service")
    tenant_id = tenant.get_tenant_id()
    logger.info(f"=== Starting Sync for Tenant: {tenant_id} (Entity: {entity}) ===")

    frappe_client = FrappeClient(tenant.frappe)
    odoo_client = OdooClient(tenant.odoo)

    if entity in ("all", "stores"):
        logger.info("Syncing Stores (Cost Centers)...")
        StoreSyncer(tenant, frappe_client, odoo_client, state_store).sync()

    if entity in ("all", "users"):
        logger.info("Syncing Users...")
        UserSyncer(tenant, frappe_client, odoo_client, state_store).sync()

    if entity in ("all", "customers"):
        logger.info("Syncing Customers...")
        CustomerSyncer(tenant, frappe_client, odoo_client, state_store).sync()

    if entity in ("all", "products"):
        logger.info("Syncing Products...")
        ProductSyncer(tenant, frappe_client, odoo_client, state_store).sync()

    if entity in ("all", "pricelists"):
        logger.info("Syncing Pricelists...")
        PricelistSyncer(tenant, frappe_client, odoo_client, state_store).sync()

    if entity in ("all", "inventory"):
        logger.info("Syncing Inventory...")
        InventorySyncer(tenant, frappe_client, odoo_client, state_store).sync()

    if entity in ("all", "sales"):
        logger.info("Syncing Sales Invoices...")
        SalesSyncer(tenant, frappe_client, odoo_client, state_store).sync()

    if entity in ("all", "payments"):
        logger.info("Syncing Payments...")
        PaymentSyncer(tenant, frappe_client, odoo_client, state_store).sync()

    logger.info(f"=== Finished Sync for Tenant: {tenant_id} ===")


@main.command()
@click.option("--tenant", "-t", help="Tenant ID to sync")
@click.option("--entity", "-e", default="all", type=click.Choice(["all", "stores", "users", "customers", "products", "pricelists", "inventory", "sales", "payments"]))
@click.pass_context
def sync(ctx, tenant: Optional[str], entity: str):
    """Run one-off sync for specified tenant or all enabled tenants"""
    config_path = ctx.obj["config_path"]
    app_config = AppConfig.load_from_yaml(config_path)
    setup_logging(app_config.sync_engine.log_level)

    state_store = StateStore(app_config.sync_engine.state_db_path)

    tenants_to_sync = []
    for t in app_config.tenants:
        if not t.enabled:
            continue
        if tenant and t.get_tenant_id() != tenant:
            continue
        tenants_to_sync.append(t)

    if not tenants_to_sync:
        click.echo(f"No active tenants found matching tenant_id='{tenant}'")
        return

    for t in tenants_to_sync:
        sync_tenant(t, state_store, entity)


@main.command()
@click.pass_context
def daemon(ctx):
    """Run sync service as a background daemon loop"""
    config_path = ctx.obj["config_path"]
    app_config = AppConfig.load_from_yaml(config_path)
    setup_logging(app_config.sync_engine.log_level)
    logger = logging.getLogger("frappe_odoo_service.daemon")

    interval = app_config.sync_engine.poll_interval_seconds
    state_store = StateStore(app_config.sync_engine.state_db_path)

    logger.info(f"Starting Frappe-to-Odoo Sync Daemon (Interval: {interval}s)")

    while True:
        try:
            for t in app_config.tenants:
                if t.enabled:
                    sync_tenant(t, state_store, "all")
        except Exception as e:
            logger.error(f"Error during daemon cycle: {e}")

        logger.info(f"Sleeping for {interval} seconds...")
        time.sleep(interval)


@main.command()
@click.pass_context
def status(ctx):
    """Show sync state DB statistics and mapping counts"""
    config_path = ctx.obj["config_path"]
    app_config = AppConfig.load_from_yaml(config_path)
    state_store = StateStore(app_config.sync_engine.state_db_path)

    with state_store._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT tenant_id, frappe_doctype, count(*) as cnt FROM entity_mapping GROUP BY tenant_id, frappe_doctype")
        rows = cursor.fetchall()
        click.echo("=== Entity Mapping Summary ===")
        for r in rows:
            click.echo(f"Tenant: {r['tenant_id']} | Doctype: {r['frappe_doctype']} | Mapped Count: {r['cnt']}")

        cursor.execute("SELECT tenant_id, entity_type, status, records_synced, finished_at FROM sync_logs ORDER BY id DESC LIMIT 10")
        logs = cursor.fetchall()
        click.echo("\n=== Recent Sync Logs ===")
        for l in logs:
            click.echo(f"[{l['finished_at']}] Tenant: {l['tenant_id']} | Entity: {l['entity_type']} | Status: {l['status']} | Synced: {l['records_synced']}")


if __name__ == "__main__":
    main()
