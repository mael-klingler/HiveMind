import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("DB_PATH", "/app/data/orchestrator.db"))

def get_db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _add_column_if_not_exists(cursor, table: str, column: str, definition: str):
    """Adds a column if it does not already exist (for migrations)."""
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")