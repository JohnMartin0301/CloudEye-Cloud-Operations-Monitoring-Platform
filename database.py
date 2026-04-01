import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "cloudeye.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            use_mock INTEGER DEFAULT 0,
            mock_status TEXT DEFAULT 'UP',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS service_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            response_time_ms REAL,
            status_code INTEGER,
            error_message TEXT,
            checked_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (service_id) REFERENCES services(id)
        )
    """)

    # Seed default services if empty
    cur.execute("SELECT COUNT(*) FROM services")
    count = cur.fetchone()[0]
    if count == 0:
        default_services = [
            ("GitHub",        "https://github.com",       0, "UP"),
            ("Cloudflare DNS","https://one.one.one.one",  0, "UP"),
            ("Auth API",      "https://httpbin.org/get",  0, "UP"),
            ("Payment API",   "https://httpbin.org/status/200", 0, "UP"),
            ("User Service",  "http://localhost:9999",    1, "DOWN"),
            ("Cache Service", "http://localhost:9998",    1, "UP"),
        ]
        cur.executemany(
            "INSERT INTO services (name, url, use_mock, mock_status) VALUES (?,?,?,?)",
            default_services
        )

    conn.commit()
    conn.close()