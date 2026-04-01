from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
import os

from database import init_db, get_connection
from monitor import run_all_checks, check_service


# ──────────────────────────────────────────────
# Pydantic schemas
# ──────────────────────────────────────────────

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


# ──────────────────────────────────────────────
# App lifecycle
# ──────────────────────────────────────────────

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    run_all_checks()                              # immediate first poll
    scheduler.add_job(run_all_checks, "interval", seconds=30, id="monitor")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="CloudEye Platform", lifespan=lifespan)


# ──────────────────────────────────────────────
# Static files + SPA root
# ──────────────────────────────────────────────

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ──────────────────────────────────────────────
# Services CRUD
# ──────────────────────────────────────────────

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


# ──────────────────────────────────────────────
# Manual trigger + history
# ──────────────────────────────────────────────

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


# ──────────────────────────────────────────────
# Summary stats
# ──────────────────────────────────────────────

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