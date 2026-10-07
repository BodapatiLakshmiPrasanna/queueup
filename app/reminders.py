"""Sends a reminder shortly before each appointment.

Replace `console_send` with Twilio / WhatsApp Cloud API / an email provider in production.
"""
import asyncio
from datetime import datetime

from .db import connect


def console_send(phone: str, message: str) -> None:
    print(f"[reminder] to {phone}: {message}", flush=True)


def run_reminders(db_file: str, minutes: int, send=console_send, now: datetime | None = None) -> int:
    now = now or datetime.now()
    conn = connect(db_file)
    sent = 0
    try:
        rows = conn.execute(
            "SELECT * FROM appointments WHERE reminded = 0 AND status IN ('booked','confirmed') AND date >= ?",
            (now.date().isoformat(),),
        ).fetchall()
        for a in rows:
            start = datetime.fromisoformat(f"{a['date']}T{a['time']}")
            mins_left = (start - now).total_seconds() / 60
            if 0 < mins_left <= minutes:
                send(a["phone"], f"Hi {a['name']}, your appointment is at {a['time']}. Your queue number is {a['token']}.")
                with conn:
                    conn.execute("UPDATE appointments SET reminded = 1 WHERE id = ?", (a["id"],))
                sent += 1
    finally:
        conn.close()
    return sent


async def reminder_loop(db_file: str, minutes: int) -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(run_reminders, db_file, minutes)
        except Exception as exc:  # keep the loop alive
            print("reminder error:", exc, flush=True)
