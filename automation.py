from database import get_connection


METRIC_LABELS = {
    "cpu_percent":    "CPU Usage",
    "memory_percent": "Memory Usage",
    "disk_percent":   "Disk Usage",
}

COOLDOWN_SECONDS = 300  # 5 min between repeated triggers for same rule


def get_enabled_rules() -> list:
    conn  = get_connection()
    rows  = conn.execute(
        "SELECT * FROM automation_rules WHERE enabled=1 ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def was_recently_triggered(rule_id: int) -> bool:
    """Prevent the same rule from firing more than once every 5 minutes."""
    conn = get_connection()
    row  = conn.execute(
        """
        SELECT triggered_at FROM automation_events
        WHERE rule_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (rule_id,),
    ).fetchone()
    conn.close()
    if not row:
        return False
    from datetime import datetime
    try:
        last = datetime.fromisoformat(row["triggered_at"])
        diff = (datetime.utcnow() - last).total_seconds()
        return diff < COOLDOWN_SECONDS
    except Exception:
        return False


def generate_incident_number(conn) -> str:
    row   = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()
    count = row[0] + 1
    return f"INC-{count:04d}"


def run_automation_rules(metrics: dict):
    """
    Called after every metrics collection.
    Checks each enabled rule against the latest metrics snapshot.
    Creates a health incident if a threshold is breached.
    """
    if not metrics:
        return

    rules = get_enabled_rules()

    for rule in rules:
        metric    = rule["metric"]
        threshold = rule["threshold"]
        value     = metrics.get(metric)

        if value is None:
            continue

        if value < threshold:
            continue

        if was_recently_triggered(rule["id"]):
            continue

        # Threshold breached — create incident
        conn            = get_connection()
        label           = METRIC_LABELS.get(metric, metric)
        incident_number = generate_incident_number(conn)
        title           = f"Automation: {rule['name']} threshold breached"
        description     = (
            f"{label} is at {value:.1f}%, exceeding the "
            f"configured threshold of {threshold:.0f}%. "
            f"Rule: {rule['name']}."
        )

        cur = conn.execute(
            """
            INSERT INTO incidents
                (incident_number, service_id, service_name, title, description,
                 severity, status, trigger_error)
            VALUES (?, 0, 'System', ?, ?, ?, 'Open', ?)
            """,
            (
                incident_number,
                title,
                description,
                rule["severity"],
                f"{label} = {value:.1f}% (threshold: {threshold:.0f}%)",
            ),
        )
        incident_id = cur.lastrowid

        conn.execute(
            "INSERT INTO incident_timeline (incident_id, event, note) VALUES (?,?,?)",
            (
                incident_id,
                "Incident opened",
                f"Automation rule '{rule['name']}' triggered. "
                f"{label} reached {value:.1f}% (threshold: {threshold:.0f}%).",
            ),
        )

        # Log the automation event
        conn.execute(
            """
            INSERT INTO automation_events
                (rule_id, rule_name, metric, value, threshold, action_taken, incident_id)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                rule["id"],
                rule["name"],
                metric,
                value,
                threshold,
                f"Created incident {incident_number}",
                incident_id,
            ),
        )

        conn.commit()
        conn.close()