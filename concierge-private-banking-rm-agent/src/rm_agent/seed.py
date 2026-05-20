from __future__ import annotations

import typer
from rich.console import Console

from .db import seed_database


app = typer.Typer(help="Seed mock private banking database")
console = Console()


@app.command()
def main() -> None:
    path = seed_database(reset=True)
    console.print(f"Seeded database at: {path}")


if __name__ == "__main__":
    app()
