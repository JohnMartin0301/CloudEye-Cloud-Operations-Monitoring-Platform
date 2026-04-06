import requests
import time
import random
from datetime import datetime
from database import get_connection


TIMEOUT_SECONDS = 3

# ── Simulation state (in-memory) ─────────────────
_sim_status      = {}   # service_id → current simulated status code
_sim_last_change = {}   # service_id → datetime of last state change


def _simulate_status(service_id: int, current_mock_status: str) -> str:
    """
    Subtle random failure simulation for mock services.
    - 3% chance a healthy service fails per cycle
    - Minimum 2 min healthy before it can fail
    - Minimum 1 min down before it can recover
    - Forced recovery after 5 min maximum downtime
    """
    now = datetime.now()

    if service_id not in _sim_status:
        if current_mock_status == "UP":
            _sim_status[service_id] = 200
        elif current_mock_status == "DEGRADED":
            _sim_status[service_id] = 503
        else:  # DOWN
            _sim_status[service_id] = 500
        _sim_last_change[service_id] = now

    code             = _sim_status[service_id]
    last_change      = _sim_last_change[service_id]
    seconds_in_state = (now - last_change).total_seconds()

    if code == 200:
        if seconds_in_state >= 120 and random.random() < 0.03:
            _sim_status[service_id]      = random.choice([500, 503, 504])
            _sim_last_change[service_id] = now
    else:
        if seconds_in_state >= 60:
            if seconds_in_state >= 300 or random.random() < 0.30:
                _sim_status[service_id]      = 200
                _sim_last_change[service_id] = now

    final_code = _sim_status[service_id]
    if final_code == 200:
        return "UP"
    elif final_code == 503:
        return "DEGRADED"
    return "DOWN"


def check_service(service: dict) -> dict:
    service_id  = service["id"]
    url         = service["url"]
    use_mock    = bool(service["use_mock"])
    mock_status = service["mock_status"]

    if use_mock:
        simulated = _simulate_status(service_id, mock_status)
        return {
            "service_id":       service_id,
            "status":           simulated,
            "response_time_ms": round(10 + random.uniform(5, 60), 1),
            "status_code":      200 if simulated == "UP" else (503 if simulated == "DEGRADED" else 500),
            "error_message":    None if simulated == "UP" else "Simulated failure",
        }

    start = time.time()
    try:
        resp    = requests.get(url, timeout=TIMEOUT_SECONDS)
        elapsed = round((time.time() - start) * 1000, 1)
        status  = "UP" if resp.status_code < 400 else "DEGRADED"
        return {
            "service_id":       service_id,
            "status":           status,
            "response_time_ms": elapsed,
            "status_code":      resp.status_code,
            "error_message":    None,
        }
    except requests.exceptions.ConnectionError:
        elapsed = round((time.time() - start) * 1000, 1)
        return {
            "service_id": service_id, "status": "DOWN",
            "response_time_ms": elapsed, "status_code": None,
            "error_message": "Connection refused",
        }
    except requests.exceptions.Timeout:
        elapsed = round((time.time() - start) * 1000, 1)
        return {
            "service_id": service_id, "status": "DOWN",
            "response_time_ms": elapsed, "status_code": None,
            "error_message": "Request timed out",
        }
    except Exception as e:
        elapsed = round((time.time() - start) * 1000, 1)
        return {
            "service_id": service_id, "status": "DOWN",
            "response_time_ms": elapsed, "status_code": None,
            "error_message": str(e)[:200],
        }


def save_check_result(result: dict):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO service_checks
            (service_id, status, response_time_ms, status_code, error_message)
        VALUES (?, ?, ?, ?, ?)
        """,
        (result["service_id"], result["status"], result["response_time_ms"],
         result["status_code"], result["error_message"]),
    )
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
    row   = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()
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
    conn     = get_connection()
    services = conn.execute("SELECT * FROM services").fetchall()
    conn.close()

    for svc in services:
        svc_dict = dict(svc)
        result   = check_service(svc_dict)
        save_check_result(result)
        handle_incidents(result, svc_dict)