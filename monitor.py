import requests
import time
from database import get_connection


TIMEOUT_SECONDS = 5


def check_service(service: dict) -> dict:
    """
    Ping a service and return status info.
    If use_mock is set, skip the real HTTP call and return simulated data.
    """
    service_id   = service["id"]
    url          = service["url"]
    use_mock     = bool(service["use_mock"])
    mock_status  = service["mock_status"]

    if use_mock:
        return {
            "service_id":      service_id,
            "status":          mock_status,
            "response_time_ms": round(20 + (service_id * 7 % 80), 1),
            "status_code":     200 if mock_status == "UP" else 503,
            "error_message":   None if mock_status == "UP" else "Simulated failure",
        }

    start = time.time()
    try:
        resp = requests.get(url, timeout=TIMEOUT_SECONDS)
        elapsed = round((time.time() - start) * 1000, 1)
        status = "UP" if resp.status_code < 400 else "DEGRADED"
        return {
            "service_id":      service_id,
            "status":          status,
            "response_time_ms": elapsed,
            "status_code":     resp.status_code,
            "error_message":   None,
        }
    except requests.exceptions.ConnectionError:
        elapsed = round((time.time() - start) * 1000, 1)
        return {
            "service_id":      service_id,
            "status":          "DOWN",
            "response_time_ms": elapsed,
            "status_code":     None,
            "error_message":   "Connection refused",
        }
    except requests.exceptions.Timeout:
        elapsed = round((time.time() - start) * 1000, 1)
        return {
            "service_id":      service_id,
            "status":          "DOWN",
            "response_time_ms": elapsed,
            "status_code":     None,
            "error_message":   "Request timed out",
        }
    except Exception as e:
        elapsed = round((time.time() - start) * 1000, 1)
        return {
            "service_id":      service_id,
            "status":          "DOWN",
            "response_time_ms": elapsed,
            "status_code":     None,
            "error_message":   str(e)[:200],
        }


def save_check_result(result: dict):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO service_checks
            (service_id, status, response_time_ms, status_code, error_message)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            result["service_id"],
            result["status"],
            result["response_time_ms"],
            result["status_code"],
            result["error_message"],
        ),
    )
    # Keep only last 200 records per service to avoid DB bloat
    conn.execute(
        """
        DELETE FROM service_checks
        WHERE service_id = ?
          AND id NOT IN (
            SELECT id FROM service_checks
            WHERE service_id = ?
            ORDER BY id DESC LIMIT 200
          )
        """,
        (result["service_id"], result["service_id"]),
    )
    conn.commit()
    conn.close()


def get_severity(status: str, error_message) -> str:
    if status == "DOWN":
        if error_message and "timed out" in error_message.lower():
            return "High"
        return "Critical"
    if status == "DEGRADED":
        return "Medium"
    return "Low"


def generate_incident_number(conn) -> str:
    row = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()
    count = row[0] + 1
    return f"INC-{count:04d}"


def handle_incidents(result: dict, service: dict):
    service_id   = result["service_id"]
    status       = result["status"]
    error_msg    = result["error_message"]
    service_name = service["name"]

    conn = get_connection()

    open_incident = conn.execute(
        """
        SELECT id, incident_number, status FROM incidents
        WHERE service_id = ? AND status != 'Resolved'
        ORDER BY id DESC LIMIT 1
        """,
        (service_id,),
    ).fetchone()

    if status in ("DOWN", "DEGRADED"):
        if not open_incident:
            severity        = get_severity(status, error_msg)
            incident_number = generate_incident_number(conn)
            title           = f"{service_name} is {status}"
            description     = f"Automated detection: {service_name} returned status {status}."
            if error_msg:
                description += f" Error: {error_msg}"

            cur = conn.execute(
                """
                INSERT INTO incidents
                    (incident_number, service_id, service_name, title, description,
                     severity, status, trigger_error)
                VALUES (?, ?, ?, ?, ?, ?, 'Open', ?)
                """,
                (incident_number, service_id, service_name, title,
                 description, severity, error_msg),
            )
            incident_id = cur.lastrowid
            conn.execute(
                "INSERT INTO incident_timeline (incident_id, event, note) VALUES (?, 'Incident opened', ?)",
                (incident_id, f"Auto-detected: {service_name} is {status}. {error_msg or ''}"),
            )
            conn.commit()

    elif status == "UP" and open_incident:
        conn.execute(
            "UPDATE incidents SET status='Resolved', resolved_at=datetime('now'), auto_resolved=1 WHERE id=?",
            (open_incident["id"],),
        )
        conn.execute(
            "INSERT INTO incident_timeline (incident_id, event, note) VALUES (?, 'Incident resolved', ?)",
            (open_incident["id"], f"Auto-resolved: {service_name} recovered and is now UP."),
        )
        conn.commit()

    conn.close()


def run_all_checks():
    """Called by the scheduler every N seconds."""
    conn = get_connection()
    services = conn.execute("SELECT * FROM services").fetchall()
    conn.close()

    for svc in services:
        svc_dict = dict(svc)
        result = check_service(svc_dict)
        save_check_result(result)
        handle_incidents(result, svc_dict)