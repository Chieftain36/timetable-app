from datetime import datetime, time, timezone
from uuid import uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from auth import JWT_ALGORITHM, hash_password
from main import app, get_connection
from scheduler import SchedulingError, detect_schedule_conflicts, generate_schedule_matrix


class FakeDatabase:
    """Minimal asyncpg-shaped state used to isolate HTTP API tests from PostgreSQL."""

    def __init__(self) -> None:
        self.users: dict[str, dict[str, object]] = {}
        self.courses: list[dict[str, object]] = []
        self.rooms: list[dict[str, object]] = []
        self.add_user("admin@example.com", "AdminPassword1", "admin")
        self.add_user("student@example.com", "StudentPassword1", "student")

    def add_user(self, email: str, password: str, role: str) -> None:
        self.users[email] = {
            "id": uuid4(),
            "email": email,
            "password_hash": hash_password(password),
            "role": role,
            "created_at": datetime.now(timezone.utc),
        }


class FakeConnection:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database

    async def fetchval(self, query: str, *args: object) -> int | None:
        if "FROM users" in query:
            return 1 if str(args[0]) in self.database.users else None
        if "FROM courses" in query:
            return 1 if any(row["code"] == args[0] for row in self.database.courses) else None
        raise AssertionError(f"Unexpected fetchval query: {query}")

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        if query.lstrip().startswith("SELECT") and "FROM users" in query:
            if "WHERE email" in query:
                return self.database.users.get(str(args[0]))
            for record in self.database.users.values():
                if record["id"] == args[0]:
                    return record
            return None
        if "INSERT INTO rooms" in query:
            name, building, capacity, is_active = args
            record = {
                "id": uuid4(),
                "name": str(name),
                "building": str(building),
                "capacity": int(capacity),
                "is_active": bool(is_active),
            }
            self.database.rooms.append(record)
            return record
        raise AssertionError(f"Unexpected fetchrow query: {query}")

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        if "FROM courses" in query:
            return list(self.database.courses)
        raise AssertionError(f"Unexpected fetch query: {query}")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def app_with_fake_database(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-at-least-32-characters")
    database = FakeDatabase()

    async def override_connection():
        yield FakeConnection(database)

    app.dependency_overrides[get_connection] = override_connection
    yield app, database
    app.dependency_overrides.clear()


async def login(client: AsyncClient, email: str, password: str) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest.mark.anyio
async def test_login_returns_valid_jwt_and_authorizes_admin(app_with_fake_database) -> None:
    test_app, database = app_with_fake_database
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        token = await login(client, "admin@example.com", "AdminPassword1")
        response = await client.get(
            "/api/v1/courses", headers={"Authorization": f"Bearer {token}"}
        )

    payload = jwt.decode(
        token,
        "test-secret-key-with-at-least-32-characters",
        algorithms=[JWT_ALGORITHM],
    )
    assert payload["sub"] == str(database.users["admin@example.com"]["id"])
    assert payload["role"] == "admin"
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.anyio
async def test_login_rejects_wrong_password(app_with_fake_database) -> None:
    test_app, _ = app_with_fake_database
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "not-the-password"},
        )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


@pytest.mark.anyio
async def test_student_token_cannot_manage_courses(app_with_fake_database) -> None:
    test_app, _ = app_with_fake_database
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        token = await login(client, "student@example.com", "StudentPassword1")
        response = await client.get(
            "/api/v1/courses", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize("capacity", [0, -1])
async def test_room_capacity_must_be_positive(app_with_fake_database, capacity: int) -> None:
    test_app, _ = app_with_fake_database
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        token = await login(client, "admin@example.com", "AdminPassword1")
        response = await client.post(
            "/api/v1/rooms",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "A-101", "building": "Main", "capacity": capacity},
        )
    assert response.status_code == 422


def test_scheduler_generates_deterministic_non_conflicting_timetable() -> None:
    groups = [
        {"id": "group-large", "student_count": 35},
        {"id": "group-small", "student_count": 18},
    ]
    requirements = [
        {"course_id": "course-algorithms", "group_id": "group-large", "instructor_id": "instructor-a"},
        {"course_id": "course-databases", "group_id": "group-small", "instructor_id": "instructor-b"},
    ]
    instructors = [{"id": "instructor-a"}, {"id": "instructor-b"}]
    rooms = [
        {"id": "small-room", "name": "101", "building": "A", "capacity": 20, "is_active": True},
        {"id": "large-room", "name": "201", "building": "A", "capacity": 40, "is_active": True},
    ]
    slots = [
        {"id": "monday-09", "day_of_week": 1, "start_time": time(9), "end_time": time(10)},
        {"id": "monday-10", "day_of_week": 1, "start_time": time(10), "end_time": time(11)},
    ]

    generated = generate_schedule_matrix(groups, requirements, instructors, rooms, slots)

    assert generated == [
        {"course_id": "course-algorithms", "room_id": "large-room", "instructor_id": "instructor-a", "group_id": "group-large", "time_slot_id": "monday-09"},
        {"course_id": "course-databases", "room_id": "small-room", "instructor_id": "instructor-b", "group_id": "group-small", "time_slot_id": "monday-09"},
    ]
    assert detect_schedule_conflicts(generated) == []


def test_scheduler_reports_impossible_capacity_and_detects_conflicts() -> None:
    with pytest.raises(SchedulingError) as error:
        generate_schedule_matrix(
            [{"id": "group-1", "student_count": 50}],
            [{"course_id": "course-1", "group_id": "group-1", "instructor_id": "instructor-1"}],
            [{"id": "instructor-1"}],
            [{"id": "room-1", "capacity": 30, "is_active": True}],
            [{"id": "slot-1", "day_of_week": 1, "start_time": time(9), "end_time": time(10)}],
        )
    assert error.value.unscheduled[0]["reason"] == "No active room has sufficient capacity"

    conflicts = detect_schedule_conflicts([
        {"id": "schedule-1", "room_id": "room-1", "instructor_id": "instructor-1", "group_id": "group-1", "time_slot_id": "slot-1", "room_capacity": 20, "student_count": 30},
        {"id": "schedule-2", "room_id": "room-1", "instructor_id": "instructor-1", "group_id": "group-1", "time_slot_id": "slot-1", "room_capacity": 20, "student_count": 10},
    ])
    assert {conflict["type"] for conflict in conflicts} == {
        "room_capacity_violation",
        "room_double_booking",
        "instructor_double_booking",
        "group_double_booking",
    }
