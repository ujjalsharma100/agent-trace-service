#!/usr/bin/env python3
"""
Database initialization and management for agent-trace-service.

Reads individual .sql files from the sql/ directory and applies them in the
correct order to create (or reset) the schema.

Usage:
    python init_db.py create   — create all tables
    python init_db.py drop     — drop all tables (asks for confirmation)
    python init_db.py reset    — drop + recreate (asks for confirmation)
    python init_db.py status   — show row counts
"""

import argparse
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def _build_database_url() -> str:
    """Build a PostgreSQL connection URL from individual env vars."""
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    user = os.environ.get("DB_USER", "postgres")
    password = os.environ.get("DB_PASSWORD", "postgres")
    name = os.environ.get("DB_NAME", "agent_trace")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"

# Order matters — tables with foreign keys come after the tables they reference.
SQL_FILES = [
    "projects.sql",
    "traces.sql",
    "conversation_contents.sql",
    "commit_links.sql",
]

SQL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql")


def get_connection(database_url=None):
    url = database_url or _build_database_url()
    return psycopg2.connect(url)


def create_tables(conn):
    """Run each SQL file in order to create all tables and indexes."""
    for filename in SQL_FILES:
        path = os.path.join(SQL_DIR, filename)
        if not os.path.exists(path):
            print(f"  WARNING: {path} not found, skipping.")
            continue
        with open(path) as f:
            sql = f.read()
        with conn.cursor() as cur:
            cur.execute(sql)
        print(f"  Applied {filename}")
    conn.commit()
    print("Tables created successfully.")


def drop_tables(conn):
    """Drop all application tables."""
    with conn.cursor() as cur:
        cur.execute("""
            DROP TABLE IF EXISTS commit_links CASCADE;
            DROP TABLE IF EXISTS conversation_contents CASCADE;
            DROP TABLE IF EXISTS traces CASCADE;
            DROP TABLE IF EXISTS projects CASCADE;
        """)
    conn.commit()
    print("All tables dropped.")


def reset_tables(conn):
    """Drop and recreate all tables."""
    drop_tables(conn)
    create_tables(conn)
    print("Database reset complete.")


def show_status(conn):
    """Print row counts for each table."""
    tables = ["projects", "traces", "conversation_contents", "commit_links"]
    print("Database status:\n")
    with conn.cursor() as cur:
        for table in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
                print(f"  {table}: {count} rows")
            except psycopg2.Error:
                conn.rollback()
                print(f"  {table}: table does not exist")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="agent-trace-service database management",
    )
    parser.add_argument(
        "command",
        choices=["create", "drop", "reset", "status"],
        help="Command to execute",
    )
    parser.add_argument(
        "--database-url",
        help="Override database URL (built from DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME by default)",
    )

    args = parser.parse_args()
    conn = get_connection(args.database_url)

    try:
        if args.command == "create":
            create_tables(conn)

        elif args.command == "drop":
            answer = input("Are you sure you want to drop all tables? (yes/no): ")
            if answer.strip().lower() == "yes":
                drop_tables(conn)
            else:
                print("Cancelled.")

        elif args.command == "reset":
            answer = input("Are you sure you want to reset the database? This deletes all data. (yes/no): ")
            if answer.strip().lower() == "yes":
                reset_tables(conn)
            else:
                print("Cancelled.")

        elif args.command == "status":
            show_status(conn)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
