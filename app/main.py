import asyncio
import hashlib
import hmac
import os
import re
import sqlite3
import time as _time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Settings
from .db import connect, init_db
from .reminders import reminder_loop
from .slots import days_ago, generate_slots, is_valid_date, now_time, today_str

PHONE_RE = re.compile(r"^\+?[0-9]{10,13}$")
ADMIN_STATUSES = {"booked", "confirmed", "completed", "cancelled", "no_show"}
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class BookingIn(BaseModel):
    name: str
    phone: str
    date: str
    time: str


class CancelIn(BaseModel):
    phone: str


class LoginIn(BaseModel):
    password: str


class StatusIn(BaseModel):
    status: str


def _digest(s: str) -> bytes:
    return hashlib.sha256(s.encode()).digest()


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or Settings.from_env()
    if os.environ.get("APP_ENV") == "production" and (
        s.admin_password == "change-me" or s.jwt_secret.startswith("dev-")
    ):
        raise RuntimeError("Set ADMIN_PASSWORD and JWT_SECRET before running in production.")

    init_db(s.db_file)
    slots = generate_slots(s.open_hour, s.close_hour, s.step)
    attempts: dict[str, list[float]] = {}

    @asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(reminder_loop(s.db_file, s.reminder_minutes))
        yield
        task.cancel()

    app = FastAPI(title="QueueUp", description="Appointments and live queue for small businesses", lifespan=lifespan)

    def db():
        conn = connect(s.db_file)
        try:
            yield conn
        finally:
            conn.close()

    def require_admin(authorization: str = Header(default="")):
        try:
            jwt.decode(authorization.replace("Bearer ", ""), s.jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            raise HTTPException(401, "Please log in again.")

    # ---------- Public: customers ----------
    @app.get("/api/slots")
    def list_slots(date: str, conn=Depends(db)):
        if not is_valid_date(date) or date < today_str():
            raise HTTPException(400, "Choose today or a future date.")
        taken = {r["time"] for r in conn.execute(
            "SELECT time FROM appointments WHERE date = ? AND status != 'cancelled'", (date,))}
        now = now_time()
        return [
            {"time": t, "available": t not in taken and not (date == today_str() and t <= now)}
            for t in slots
        ]

    @app.post("/api/appointments", status_code=201)
    def book(body: BookingIn, conn=Depends(db)):
        name = body.name.strip()
        phone = re.sub(r"[\s-]", "", body.phone)
        if not 2 <= len(name) <= 80:
            raise HTTPException(400, "Enter your name.")
        if not PHONE_RE.match(phone):
            raise HTTPException(400, "Enter a valid phone number (10-13 digits).")
        if not is_valid_date(body.date) or body.date < today_str():
            raise HTTPException(400, "Choose today or a future date.")
        if body.time not in slots:
            raise HTTPException(400, "That time is not offered.")
        if body.date == today_str() and body.time <= now_time():
            raise HTTPException(400, "That time has already passed.")
        token = slots.index(body.time) + 1
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO appointments (name, phone, date, time, token) VALUES (?, ?, ?, ?, ?)",
                    (name, phone, body.date, body.time, token))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Someone just booked that slot. Pick another time.")
        return {"id": cur.lastrowid, "token": token, "date": body.date, "time": body.time, "status": "booked"}

    @app.post("/api/appointments/{appt_id}/cancel")
    def cancel(appt_id: int, body: CancelIn, conn=Depends(db)):
        phone = re.sub(r"[\s-]", "", body.phone)
        row = conn.execute("SELECT * FROM appointments WHERE id = ?", (appt_id,)).fetchone()
        if not row or row["phone"] != phone:
            raise HTTPException(404, "Booking not found.")
        if row["status"] not in ("booked", "confirmed"):
            raise HTTPException(409, "This booking can no longer be cancelled.")
        with conn:
            conn.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (appt_id,))
        return {"ok": True}

    @app.get("/api/queue")
    def queue(conn=Depends(db)):
        today = today_str()
        serving = conn.execute(
            "SELECT token FROM appointments WHERE date = ? AND status = 'serving'", (today,)).fetchone()
        waiting = conn.execute(
            "SELECT COUNT(*) AS c FROM appointments WHERE date = ? AND status IN ('booked','confirmed')",
            (today,)).fetchone()["c"]
        return {"nowServing": serving["token"] if serving else None, "waiting": waiting}

    # ---------- Owner ----------
    @app.post("/api/admin/login")
    def login(body: LoginIn, request: Request):
        ip = request.client.host if request.client else "unknown"
        recent = [t for t in attempts.get(ip, []) if _time.time() - t < 900]
        if len(recent) >= 10:
            raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
        if not hmac.compare_digest(_digest(body.password), _digest(s.admin_password)):
            attempts[ip] = recent + [_time.time()]
            raise HTTPException(401, "Wrong password.")
        attempts.pop(ip, None)
        exp = datetime.now(timezone.utc) + timedelta(hours=8)
        return {"token": jwt.encode({"role": "owner", "exp": exp}, s.jwt_secret, algorithm="HS256")}

    @app.get("/api/admin/appointments", dependencies=[Depends(require_admin)])
    def admin_list(date: str | None = None, conn=Depends(db)):
        date = date or today_str()
        if not is_valid_date(date):
            raise HTTPException(400, "Invalid date.")
        rows = conn.execute("SELECT * FROM appointments WHERE date = ? ORDER BY time", (date,)).fetchall()
        return [dict(r) for r in rows]

    @app.patch("/api/admin/appointments/{appt_id}", dependencies=[Depends(require_admin)])
    def admin_update(appt_id: int, body: StatusIn, conn=Depends(db)):
        if body.status not in ADMIN_STATUSES:
            raise HTTPException(400, "Invalid status.")
        with conn:
            cur = conn.execute("UPDATE appointments SET status = ? WHERE id = ?", (body.status, appt_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "Booking not found.")
        return {"ok": True}

    @app.post("/api/admin/queue/next", dependencies=[Depends(require_admin)])
    def call_next(conn=Depends(db)):
        today = today_str()
        with conn:  # one transaction: finish current, call next
            conn.execute("UPDATE appointments SET status = 'completed' WHERE date = ? AND status = 'serving'", (today,))
            nxt = conn.execute(
                "SELECT * FROM appointments WHERE date = ? AND status IN ('booked','confirmed') ORDER BY time LIMIT 1",
                (today,)).fetchone()
            if nxt:
                conn.execute("UPDATE appointments SET status = 'serving' WHERE id = ?", (nxt["id"],))
        return {"serving": dict(nxt) if nxt else None}

    @app.get("/api/admin/stats", dependencies=[Depends(require_admin)])
    def stats(conn=Depends(db)):
        per_day = conn.execute(
            "SELECT date, COUNT(*) AS count FROM appointments WHERE status != 'cancelled' AND date >= ? "
            "GROUP BY date ORDER BY date", (days_ago(6),)).fetchall()
        hours = conn.execute(
            "SELECT substr(time, 1, 2) || ':00' AS hour, COUNT(*) AS count FROM appointments "
            "WHERE status != 'cancelled' GROUP BY hour ORDER BY count DESC LIMIT 5").fetchall()
        totals = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(status = 'no_show'), 0) AS noShows, "
            "COALESCE(SUM(status = 'cancelled'), 0) AS cancelled FROM appointments").fetchone()
        return {"perDay": [dict(r) for r in per_day], "busiestHours": [dict(r) for r in hours], "totals": dict(totals)}

    @app.exception_handler(Exception)
    async def unhandled(_req, exc):
        print("error:", exc, flush=True)
        return JSONResponse({"detail": "Something went wrong. Please try again."}, status_code=500)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
