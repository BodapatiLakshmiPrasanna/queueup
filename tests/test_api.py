from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import connect
from app.main import create_app
from app.reminders import run_reminders

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
PHONE = "9876543210"


@pytest.fixture
def ctx(tmp_path):
    s = Settings(admin_password="pw", jwt_secret="test-secret-that-is-at-least-32-bytes-long", db_file=str(tmp_path / "t.db"))
    return TestClient(create_app(s)), s


@pytest.fixture
def client(ctx):
    return ctx[0]


@pytest.fixture
def admin(client):
    token = client.post("/api/admin/login", json={"password": "pw"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def book(client, time="10:00", date_=TOMORROW, phone=PHONE, name="Asha"):
    return client.post("/api/appointments", json={"name": name, "phone": phone, "date": date_, "time": time})


def test_slots_listed_and_marked_taken(client):
    assert book(client, "10:00").status_code == 201
    slots = {x["time"]: x["available"] for x in client.get(f"/api/slots?date={TOMORROW}").json()}
    assert slots["09:00"] is True and slots["10:00"] is False
    assert "16:30" in slots and "17:00" not in slots


def test_double_booking_rejected(client):
    assert book(client).status_code == 201
    assert book(client, phone="9123456780", name="Ravi").status_code == 409


def test_cancel_frees_slot(client):
    b = book(client).json()
    assert client.post(f"/api/appointments/{b['id']}/cancel", json={"phone": PHONE}).status_code == 200
    assert book(client, phone="9123456780").status_code == 201


def test_cancel_requires_matching_phone(client):
    b = book(client).json()
    assert client.post(f"/api/appointments/{b['id']}/cancel", json={"phone": "9000000000"}).status_code == 404


@pytest.mark.parametrize("payload", [
    {"name": "A", "phone": PHONE, "date": TOMORROW, "time": "10:00"},
    {"name": "Asha", "phone": "123", "date": TOMORROW, "time": "10:00"},
    {"name": "Asha", "phone": PHONE, "date": "2020-01-01", "time": "10:00"},
    {"name": "Asha", "phone": PHONE, "date": TOMORROW, "time": "03:00"},
    {"name": "Asha", "phone": PHONE, "date": "not-a-date", "time": "10:00"},
])
def test_invalid_bookings_rejected(client, payload):
    assert client.post("/api/appointments", json=payload).status_code == 400


def test_admin_routes_need_login(client):
    assert client.get("/api/admin/appointments").status_code == 401
    assert client.post("/api/admin/login", json={"password": "nope"}).status_code == 401


def test_admin_can_list_and_update(client, admin):
    b = book(client).json()
    rows = client.get(f"/api/admin/appointments?date={TOMORROW}", headers=admin).json()
    assert [r["name"] for r in rows] == ["Asha"]
    assert client.patch(f"/api/admin/appointments/{b['id']}", json={"status": "confirmed"}, headers=admin).status_code == 200
    assert client.patch(f"/api/admin/appointments/{b['id']}", json={"status": "bogus"}, headers=admin).status_code == 400


def test_queue_flow_for_today(ctx, admin):
    client, s = ctx
    today = date.today().isoformat()
    conn = connect(s.db_file)
    with conn:
        for t, tok in (("09:00", 1), ("09:30", 2)):
            conn.execute("INSERT INTO appointments (name, phone, date, time, token) VALUES ('X', ?, ?, ?, ?)", (PHONE, today, t, tok))
    conn.close()
    assert client.get("/api/queue").json() == {"nowServing": None, "waiting": 2}
    assert client.post("/api/admin/queue/next", headers=admin).json()["serving"]["token"] == 1
    assert client.get("/api/queue").json() == {"nowServing": 1, "waiting": 1}
    assert client.post("/api/admin/queue/next", headers=admin).json()["serving"]["token"] == 2
    assert client.post("/api/admin/queue/next", headers=admin).json()["serving"] is None


def test_stats(client, admin):
    book(client, "10:00"); book(client, "10:30", phone="9123456780")
    stats = client.get("/api/admin/stats", headers=admin).json()
    assert stats["totals"]["total"] == 2
    assert stats["busiestHours"][0] == {"hour": "10:00", "count": 2}


def test_reminder_sent_once(ctx):
    client, s = ctx
    book(client, "10:00")
    sent = []
    start = datetime.fromisoformat(f"{TOMORROW}T10:00")
    now = start - timedelta(minutes=30)
    assert run_reminders(s.db_file, 60, lambda p, m: sent.append((p, m)), now=now) == 1
    assert run_reminders(s.db_file, 60, lambda p, m: sent.append((p, m)), now=now) == 0
    assert sent[0][0] == PHONE and "10:00" in sent[0][1]
