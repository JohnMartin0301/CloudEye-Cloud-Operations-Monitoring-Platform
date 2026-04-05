from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import os
import json
import threading

from database import init_db, get_connection
from monitor import run_all_checks, check_service
from log_parser import parse_logs
from metrics import run_metrics_collection, collect_metrics


# Pydantic schemas
class ServiceCreate(BaseModel):
    name: str
    url: str
    use_mock: bool = False
    mock_status: str = "UP"


class ServiceUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    use_mock: bool | None = None
    mock_status: str | None = None


# App lifecycle
scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    threading.Thread(target=run_all_checks, daemon=True).start()                            
    threading.Thread(target=run_metrics_collection, daemon=True).start()                    
    scheduler.add_job(run_all_checks, "interval", seconds=30, id="monitor")
    scheduler.add_job(run_metrics_collection, "interval", seconds=30, id="metrics")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="CloudOps Platform", lifespan=lifespan)


# Static files + SPA root
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# Services CRUD
@app.get("/api/services")
def list_services():
    conn = get_connection()
    rows = conn.execute("""
        SELECT
            s.id, s.name, s.url, s.use_mock, s.mock_status, s.created_at,
            (
                SELECT sc.status
                FROM service_checks sc
                WHERE sc.service_id = s.id
                ORDER BY sc.id DESC LIMIT 1
            ) AS current_status,
            (
                SELECT sc.response_time_ms
                FROM service_checks sc
                WHERE sc.service_id = s.id
                ORDER BY sc.id DESC LIMIT 1
            ) AS last_response_ms,
            (
                SELECT sc.checked_at
                FROM service_checks sc
                WHERE sc.service_id = s.id
                ORDER BY sc.id DESC LIMIT 1
            ) AS last_checked
        FROM services s
        ORDER BY s.id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/services/{service_id}")
def get_service(service_id: int):
    conn = get_connection()
    row = conn.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    return dict(row)


@app.post("/api/services", status_code=201)
def create_service(data: ServiceCreate):
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO services (name, url, use_mock, mock_status) VALUES (?,?,?,?)",
        (data.name, data.url, int(data.use_mock), data.mock_status),
    )
    conn.commit()
    service_id = cur.lastrowid
    conn.close()
    # Immediately check the new service
    svc_conn = get_connection()
    svc = dict(svc_conn.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone())
    svc_conn.close()
    from monitor import save_check_result
    result = check_service(svc)
    save_check_result(result)
    return {"id": service_id, "message": "Service created"}


@app.patch("/api/services/{service_id}")
def update_service(service_id: int, data: ServiceUpdate):
    conn = get_connection()
    row = conn.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Service not found")
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        conn.close()
        return {"message": "Nothing to update"}
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(
        f"UPDATE services SET {set_clause} WHERE id=?",
        (*updates.values(), service_id),
    )
    conn.commit()
    conn.close()
    return {"message": "Service updated"}


@app.delete("/api/services/{service_id}")
def delete_service(service_id: int):
    conn = get_connection()
    row = conn.execute("SELECT id FROM services WHERE id=?", (service_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Service not found")
    conn.execute("DELETE FROM service_checks WHERE service_id=?", (service_id,))
    conn.execute("DELETE FROM services WHERE id=?", (service_id,))
    conn.commit()
    conn.close()
    return {"message": "Service deleted"}


# Manual trigger + history
@app.post("/api/services/{service_id}/check")
def manual_check(service_id: int):
    conn = get_connection()
    row = conn.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    from monitor import save_check_result
    result = check_service(dict(row))
    save_check_result(result)
    return result


@app.post("/api/check-all")
def check_all():
    run_all_checks()
    return {"message": "All services checked"}


@app.get("/api/services/{service_id}/history")
def service_history(service_id: int, limit: int = 20):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT status, response_time_ms, status_code, error_message, checked_at
        FROM service_checks
        WHERE service_id=?
        ORDER BY id DESC LIMIT ?
        """,
        (service_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Summary stats
@app.get("/api/summary")
def summary():
    conn = get_connection()
    services = conn.execute("""
        SELECT
            s.id,
            (
                SELECT sc.status
                FROM service_checks sc
                WHERE sc.service_id = s.id
                ORDER BY sc.id DESC LIMIT 1
            ) AS current_status
        FROM services s
    """).fetchall()
    conn.close()

    total  = len(services)
    up     = sum(1 for s in services if s["current_status"] == "UP")
    down   = sum(1 for s in services if s["current_status"] == "DOWN")
    degrad = sum(1 for s in services if s["current_status"] == "DEGRADED")
    unknown = total - up - down - degrad

    return {
        "total": total,
        "up": up,
        "down": down,
        "degraded": degrad,
        "unknown": unknown,
    }


# Log Analyzer endpoints
@app.post("/api/logs/analyze")
async def analyze_log(file: UploadFile = File(...)):
    MAX_SIZE = 10 * 1024 * 1024  # 10 MB
    raw = await file.read()
    if len(raw) > MAX_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Max 10 MB.")

    try:
        content = raw.decode("utf-8", errors="replace")
    except Exception:
        raise HTTPException(status_code=400, detail="Could not decode file. Upload a plain text log file.")

    result = parse_logs(content)

    # Persist to DB
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO log_uploads (
            filename, file_size_bytes, total_lines, parsed_lines,
            count_info, count_warning, count_error, count_debug, count_critical,
            most_frequent_issue, time_range_from, time_range_to, result_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            file.filename or "upload.log",
            len(raw),
            result["total_lines"],
            result["parsed_lines"],
            result["counts"]["INFO"],
            result["counts"]["WARNING"],
            result["counts"]["ERROR"],
            result["counts"]["DEBUG"],
            result["counts"]["CRITICAL"],
            result["most_frequent_issue"],
            result["time_range"]["from"],
            result["time_range"]["to"],
            json.dumps(result),
        ),
    )
    upload_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {"upload_id": upload_id, **result}


@app.get("/api/logs/history")
def log_history(limit: int = 20):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, filename, file_size_bytes, total_lines, parsed_lines,
               count_info, count_warning, count_error, count_debug, count_critical,
               most_frequent_issue, time_range_from, time_range_to, uploaded_at
        FROM log_uploads
        ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/logs/{upload_id}")
def get_log_result(upload_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT result_json FROM log_uploads WHERE id=?", (upload_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Upload not found")
    return json.loads(row["result_json"])


@app.delete("/api/logs/{upload_id}")
def delete_log(upload_id: int):
    conn = get_connection()
    row = conn.execute("SELECT id FROM log_uploads WHERE id=?", (upload_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Upload not found")
    conn.execute("DELETE FROM log_uploads WHERE id=?", (upload_id,))
    conn.commit()
    conn.close()
    return {"message": "Deleted"}


# Incident Tracker endpoints
class IncidentUpdate(BaseModel):
    status: str | None = None
    severity: str | None = None
    note: str | None = None

class IncidentCreate(BaseModel):
    service_id: int
    title: str
    description: str | None = None
    severity: str = "Medium"


@app.get("/api/incidents")
def list_incidents(status: str | None = None, limit: int = 50):
    conn = get_connection()
    if status:
        rows = conn.execute(
            """
            SELECT * FROM incidents
            WHERE status = ?
            ORDER BY id DESC LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM incidents ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/incidents/summary")
def incident_summary():
    conn = get_connection()
    rows = conn.execute(
        "SELECT status, COUNT(*) as count FROM incidents GROUP BY status"
    ).fetchall()
    conn.close()
    summary = {"Open": 0, "Acknowledged": 0, "Resolved": 0, "total": 0}
    for row in rows:
        summary[row["status"]] = row["count"]
        summary["total"] += row["count"]
    return summary


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM incidents WHERE id=?", (incident_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found")
    timeline = conn.execute(
        """
        SELECT event, note, created_at FROM incident_timeline
        WHERE incident_id=? ORDER BY id ASC
        """,
        (incident_id,),
    ).fetchall()
    conn.close()
    return {**dict(row), "timeline": [dict(t) for t in timeline]}


@app.patch("/api/incidents/{incident_id}")
def update_incident(incident_id: int, data: IncidentUpdate):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM incidents WHERE id=?", (incident_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found")

    current = dict(row)
    new_status = data.status or current["status"]

    # Build update fields
    fields = {}
    if data.severity:
        fields["severity"] = data.severity
    if data.status and data.status != current["status"]:
        fields["status"] = data.status
        if data.status == "Acknowledged":
            fields["acknowledged_at"] = "datetime('now')"
        elif data.status == "Resolved":
            fields["resolved_at"] = "datetime('now')"

    if fields:
        # Handle datetime fields separately
        set_parts = []
        values = []
        for k, v in fields.items():
            if v in ("datetime('now')",):
                set_parts.append(f"{k}=datetime('now')")
            else:
                set_parts.append(f"{k}=?")
                values.append(v)
        values.append(incident_id)
        conn.execute(
            f"UPDATE incidents SET {', '.join(set_parts)} WHERE id=?",
            values,
        )

    # Add timeline entry
    if data.status and data.status != current["status"]:
        event_map = {
            "Acknowledged": "Incident acknowledged",
            "Resolved":     "Incident resolved",
            "Open":         "Incident reopened",
        }
        event = event_map.get(data.status, f"Status changed to {data.status}")
        note = data.note or f"Status updated to {data.status} manually."
        conn.execute(
            "INSERT INTO incident_timeline (incident_id, event, note) VALUES (?,?,?)",
            (incident_id, event, note),
        )
    elif data.note:
        conn.execute(
            "INSERT INTO incident_timeline (incident_id, event, note) VALUES (?,?,?)",
            (incident_id, "Note added", data.note),
        )

    conn.commit()
    conn.close()
    return {"message": "Incident updated"}


@app.post("/api/incidents", status_code=201)
def create_incident(data: IncidentCreate):
    conn = get_connection()
    svc = conn.execute(
        "SELECT name FROM services WHERE id=?", (data.service_id,)
    ).fetchone()
    if not svc:
        conn.close()
        raise HTTPException(status_code=404, detail="Service not found")

    row = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()
    incident_number = f"INC-{row[0] + 1:04d}"

    cur = conn.execute(
        """
        INSERT INTO incidents
            (incident_number, service_id, service_name, title,
             description, severity, status)
        VALUES (?, ?, ?, ?, ?, ?, 'Open')
        """,
        (incident_number, data.service_id, svc["name"],
         data.title, data.description, data.severity),
    )
    incident_id = cur.lastrowid
    conn.execute(
        "INSERT INTO incident_timeline (incident_id, event, note) VALUES (?,?,?)",
        (incident_id, "Incident opened", "Manually created by engineer."),
    )
    conn.commit()
    conn.close()
    return {"id": incident_id, "incident_number": incident_number}


@app.delete("/api/incidents/{incident_id}")
def delete_incident(incident_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM incidents WHERE id=?", (incident_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Incident not found")
    conn.execute(
        "DELETE FROM incident_timeline WHERE incident_id=?", (incident_id,)
    )
    conn.execute("DELETE FROM incidents WHERE id=?", (incident_id,))
    conn.commit()
    conn.close()
    return {"message": "Incident deleted"}


# System Health endpoints
@app.get("/api/health/current")
def health_current():
    """Return the latest metrics snapshot."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM system_metrics ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        # If no saved snapshot yet, collect live
        return collect_metrics()
    return dict(row)
 
 
@app.get("/api/health/history")
def health_history(limit: int = 60):
    """Return last N snapshots for sparkline charts (default 60 = 30 min)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT cpu_percent, memory_percent, disk_percent,
               net_bytes_sent_mb, net_bytes_recv_mb, recorded_at
        FROM system_metrics
        ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    # Reverse so oldest is first (left to right on chart)
    return [dict(r) for r in reversed(rows)]
 
 
@app.get("/api/health/live")
def health_live():
    """Collect and return a fresh live reading (not saved to DB)."""
    return collect_metrics()