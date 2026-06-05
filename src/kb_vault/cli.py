import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import Config
from .database import init_vault_db
from .process import scan_and_index_vaults

console = Console()
config = Config()

kb_vault_cli = typer.Typer(
    help="CLI for `kb-vault` - scan and track Obsidian markdown vaults in the kb stack."
)


@kb_vault_cli.command("add")
def add_path(path: str = typer.Argument(..., help="Path of directory to add to vault scan config")):
    """Add a directory to the vault scan paths list (scans recursively for .obsidian)."""
    resolved_path = Path(path).resolve()
    if not resolved_path.is_dir():
        console.print(f"[bold red]Error: Path is not a valid directory: {path}[/bold red]")
        raise typer.Exit(code=1)

    vault_config = config.load_vault_config()
    scan_paths = vault_config.get("scan_paths", [])

    path_str = resolved_path.as_posix()
    if path_str in scan_paths:
        console.print(f"[yellow]Path is already configured to be scanned: {path_str}[/yellow]")
        return

    scan_paths.append(path_str)
    vault_config["scan_paths"] = scan_paths
    config.save_vault_config(vault_config)
    console.print(f"[green]Successfully added path to scan list: {path_str}[/green]")


@kb_vault_cli.command("remove")
def remove_path(path: str = typer.Argument(..., help="Path of directory to remove from scan config")):
    """Remove a directory from the vault scan paths list."""
    resolved_path = Path(path).resolve()
    path_str = resolved_path.as_posix()

    vault_config = config.load_vault_config()
    scan_paths = vault_config.get("scan_paths", [])

    if path_str not in scan_paths:
        # Fallback to direct string match
        path_str = path

    if path_str not in scan_paths:
        console.print(f"[bold red]Error: Path not found in scan config: {path_str}[/bold red]")
        raise typer.Exit(code=1)

    scan_paths.remove(path_str)
    vault_config["scan_paths"] = scan_paths
    config.save_vault_config(vault_config)
    console.print(f"[green]Successfully removed path from scan list: {path_str}[/green]")


@kb_vault_cli.command("list-paths")
def list_paths():
    """List all directories configured to be scanned for Obsidian vaults."""
    vault_config = config.load_vault_config()
    scan_paths = vault_config.get("scan_paths", [])

    if not scan_paths:
        console.print("[yellow]No scan paths configured. Use 'kb-vault add <path>' to add one.[/yellow]")
        return

    console.print("[bold cyan]Configured Vault Scan Paths:[/bold cyan]")
    for p in scan_paths:
        console.print(f" - {p}")


@kb_vault_cli.command("scan")
def scan(
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Run scan without database writes.")
):
    """Scan configured paths recursively for Obsidian vaults and index markdown file metadata/contents."""
    db = config.get_db()
    if not dry_run:
        init_vault_db(db)

    vault_config = config.load_vault_config()
    scan_paths = vault_config.get("scan_paths", [])

    if not scan_paths:
        console.print("[bold red]No scan paths configured. Please run 'kb-vault add <path>' first.[/bold red]")
        raise typer.Exit(code=1)

    console.print("[bold blue]Starting recursive scan of paths for Obsidian vaults...[/bold blue]")
    count = scan_and_index_vaults(scan_paths, db, dry_run=dry_run)
    console.print(f"[bold green]Scan finished. Found and indexed {count} vaults.[/bold green]")


@kb_vault_cli.command("list")
def list_vaults():
    """List all tracked Obsidian vaults and their indexed file counts."""
    db = config.get_db()
    init_vault_db(db)

    vaults = db.execute_returning_dicts("SELECT * FROM vaults ORDER BY files DESC")
    if not vaults:
        console.print("[yellow]No vaults tracked in database. Run 'kb-vault scan' to find vaults.[/yellow]")
        return

    table = Table(title="Tracked Obsidian Vaults")
    table.add_column("Name", style="cyan")
    table.add_column("Path", style="magenta")
    table.add_column("Files Indexed", justify="right", style="green")

    for v in vaults:
        table.add_row(v["name"], v["path"], str(v["files"]))

    console.print(table)


if __name__ == "__main__":
    kb_vault_cli()
