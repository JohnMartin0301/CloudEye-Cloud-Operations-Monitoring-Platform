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


def run_all_checks():
    """Called by the scheduler every N seconds."""
    conn = get_connection()
    services = conn.execute("SELECT * FROM services").fetchall()
    conn.close()

    for svc in services:
        result = check_service(dict(svc))
        save_check_result(result)