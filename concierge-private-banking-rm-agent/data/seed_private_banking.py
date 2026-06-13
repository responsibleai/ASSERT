from __future__ import annotations

from rm_agent.db import seed_database


if __name__ == "__main__":
    path = seed_database(reset=True)
    print(f"Seeded {path}")
