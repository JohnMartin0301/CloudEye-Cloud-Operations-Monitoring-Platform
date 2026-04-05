import sqlite3
import os

# Use /app/data when running in Docker (volume-mounted), fall back to local dir
_DATA_DIR = "/app/data" if os.path.isdir("/app/data") else os.path.dirname(__file__)
DB_PATH = os.path.join(_DATA_DIR, "cloudeye.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
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
            ("Cloudflare DNS", "https://one.one.one.one",           0, "UP"),
            ("Google DNS",     "https://8.8.8.8",                   0, "UP"),
            ("GitHub",         "https://github.com",                 0, "UP"),
            ("Auth Service",   "http://internal/api/mock/auth",      1, "UP"),
            ("Cache Service",  "http://internal/api/mock/cache",     1, "DOWN"),
            ("Database",       "http://internal/api/mock/database",  1, "UP"),
        ]
        cur.executemany(
            "INSERT INTO services (name, url, use_mock, mock_status) VALUES (?,?,?,?)",
            default_services
        )

    cur.execute("""
        CREATE TABLE IF NOT EXISTS log_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            file_size_bytes INTEGER,
            total_lines INTEGER,
            parsed_lines INTEGER,
            count_info INTEGER DEFAULT 0,
            count_warning INTEGER DEFAULT 0,
            count_error INTEGER DEFAULT 0,
            count_debug INTEGER DEFAULT 0,
            count_critical INTEGER DEFAULT 0,
            most_frequent_issue TEXT,
            time_range_from TEXT,
            time_range_to TEXT,
            result_json TEXT,
            uploaded_at TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_number TEXT NOT NULL UNIQUE,
            service_id INTEGER NOT NULL,
            service_name TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            severity TEXT NOT NULL DEFAULT 'Medium',
            status TEXT NOT NULL DEFAULT 'Open',
            trigger_error TEXT,
            detected_at TEXT DEFAULT (datetime('now')),
            acknowledged_at TEXT,
            resolved_at TEXT,
            auto_resolved INTEGER DEFAULT 0,
            FOREIGN KEY (service_id) REFERENCES services(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS incident_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id INTEGER NOT NULL,
            event TEXT NOT NULL,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (incident_id) REFERENCES incidents(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cpu_percent REAL,
            memory_percent REAL,
            memory_used_mb REAL,
            memory_total_mb REAL,
            disk_percent REAL,
            disk_used_gb REAL,
            disk_total_gb REAL,
            net_bytes_sent_mb REAL,
            net_bytes_recv_mb REAL,
            net_packets_sent INTEGER,
            net_packets_recv INTEGER,
            recorded_at TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS automation_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            rule_type TEXT NOT NULL,
            metric TEXT NOT NULL,
            threshold REAL NOT NULL,
            severity TEXT NOT NULL DEFAULT 'High',
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS automation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER NOT NULL,
            rule_name TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            threshold REAL NOT NULL,
            action_taken TEXT NOT NULL,
            incident_id INTEGER,
            triggered_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (rule_id) REFERENCES automation_rules(id)
        )
    """)

    # Seed default automation rules
    cur.execute("SELECT COUNT(*) FROM automation_rules")
    if cur.fetchone()[0] == 0:
        default_rules = [
            # CPU
            ("High CPU Usage",        "threshold", "cpu_percent",    80.0, "High"),
            ("Critical CPU Usage",    "threshold", "cpu_percent",    90.0, "Critical"),
            # Memory
            ("High Memory Usage",     "threshold", "memory_percent", 85.0, "High"),
            ("Critical Memory Usage", "threshold", "memory_percent", 90.0, "Critical"),
            # Disk
            ("High Disk Usage",       "threshold", "disk_percent",   85.0, "High"),
            ("Critical Disk Usage",   "threshold", "disk_percent",   90.0, "Critical"),
        ]
        cur.executemany(
            "INSERT INTO automation_rules (name, rule_type, metric, threshold, severity) VALUES (?,?,?,?,?)",
            default_rules
        )

    conn.commit()
    conn.close()