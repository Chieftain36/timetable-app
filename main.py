import os
from datetime import datetime, timezone
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auth import create_access_token, get_current_user, hash_password, require_roles, verify_password
from models import (
    ScheduleDraft, ScheduleUpdate, ManualScheduleRead,
    AvailabilityEntry, AvailabilityStatus, AvailabilityRead, AvailabilityWrite,
    InstructorStatusUpdate, WorkloadRead, WorkloadUpdate, TeachingLoadCreate, TeachingLoadRead,
    ConflictResponse,
    CourseCreate,
    CourseRead,
    CourseUpdate,
    CurrentUser,
    InstructorCreate,
    InstructorRead,
    InstructorUpdate,
    LoginRequest,
    RoomCreate,
    RoomRead,
    RoomUpdate,
    ScheduleGenerationRequest,
    ScheduleGenerationResponse,
    ScheduleRead,
    StudentGroupCreate,
    StudentGroupRead,
    StudentGroupUpdate,
    TimeSlotCreate,
    TimeSlotRead,
    TimeSlotUpdate,
    TokenResponse,
    UserCreate,
    UserRead,
    UserRole,
    UserUpdate,
)
from scheduler import SchedulingError, detect_schedule_conflicts, generate_schedule_matrix


async def _bootstrap_admin(pool: asyncpg.Pool) -> None:
    """Create a first administrator only when both bootstrap variables are set."""
    email = os.getenv("BOOTSTRAP_ADMIN_EMAIL")
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
    if bool(email) != bool(password):
        raise RuntimeError(
            "Set both BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD, or neither"
        )
    if not email:
        return

    bootstrap_user = UserCreate(email=email, password=password, role=UserRole.ADMIN)
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO users (email, password_hash, role)
            VALUES ($1, $2, $3)
            ON CONFLICT (email) DO NOTHING
            """,
            str(bootstrap_user.email),
            hash_password(bootstrap_user.password),
            bootstrap_user.role.value,
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is required")

    pool = await asyncpg.create_pool(
        dsn=database_url,
        min_size=1,
        max_size=10,
        command_timeout=60,
    )
    app.state.pool = pool
    try:
        await _bootstrap_admin(pool)
        yield
    finally:
        await pool.close()


app = FastAPI(
    title="Intelligent University Timetable Generator API",
    version="2.0.0",
    lifespan=lifespan,
)


FIELD_LABELS = {
    "academic_year": "Academic year", "max_hours": "Workload limit, hours", "name": "Name", "code": "Course code", "description": "Description",
    "department": "Department", "title": "Title", "building": "Building",
    "capacity": "Capacity", "student_count": "Student count",
    "start_time": "Start time", "end_time": "End time", "day_of_week": "Day of week",
    "sessions_per_week": "Classes per week", "assignments": "Assignments",
    "schedule_ids": "ID schedules", "schedule_id": "ID schedule",
    "course_id": "ID course", "group_id": "ID group", "instructor_id": "ID instructor",
    "user_id": "ID user", "email": "Email", "password": "Password", "role": "Role",
}


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    error = errors[0] if errors else {}
    kind = error.get("type", "")
    location = error.get("loc", ())
    context = error.get("ctx") or {}
    field = next((part for part in reversed(location) if isinstance(part, str) and part not in {"body", "query", "path"}), "")
    if kind == "null_update":
        field = context.get("field", "")
    label = FIELD_LABELS.get(field, "Request data")
    if field in {"name", "code"} and "/student-groups" in request.url.path:
        label = "Group code"
    elif field == "name" and "/courses" in request.url.path:
        label = "Course name"
    reasons = {
        "academic_year_format": "expected YYYY-YYYY format",
        "text_characters": "forbidden characters",
        "text_empty": "field must not be empty",
        "clock_format": "expected HH:MM or HH:MM:SS time",
        "room_code": "choose a valid room number and matching building (practical: D101–I312, floors 1–3, rooms 01–12; lecture: A1–D2)",
        "room_capacity": "practical room capacity must be 5 to 25",
        "missing": "required field is missing", "extra_forbidden": "unknown field",
        "int_type": "a whole number without quotes is required", "int_parsing": "a whole number is required",
        "greater_than": "value must be greater than zero",
        "greater_than_equal": "value is below the minimum", "less_than_equal": "value exceeds the maximum",
        "string_type": "a string is required", "string_too_short": "value is too short",
        "string_too_long": "value is too long", "string_pattern_mismatch": "invalid format",
        "uuid_parsing": "expected a valid UUID", "uuid_type": "expected UUID as a string",
        "too_short": "list is too short", "too_long": "list is too long",
        "list_type": "expected a list", "model_attributes_type": "expected JSON-object",
        "enum": "invalid option", "null_update": "field cannot be null",
    }
    if kind == "json_invalid":
        detail = "Invalid syntax JSON: check quotes, commas and brackets"
    else:
        reason = reasons.get(kind, "invalid format or value")
        if kind == "value_error" and field == "email":
            reason = "invalid email address"
        elif kind == "value_error" and "time-slots" in request.url.path:
            reason = "end time must be after start time; supply at least one field for updates"
        if field == "student_count" and kind in {"greater_than_equal", "less_than_equal", "int_type", "int_parsing"}:
            reason = "student count must be a whole number from 5 to 50"
        detail = f"Invalid syntax or format for field '{label}': {reason}"
    # Do not return raw input, exception context, passwords or internal exception text.
    return JSONResponse(status_code=422, content={"detail": detail})


async def get_connection(request: Request) -> AsyncIterator[asyncpg.Connection]:
    pool: asyncpg.Pool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not ready",
        )
    async with pool.acquire() as connection:
        # Serialize domain writes across workers, including generation and deactivation.
        # READ COMMITTED reads after the lock see the preceding writer's committed state.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not request.url.path.endswith("/auth/login"):
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(20261005)")
                yield connection
        else:
            yield connection


DbConnection = Annotated[asyncpg.Connection, Depends(get_connection, scope="function")]
AdminUser = Annotated[CurrentUser, Depends(require_roles([UserRole.ADMIN]))]
AuthenticatedUser = Annotated[CurrentUser, Depends(get_current_user)]


SCHEDULE_SELECT = """
SELECT
    s.id, s.course_id, s.room_id, s.instructor_id, s.group_id, s.time_slot_id,
    s.created_at, c.code AS course_code, c.name AS course_name,
    r.name AS room_name, r.building AS room_building,
    g.name AS group_name, g.student_count,
    u.email AS instructor_email,
    t.day_of_week, t.start_time, t.end_time
FROM schedules AS s
JOIN courses AS c ON c.id = s.course_id
JOIN rooms AS r ON r.id = s.room_id
JOIN student_groups AS g ON g.id = s.group_id
JOIN instructors AS i ON i.id = s.instructor_id
JOIN users AS u ON u.id = i.user_id
JOIN time_slots AS t ON t.id = s.time_slot_id
"""


def _record_or_500(record: asyncpg.Record | None) -> dict[str, Any]:
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database did not return the expected record",
        )
    return dict(record)


def _not_found(entity_name: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{entity_name} was not found",
    )


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


async def _delete_by_id(
    connection: asyncpg.Connection,
    table: str,
    entity_name: str,
    entity_id: UUID,
) -> None:
    try:
        result = await connection.execute(f"DELETE FROM {table} WHERE id = $1", entity_id)
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{entity_name} is referenced by a schedule and cannot be deleted",
        ) from None
    if result == "DELETE 0":
        raise _not_found(entity_name)


async def _update_by_id(
    connection: asyncpg.Connection,
    table: str,
    entity_id: UUID,
    changes: dict[str, Any],
    returning_columns: str,
) -> dict[str, Any]:
    assignments = ", ".join(
        f"{column} = ${index}"
        for index, column in enumerate(changes, start=1)
    )
    query = (
        f"UPDATE {table} SET {assignments} "
        f"WHERE id = ${len(changes) + 1} RETURNING {returning_columns}"
    )
    record = await connection.fetchrow(query, *changes.values(), entity_id)
    if record is None:
        raise _not_found("Resource")
    return dict(record)


async def _ensure_instructor_user(connection: asyncpg.Connection, user_id: UUID) -> None:
    user = await connection.fetchrow("SELECT id, role FROM users WHERE id = $1", user_id)
    if user is None:
        raise _not_found("User")
    if str(user["role"]) != UserRole.INSTRUCTOR.value:
        raise _conflict("An instructor profile requires a user with the instructor role")


@app.get("/", include_in_schema=False)
async def serve_frontend() -> FileResponse:
    return FileResponse(Path(__file__).with_name("index.html"))


@app.post("/api/v1/auth/login", response_model=TokenResponse)
async def login(credentials: LoginRequest, connection: DbConnection) -> TokenResponse:
    record = await connection.fetchrow(
        """
        SELECT id, email, password_hash, role
        FROM users
        WHERE email = $1
        """,
        str(credentials.email),
    )
    if record is None or not verify_password(credentials.password, record["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = CurrentUser(id=record["id"], email=record["email"], role=record["role"])
    return TokenResponse(access_token=create_access_token(user))


@app.post("/api/v1/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(user: UserCreate, _: AdminUser, connection: DbConnection) -> UserRead:
    if await connection.fetchval("SELECT 1 FROM users WHERE email = $1", str(user.email)):
        raise _conflict("Email already exists")
    try:
        record = await connection.fetchrow(
            """
            INSERT INTO users (email, password_hash, role)
            VALUES ($1, $2, $3)
            RETURNING id, email, role, created_at
            """,
            str(user.email),
            hash_password(user.password),
            user.role.value,
        )
    except asyncpg.UniqueViolationError:
        raise _conflict("Email already exists") from None
    return UserRead.model_validate(_record_or_500(record))


@app.get("/api/v1/users", response_model=list[UserRead])
async def list_users(_: AdminUser, connection: DbConnection) -> list[UserRead]:
    records = await connection.fetch(
        "SELECT id, email, role, created_at FROM users ORDER BY email"
    )
    return [UserRead.model_validate(dict(record)) for record in records]


@app.patch("/api/v1/users/{user_id}", response_model=UserRead)
async def update_user(
    user_id: UUID,
    update: UserUpdate,
    _: AdminUser,
    connection: DbConnection,
) -> UserRead:
    changes = update.model_dump(exclude_unset=True)
    if "email" in changes:
        changes["email"] = str(changes["email"])
    if "password" in changes:
        changes["password_hash"] = hash_password(changes.pop("password"))
    if "role" in changes:
        new_role = changes["role"]
        if new_role != UserRole.INSTRUCTOR:
            has_profile = await connection.fetchval(
                "SELECT 1 FROM instructors WHERE user_id = $1", user_id
            )
            if has_profile:
                raise _conflict("A user linked to an instructor profile must keep the instructor role")
        changes["role"] = new_role.value
    try:
        record = await _update_by_id(
            connection,
            "users",
            user_id,
            changes,
            "id, email, role, created_at",
        )
    except asyncpg.UniqueViolationError:
        raise _conflict("Email already exists") from None
    return UserRead.model_validate(record)


@app.delete("/api/v1/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: UUID, _: AdminUser, connection: DbConnection) -> None:
    user = await _row(connection, "users", user_id)
    if user["role"] == UserRole.ADMIN:
        raise HTTPException(403, "Administrator accounts cannot be deleted")
    await _delete_by_id(connection, "users", "User", user_id)


@app.post("/api/v1/courses", response_model=CourseRead, status_code=status.HTTP_201_CREATED)
async def create_course(course: CourseCreate, _: AdminUser, connection: DbConnection) -> CourseRead:
    if await connection.fetchval("SELECT 1 FROM courses WHERE code = $1", course.code):
        raise _conflict("Course code already exists")
    try:
        record = await connection.fetchrow(
            """
            INSERT INTO courses (code, name, description, is_active, max_hours)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, code, name, description, is_active, max_hours
            """,
            course.code,
            course.name,
            course.description, course.is_active, course.max_hours,
        )
    except asyncpg.UniqueViolationError:
        raise _conflict("Course code already exists") from None
    return CourseRead.model_validate(_record_or_500(record))


@app.get("/api/v1/courses", response_model=list[CourseRead])
async def list_courses(_: AdminUser, connection: DbConnection) -> list[CourseRead]:
    records = await connection.fetch("SELECT id, code, name, description, is_active, max_hours FROM courses ORDER BY code")
    return [CourseRead.model_validate(dict(record)) for record in records]


@app.patch("/api/v1/courses/{course_id}", response_model=CourseRead)
async def update_course(
    course_id: UUID,
    update: CourseUpdate,
    _: AdminUser,
    connection: DbConnection,
) -> CourseRead:
    if update.max_hours is not None:
        totals = {}
        for row in await connection.fetch("SELECT * FROM teaching_loads"):
            if row["course_id"] == course_id and row["is_active"]:
                totals[row["group_id"]] = totals.get(row["group_id"], 0) + row["sessions_per_week"] * row["session_minutes"]
        slots = {row["id"]: row for row in await connection.fetch("SELECT * FROM time_slots")}
        actual = {}
        for row in await connection.fetch("SELECT * FROM schedules"):
            if row["course_id"] == course_id:
                actual[row["group_id"]] = actual.get(row["group_id"], 0) + _minutes(slots[row["time_slot_id"]])
        if max([0, *totals.values(), *actual.values()]) > update.max_hours * 60:
            raise HTTPException(409, "The course limit is below the existing workload")
    try:
        record = await _update_by_id(
            connection,
            "courses",
            course_id,
            update.model_dump(exclude_unset=True),
            "id, code, name, description, is_active, max_hours",
        )
    except asyncpg.UniqueViolationError:
        raise _conflict("Course code already exists") from None
    return CourseRead.model_validate(record)


@app.delete("/api/v1/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(course_id: UUID, _: AdminUser, connection: DbConnection) -> None:
    await _delete_by_id(connection, "courses", "Course", course_id)


@app.post("/api/v1/rooms", response_model=RoomRead, status_code=status.HTTP_201_CREATED)
async def create_room(room: RoomCreate, _: AdminUser, connection: DbConnection) -> RoomRead:
    try:
        record = await connection.fetchrow(
            """
            INSERT INTO rooms (name, building, capacity, is_active, room_type)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, name, building, capacity, is_active, room_type
            """,
            room.name,
            room.building,
            room.capacity,
            room.is_active, room.room_type,
        )
    except asyncpg.UniqueViolationError:
        raise _conflict("A room with this name already exists in the building") from None
    return RoomRead.model_validate(_record_or_500(record))


@app.get("/api/v1/rooms", response_model=list[RoomRead])
async def list_rooms(_: AdminUser, connection: DbConnection) -> list[RoomRead]:
    records = await connection.fetch(
        "SELECT id, name, building, capacity, is_active, room_type FROM rooms ORDER BY building, name"
    )
    return [RoomRead.model_validate(dict(record)) for record in records]


@app.patch("/api/v1/rooms/{room_id}", response_model=RoomRead)
async def update_room(
    room_id: UUID,
    update: RoomUpdate,
    _: AdminUser,
    connection: DbConnection,
) -> RoomRead:
    current = await _row(connection, "rooms", room_id)
    changes = update.model_dump(exclude_unset=True)
    merged = {key: changes.get(key, current.get(key)) for key in RoomCreate.model_fields}
    try:
        valid = RoomCreate.model_validate(merged)
    except ValidationError:
        raise HTTPException(422, "Select a valid room type, building and number; capacity is 5–25 for practical rooms or 1–100 for lecture rooms") from None
    for entry in await connection.fetch("SELECT * FROM schedules"):
        if entry["room_id"] == room_id:
            group = await _row(connection, "student_groups", entry["group_id"])
            if not valid.is_active or valid.capacity < group["student_count"]:
                raise HTTPException(409, "This change would invalidate an existing scheduled class")
    try:
        record = await _update_by_id(
            connection,
            "rooms",
            room_id,
            valid.model_dump(),
            "id, name, building, capacity, is_active, room_type",
        )
    except asyncpg.UniqueViolationError:
        raise _conflict("A room with this name already exists in the building") from None
    except asyncpg.CheckViolationError:
        raise _conflict("Room capacity must be positive") from None
    return RoomRead.model_validate(record)


@app.delete("/api/v1/rooms/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_room(room_id: UUID, _: AdminUser, connection: DbConnection) -> None:
    await _delete_by_id(connection, "rooms", "Room", room_id)


@app.post(
    "/api/v1/student-groups",
    response_model=StudentGroupRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_student_group(
    group: StudentGroupCreate,
    _: AdminUser,
    connection: DbConnection,
) -> StudentGroupRead:
    try:
        record = await connection.fetchrow(
            """
            INSERT INTO student_groups (name, student_count, academic_year, is_active)
            VALUES ($1, $2, $3, $4)
            RETURNING id, name, student_count, academic_year, is_active
            """,
            group.name,
            group.student_count, group.academic_year, group.is_active,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "This group code already exists in this academic year") from None
    return StudentGroupRead.model_validate(_record_or_500(record))


@app.get("/api/v1/student-groups", response_model=list[StudentGroupRead])
async def list_student_groups(_: AdminUser, connection: DbConnection) -> list[StudentGroupRead]:
    records = await connection.fetch("SELECT id, name, student_count, academic_year, is_active FROM student_groups ORDER BY name")
    return [StudentGroupRead.model_validate(dict(record)) for record in records]


@app.patch("/api/v1/student-groups/{group_id}", response_model=StudentGroupRead)
async def update_student_group(
    group_id: UUID,
    update: StudentGroupUpdate,
    _: AdminUser,
    connection: DbConnection,
) -> StudentGroupRead:
    try:
        record = await _update_by_id(
            connection,
            "student_groups",
            group_id,
            update.model_dump(exclude_unset=True),
            "id, name, student_count, academic_year, is_active",
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "This group code already exists in this academic year") from None
    except asyncpg.CheckViolationError:
        raise _conflict("Student count must be positive") from None
    return StudentGroupRead.model_validate(record)


@app.delete("/api/v1/student-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_student_group(group_id: UUID, _: AdminUser, connection: DbConnection) -> None:
    await _delete_by_id(connection, "student_groups", "Student group", group_id)


@app.post(
    "/api/v1/instructors",
    response_model=InstructorRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_instructor(
    instructor: InstructorCreate,
    _: AdminUser,
    connection: DbConnection,
) -> InstructorRead:
    await _ensure_instructor_user(connection, instructor.user_id)
    try:
        record = await connection.fetchrow(
            """
            INSERT INTO instructors (user_id, department, title, is_active, max_hours, max_unavailable_slots, max_weekly_minutes)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, user_id, department, title, is_active, max_hours, max_unavailable_slots, availability_deadline, max_weekly_minutes
            """,
            instructor.user_id,
            instructor.department,
            instructor.title, instructor.is_active, instructor.max_hours, instructor.max_unavailable_slots, instructor.max_weekly_minutes,
        )
    except asyncpg.UniqueViolationError:
        raise _conflict("This user already has an instructor profile") from None
    created = _record_or_500(record)
    user = await connection.fetchrow("SELECT email FROM users WHERE id = $1", instructor.user_id)
    if user is None:
        raise _not_found("User")
    created["email"] = user["email"]
    return InstructorRead.model_validate(created)


@app.get("/api/v1/instructors", response_model=list[InstructorRead])
async def list_instructors(_: AdminUser, connection: DbConnection) -> list[InstructorRead]:
    records = await connection.fetch(
        """
        SELECT i.id, i.user_id, u.email, i.department, i.title, i.is_active, i.max_hours, i.max_unavailable_slots, i.availability_deadline, i.max_weekly_minutes
        FROM instructors AS i
        JOIN users AS u ON u.id = i.user_id
        ORDER BY u.email
        """
    )
    return [InstructorRead.model_validate(dict(record)) for record in records]


@app.patch("/api/v1/instructors/{instructor_id}", response_model=InstructorRead)
async def update_instructor(
    instructor_id: UUID,
    update: InstructorUpdate,
    _: AdminUser,
    connection: DbConnection,
) -> InstructorRead:
    changes = update.model_dump(exclude_unset=True)
    confirmed = changes.pop("confirm_deactivation", False)
    current = await _row(connection, "instructors", instructor_id)
    if changes.get("is_active") is False and current["is_active"]:
        loads = await connection.fetch("SELECT * FROM teaching_loads WHERE instructor_id = $1", instructor_id)
        schedules = await connection.fetch("SELECT * FROM schedules WHERE instructor_id = $1", instructor_id)
        if (any(row["is_active"] for row in loads) or schedules) and not confirmed:
            raise HTTPException(409, {"code": "confirmation_required", "confirmation_required": True,
                                      "message": "This instructor has active assignments. Deactivate anyway?"})
    if not changes:
        raise HTTPException(422, "Supply the fields to update")
    if "max_weekly_minutes" in changes:
        workload = await _workload(connection, instructor_id)
        if changes["max_weekly_minutes"] < max(workload.planned_minutes, workload.actual_minutes):
            raise HTTPException(409, "The limit is below the existing workload")
    if "user_id" in changes:
        await _ensure_instructor_user(connection, changes["user_id"])
    try:
        record = await _update_by_id(
            connection,
            "instructors",
            instructor_id,
            changes,
            "id, user_id, department, title, is_active, max_hours, max_unavailable_slots, availability_deadline, max_weekly_minutes",
        )
    except asyncpg.UniqueViolationError:
        raise _conflict("This user already has an instructor profile") from None
    updated = _record_or_500(record)
    user = await connection.fetchrow("SELECT email FROM users WHERE id = $1", updated["user_id"])
    if user is None:
        raise _not_found("User")
    updated["email"] = user["email"]
    return InstructorRead.model_validate(updated)


@app.delete("/api/v1/instructors/{instructor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_instructor(instructor_id: UUID, _: AdminUser, connection: DbConnection) -> None:
    await _delete_by_id(connection, "instructors", "Instructor", instructor_id)


@app.post(
    "/api/v1/time-slots",
    response_model=TimeSlotRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_time_slot(
    time_slot: TimeSlotCreate,
    _: AdminUser,
    connection: DbConnection,
) -> TimeSlotRead:
    await _validate_slot(connection, time_slot.day_of_week, time_slot.start_time, time_slot.end_time)
    try:
        record = await connection.fetchrow(
            """
            INSERT INTO time_slots (day_of_week, start_time, end_time, is_active)
            VALUES ($1, $2, $3, $4)
            RETURNING id, day_of_week, start_time, end_time, is_active
            """,
            time_slot.day_of_week,
            time_slot.start_time,
            time_slot.end_time,
            time_slot.is_active,
        )
    except (asyncpg.UniqueViolationError, asyncpg.CheckViolationError, asyncpg.ExclusionViolationError):
        raise _conflict("Time slot already exists or has an invalid time range") from None
    return TimeSlotRead.model_validate(_record_or_500(record))


@app.get("/api/v1/time-slots", response_model=list[TimeSlotRead])
async def list_time_slots(_: AdminUser, connection: DbConnection) -> list[TimeSlotRead]:
    records = await connection.fetch(
        "SELECT id, day_of_week, start_time, end_time, is_active FROM time_slots ORDER BY day_of_week, start_time"
    )
    return [TimeSlotRead.model_validate(dict(record)) for record in records]


@app.patch("/api/v1/time-slots/{time_slot_id}", response_model=TimeSlotRead)
async def update_time_slot(
    time_slot_id: UUID,
    update: TimeSlotUpdate,
    _: AdminUser,
    connection: DbConnection,
) -> TimeSlotRead:
    current = await connection.fetchrow(
        "SELECT id, day_of_week, start_time, end_time, is_active FROM time_slots WHERE id = $1",
        time_slot_id,
    )
    if current is None:
        raise _not_found("Time slot")
    changes = update.model_dump(exclude_unset=True)
    start_time = changes.get("start_time", current["start_time"])
    end_time = changes.get("end_time", current["end_time"])
    if start_time >= end_time:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid field format 'End time': must be after start time",
        )
    await _validate_slot(connection, changes.get("day_of_week", current["day_of_week"]), start_time, end_time, time_slot_id)
    await _validate_slot_change(connection, {**dict(current), **changes})
    try:
        record = await _update_by_id(
            connection,
            "time_slots",
            time_slot_id,
            changes,
            "id, day_of_week, start_time, end_time, is_active",
        )
    except (asyncpg.UniqueViolationError, asyncpg.CheckViolationError, asyncpg.ExclusionViolationError):
        raise _conflict("Time slot already exists or has an invalid time range") from None
    return TimeSlotRead.model_validate(record)


@app.delete("/api/v1/time-slots/{time_slot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_time_slot(time_slot_id: UUID, _: AdminUser, connection: DbConnection) -> None:
    current = await _row(connection, "time_slots", time_slot_id)
    await _validate_slot_change(connection, current, removing=True)
    await _delete_by_id(connection, "time_slots", "Time slot", time_slot_id)


async def _fetch_required_rows(
    connection: asyncpg.Connection,
    table: str,
    columns: str,
    entity_name: str,
    identifiers: Iterable[UUID],
) -> list[asyncpg.Record]:
    requested_ids = list(set(identifiers))
    records = await connection.fetch(
        f"SELECT {columns} FROM {table} WHERE id = ANY($1::uuid[])",
        requested_ids,
    )
    found_ids = {record["id"] for record in records}
    missing = set(requested_ids) - found_ids
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{entity_name} was not found: {', '.join(map(str, sorted(missing, key=str)))}",
        )
    return records


@app.post(
    "/api/v1/schedules/generate",
    response_model=ScheduleGenerationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_schedules(
    request_data: ScheduleGenerationRequest,
    _: AdminUser,
    connection: DbConnection,
) -> ScheduleGenerationResponse:
    groups = await connection.fetch("SELECT * FROM student_groups")
    courses = {row["id"]: row for row in await connection.fetch("SELECT * FROM courses")}
    instructors = await connection.fetch("SELECT * FROM instructors")
    rooms = await connection.fetch("SELECT * FROM rooms")
    time_slots = await connection.fetch("SELECT * FROM time_slots")
    availability = await connection.fetch("SELECT * FROM instructor_availability")
    if request_data.assignments is None:
        requirement_rows = [dict(row) for row in await connection.fetch("SELECT * FROM teaching_loads") if row["is_active"]]
    else:
        requirement_rows = [assignment.model_dump() for assignment in request_data.assignments]
    if not requirement_rows:
        raise HTTPException(422, "No active teaching loads to generate a schedule")
    for requirement in requirement_rows:
        course = courses.get(requirement["course_id"])
        if course is None:
            raise _not_found("Course")
        if not course["is_active"]:
            raise HTTPException(409, "The course is inactive")
        requirement["course_max_hours"] = course["max_hours"]
    slots_by_id = {row["id"]: row for row in time_slots}
    existing_entries = []
    if not request_data.replace_existing:
        for row in await connection.fetch("SELECT * FROM schedules"):
            slot = slots_by_id[row["time_slot_id"]]
            existing_entries.append({**dict(row), "day_of_week": slot["day_of_week"],
                                     "start_time": slot["start_time"], "end_time": slot["end_time"]})

    try:
        generated = generate_schedule_matrix(
            groups,
            requirement_rows,
            instructors,
            rooms,
            time_slots,
            existing_entries,
            availability,
        )
    except SchedulingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=jsonable_encoder({"message": str(exc), "unscheduled": exc.unscheduled}),
        ) from None

    try:
        async with connection.transaction():
            if request_data.replace_existing:
                await connection.execute("DELETE FROM schedules")
            for entry in generated:
                await connection.execute(
                    """
                    INSERT INTO schedules (course_id, room_id, instructor_id, group_id, time_slot_id)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    entry["course_id"],
                    entry["room_id"],
                    entry["instructor_id"],
                    entry["group_id"],
                    entry["time_slot_id"],
                )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A concurrent schedule change created a resource conflict; retry generation",
        ) from None

    return ScheduleGenerationResponse(
        generated_count=len(generated),
        replace_existing=request_data.replace_existing,
        entries=generated,
    )


class ScheduleBatchDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule_ids: list[UUID] = Field(min_length=1, max_length=1000)


class ScheduleDeleteResponse(BaseModel):
    deleted_ids: list[UUID]
    deleted_count: int


async def _delete_schedules(
    connection: asyncpg.Connection, schedule_ids: list[UUID]
) -> ScheduleDeleteResponse:
    identifiers = list(dict.fromkeys(schedule_ids))
    try:
        async with connection.transaction():
            # Child tables, if introduced, must reference schedules(id) ON DELETE CASCADE.
            # The current schema has no child tables; reference data is preserved.
            records = await connection.fetch(
                "DELETE FROM schedules WHERE id = ANY($1::uuid[]) RETURNING id",
                identifiers,
            )
            deleted = {record["id"] for record in records}
            missing = [str(identifier) for identifier in identifiers if identifier not in deleted]
            if missing:
                # Raising inside the transaction rolls back every deletion in this batch.
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"message": "Schedules were not found; nothing was deleted", "missing_ids": missing},
                )
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Related records prevent deletion; configure their foreign keys with ON DELETE CASCADE",
        ) from None
    return ScheduleDeleteResponse(deleted_ids=identifiers, deleted_count=len(identifiers))


# Keep this static path before /{schedule_id} so 'batch' is not parsed as a UUID.
@app.delete("/api/v1/schedules/batch", response_model=ScheduleDeleteResponse)
async def delete_schedules_batch(
    request_data: ScheduleBatchDeleteRequest, _: AdminUser, connection: DbConnection
) -> ScheduleDeleteResponse:
    return await _delete_schedules(connection, request_data.schedule_ids)


@app.delete("/api/v1/schedules/{schedule_id}", response_model=ScheduleDeleteResponse)
async def delete_schedule(
    schedule_id: UUID, _: AdminUser, connection: DbConnection
) -> ScheduleDeleteResponse:
    return await _delete_schedules(connection, [schedule_id])


@app.get("/api/v1/schedules", response_model=list[ScheduleRead])
async def list_schedules(
    user: AuthenticatedUser,
    connection: DbConnection,
    group_id: UUID | None = Query(default=None),
    room_id: UUID | None = Query(default=None),
    instructor_id: UUID | None = Query(default=None),
) -> list[ScheduleRead]:
    if user.role != UserRole.ADMIN:
        if user.role != UserRole.INSTRUCTOR:
            # Student membership is not modeled yet; caller-provided filters
            # must never substitute for a server-authorized group membership.
            raise HTTPException(403, "Personal student timetables are not available")
        own_instructor_id = await _availability_owner(connection, user)
        if instructor_id is not None and instructor_id != own_instructor_id:
            raise HTTPException(403, "You cannot view another instructor's timetable")
        instructor_id = own_instructor_id
    filters: list[str] = []
    values: list[UUID] = []
    for column, value in (
        ("s.group_id", group_id),
        ("s.room_id", room_id),
        ("s.instructor_id", instructor_id),
    ):
        if value is not None:
            values.append(value)
            filters.append(f"{column} = ${len(values)}")
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    records = await connection.fetch(
        f"{SCHEDULE_SELECT} {where_clause} ORDER BY t.day_of_week, t.start_time, r.building, r.name",
        *values,
    )
    return [ScheduleRead.model_validate(dict(record)) for record in records]


@app.get("/api/v1/schedules/conflicts", response_model=ConflictResponse)
async def list_schedule_conflicts(
    _: AdminUser,
    connection: DbConnection,
) -> ConflictResponse:
    records = await connection.fetch(
        """
        SELECT s.id, s.course_id, s.room_id, s.instructor_id, s.group_id, s.time_slot_id,
               r.capacity AS room_capacity, g.student_count,
               t.day_of_week, t.start_time, t.end_time
        FROM schedules AS s
        JOIN rooms AS r ON r.id = s.room_id
        JOIN student_groups AS g ON g.id = s.group_id
        JOIN time_slots AS t ON t.id = s.time_slot_id
        """
    )
    return ConflictResponse(conflicts=detect_schedule_conflicts(records))


async def _row(connection, table: str, identifier: UUID) -> dict:
    record = await connection.fetchrow(f"SELECT * FROM {table} WHERE id = $1", identifier)
    if record is None:
        raise _not_found(table)
    return dict(record)


def _minutes(slot) -> float:
    start, end = slot["start_time"], slot["end_time"]
    return ((end.hour - start.hour) * 3600 + (end.minute - start.minute) * 60 + end.second - start.second) / 60


def _instructor_limit(instructor) -> int:
    return instructor.get("max_weekly_minutes") or instructor["max_hours"] * 60


async def _workload(connection, instructor_id: UUID) -> WorkloadRead:
    instructor = await _row(connection, "instructors", instructor_id)
    loads = await connection.fetch("SELECT * FROM teaching_loads WHERE instructor_id = $1", instructor_id)
    entries = await connection.fetch("SELECT * FROM schedules WHERE instructor_id = $1", instructor_id)
    slots = {row["id"]: row for row in await connection.fetch("SELECT * FROM time_slots")}
    return WorkloadRead(
        instructor_id=instructor_id, max_hours=instructor["max_hours"],
        max_weekly_minutes=_instructor_limit(instructor),
        max_unavailable_slots=instructor["max_unavailable_slots"],
        availability_deadline=instructor["availability_deadline"],
        planned_minutes=sum(row["sessions_per_week"] * row["session_minutes"] for row in loads if row["is_active"]),
        actual_minutes=sum(_minutes(slots[row["time_slot_id"]]) for row in entries),
    )


async def _availability(connection, instructor_id: UUID) -> AvailabilityRead:
    workload = await _workload(connection, instructor_id)
    slots = [dict(row) for row in await connection.fetch("SELECT * FROM time_slots ORDER BY day_of_week, start_time")
             if row["is_active"] and row["day_of_week"] <= 6]
    records = await connection.fetch("SELECT * FROM instructor_availability WHERE instructor_id = $1", instructor_id)
    states = {row["time_slot_id"]: row["status"] for row in records}
    return AvailabilityRead(
        slots=slots,
        entries=[AvailabilityEntry(time_slot_id=row["id"], status=states.get(row["id"], "available")) for row in slots],
        max_unavailable_slots=min(workload.max_unavailable_slots, max(0, len(slots) - 1)),
        min_available_minutes=max(workload.planned_minutes, workload.actual_minutes),
    )


async def _availability_owner(connection, user: CurrentUser, instructor_id: UUID | None = None) -> UUID:
    if user.role != UserRole.INSTRUCTOR:
        raise HTTPException(403, "Only the instructor can edit their own availability")
    records = await connection.fetch("SELECT * FROM instructors WHERE user_id = $1", user.id)
    if not records or not records[0]["is_active"]:
        raise HTTPException(403, "No active instructor profile")
    own_id = records[0]["id"]
    if instructor_id is not None and own_id != instructor_id:
        raise HTTPException(403, "You cannot edit another instructor's availability")
    return own_id


async def _save_availability(connection, instructor_id: UUID, payload: AvailabilityWrite) -> AvailabilityRead:
    async with connection.transaction():
        instructor = await _row(connection, "instructors", instructor_id)
        deadline = instructor["availability_deadline"]
        if deadline is not None and datetime.now(timezone.utc) >= deadline:
            raise HTTPException(409, "The availability editing deadline has passed")
        matrix = await _availability(connection, instructor_id)
        slots = {slot.id: slot for slot in matrix.slots}
        states = {entry.time_slot_id: entry.status.value for entry in payload.entries}
        if set(states) != set(slots):
            raise HTTPException(422, "Include each active Monday–Saturday slot exactly once; reload the matrix")
        blocked = {identifier for identifier, state in states.items() if state == "unavailable"}
        if len(blocked) > matrix.max_unavailable_slots:
            raise HTTPException(422, "The unavailable slot limit has been exceeded")
        available_minutes = sum(_minutes(slot.model_dump()) for identifier, slot in slots.items() if identifier not in blocked)
        if available_minutes < matrix.min_available_minutes:
            raise HTTPException(422, "Insufficient available time for the teaching load")
        loads = await connection.fetch("SELECT * FROM teaching_loads WHERE instructor_id = $1", instructor_id)
        durations = {}
        for load in loads:
            if load["is_active"]:
                duration = load["session_minutes"]
                durations[duration] = durations.get(duration, 0) + load["sessions_per_week"]
        for duration, count in durations.items():
            if sum(identifier not in blocked and _minutes(slot.model_dump()) == duration for identifier, slot in slots.items()) < count:
                raise HTTPException(422, "Not enough available slots of the required duration")
        scheduled = await connection.fetch("SELECT * FROM schedules WHERE instructor_id = $1", instructor_id)
        if any(row["time_slot_id"] in blocked for row in scheduled):
            raise HTTPException(409, "This slot already has a class; update the schedule first")
        await connection.execute("DELETE FROM instructor_availability WHERE instructor_id = $1", instructor_id)
        for identifier, state in states.items():
            await connection.execute(
                "INSERT INTO instructor_availability (instructor_id, time_slot_id, status) VALUES ($1, $2, $3)",
                instructor_id, identifier, state,
            )
        return await _availability(connection, instructor_id)


@app.get("/api/v1/instructors/me/availability", response_model=AvailabilityRead)
async def get_my_availability(user: AuthenticatedUser, connection: DbConnection):
    return await _availability(connection, await _availability_owner(connection, user))


@app.post("/api/v1/instructors/me/availability", response_model=AvailabilityRead)
@app.put("/api/v1/instructors/me/availability", response_model=AvailabilityRead)
async def put_my_availability(payload: AvailabilityWrite, user: AuthenticatedUser, connection: DbConnection):
    return await _save_availability(connection, await _availability_owner(connection, user), payload)


@app.get("/api/v1/instructors/{instructor_id}/availability", response_model=AvailabilityRead)
async def get_instructor_availability(instructor_id: UUID, user: AuthenticatedUser, connection: DbConnection):
    return await _availability(connection, await _availability_owner(connection, user, instructor_id))


@app.post("/api/v1/instructors/{instructor_id}/availability", response_model=AvailabilityRead)
@app.put("/api/v1/instructors/{instructor_id}/availability", response_model=AvailabilityRead)
async def put_instructor_availability(instructor_id: UUID, payload: AvailabilityWrite, user: AuthenticatedUser, connection: DbConnection):
    return await _save_availability(connection, await _availability_owner(connection, user, instructor_id), payload)


@app.patch("/api/v1/instructors/{instructor_id}/status", response_model=InstructorRead)
async def set_instructor_status(instructor_id: UUID, update: InstructorStatusUpdate, user: AdminUser, connection: DbConnection):
    return await update_instructor(instructor_id, InstructorUpdate(**update.model_dump()), user, connection)


@app.get("/api/v1/instructors/{instructor_id}/workload", response_model=WorkloadRead)
async def get_instructor_workload(instructor_id: UUID, user: AuthenticatedUser, connection: DbConnection):
    if user.role != UserRole.ADMIN:
        await _availability_owner(connection, user, instructor_id)
    return await _workload(connection, instructor_id)


@app.put("/api/v1/instructors/{instructor_id}/workload", response_model=WorkloadRead)
async def set_instructor_workload(instructor_id: UUID, update: WorkloadUpdate, _: AdminUser, connection: DbConnection):
    current = await _workload(connection, instructor_id)
    changes = update.model_dump(exclude_unset=True)
    if "max_hours" in changes:
        changes["max_weekly_minutes"] = None
    limit = changes["max_hours"] * 60 if "max_hours" in changes else changes.get("max_weekly_minutes", current.max_weekly_minutes)
    if limit < max(current.planned_minutes, current.actual_minutes):
        raise HTTPException(409, "The limit is below the existing workload")
    matrix = await _availability(connection, instructor_id)
    blocked_count = sum(entry.status == AvailabilityStatus.UNAVAILABLE for entry in matrix.entries)
    if changes.get("max_unavailable_slots", current.max_unavailable_slots) < blocked_count:
        raise HTTPException(409, "The new limit is below the number of unavailable slots")
    await _update_by_id(connection, "instructors", instructor_id, changes, "*")
    return await _workload(connection, instructor_id)


async def _validate_teaching_load(connection, data: TeachingLoadCreate, load_id: UUID | None = None):
    instructor = await _row(connection, "instructors", data.instructor_id)
    group = await _row(connection, "student_groups", data.group_id)
    course = await _row(connection, "courses", data.course_id)
    if not data.is_active:
        return
    if not all(row["is_active"] for row in (instructor, group, course)):
        raise HTTPException(409, "The course, group and instructor must be active")
    loads = [row for row in await connection.fetch("SELECT * FROM teaching_loads") if row["id"] != load_id and row["is_active"]]
    minutes = data.sessions_per_week * data.session_minutes
    total = minutes + sum(row["sessions_per_week"] * row["session_minutes"] for row in loads if row["instructor_id"] == data.instructor_id)
    if total > _instructor_limit(instructor):
        raise HTTPException(409, "The instructor workload limit has been exceeded")
    course_total = minutes + sum(row["sessions_per_week"] * row["session_minutes"] for row in loads if row["course_id"] == data.course_id and row["group_id"] == data.group_id)
    if course_total > course["max_hours"] * 60:
        raise HTTPException(409, "The course and group curriculum limit has been exceeded")
    matrix = await _availability(connection, data.instructor_id)
    blocked = {entry.time_slot_id for entry in matrix.entries if entry.status == AvailabilityStatus.UNAVAILABLE}
    eligible = [slot for slot in matrix.slots if slot.id not in blocked]
    if sum(_minutes(slot.model_dump()) for slot in eligible) < total:
        raise HTTPException(409, "Available time is insufficient for the workload")
    durations = {data.session_minutes: data.sessions_per_week}
    for row in loads:
        if row["instructor_id"] == data.instructor_id:
            durations[row["session_minutes"]] = durations.get(row["session_minutes"], 0) + row["sessions_per_week"]
    if any(sum(_minutes(slot.model_dump()) == duration for slot in eligible) < count for duration, count in durations.items()):
        raise HTTPException(409, "Not enough available slots of the required duration")


@app.get("/api/v1/teaching-loads", response_model=list[TeachingLoadRead])
async def list_teaching_loads(_: AdminUser, connection: DbConnection, instructor_id: UUID | None = None):
    rows = await connection.fetch("SELECT * FROM teaching_loads ORDER BY id")
    return [dict(row) for row in rows if instructor_id is None or row["instructor_id"] == instructor_id]


@app.post("/api/v1/teaching-loads", response_model=TeachingLoadRead, status_code=201)
async def create_teaching_load(payload: TeachingLoadCreate, _: AdminUser, connection: DbConnection):
    await _validate_teaching_load(connection, payload)
    data = payload.model_dump()
    columns = ", ".join(data)
    parameters = ", ".join(f"${index}" for index in range(1, len(data) + 1))
    try:
        return dict(await connection.fetchrow(f"INSERT INTO teaching_loads ({columns}) VALUES ({parameters}) RETURNING *", *data.values()))
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "This teaching load already exists") from None


@app.put("/api/v1/teaching-loads/{load_id}", response_model=TeachingLoadRead)
async def replace_teaching_load(load_id: UUID, payload: TeachingLoadCreate, _: AdminUser, connection: DbConnection):
    await _row(connection, "teaching_loads", load_id)
    await _validate_teaching_load(connection, payload, load_id)
    try:
        return await _update_by_id(connection, "teaching_loads", load_id, payload.model_dump(), "*")
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "This teaching load already exists") from None


@app.delete("/api/v1/teaching-loads/{load_id}", status_code=204)
async def delete_teaching_load(load_id: UUID, _: AdminUser, connection: DbConnection) -> None:
    await _delete_by_id(connection, "teaching_loads", "Teaching loads", load_id)


async def _validate_slot(connection, day, start, end, slot_id=None):
    for slot in await connection.fetch("SELECT * FROM time_slots"):
        if slot["id"] != slot_id and slot["day_of_week"] == day and start < slot["end_time"] and end > slot["start_time"]:
            raise HTTPException(409, "Time slots overlap")


async def _validate_slot_change(connection, proposed, removing=False):
    slots = {row["id"]: dict(row) for row in await connection.fetch("SELECT * FROM time_slots")}
    original = slots[proposed["id"]]
    schedules = await connection.fetch("SELECT * FROM schedules")
    if (removing or original != proposed) and any(row["time_slot_id"] == proposed["id"] for row in schedules):
        raise HTTPException(409, "This slot is scheduled; remove or update its classes first")
    if removing:
        slots.pop(proposed["id"])
    else:
        slots[proposed["id"]] = proposed
    active = [row for row in slots.values() if row["is_active"] and row["day_of_week"] <= 6]
    loads = await connection.fetch("SELECT * FROM teaching_loads")
    entries = await connection.fetch("SELECT * FROM instructor_availability")
    for instructor in await connection.fetch("SELECT * FROM instructors"):
        blocked = {row["time_slot_id"] for row in entries if row["instructor_id"] == instructor["id"] and row["status"] == "unavailable"}
        eligible = [row for row in active if row["id"] not in blocked]
        if sum(row["id"] in blocked for row in active) > min(instructor["max_unavailable_slots"], max(0, len(active)-1)):
            raise HTTPException(409, "This change exceeds the instructor availability limit")
        requirements = {}
        for load in loads:
            if load["instructor_id"] == instructor["id"] and load["is_active"]:
                duration = load["session_minutes"]
                requirements[duration] = requirements.get(duration, 0) + load["sessions_per_week"]
        if any(sum(_minutes(slot) == duration for slot in eligible) < count for duration, count in requirements.items()):
            raise HTTPException(409, "Changing this slot leaves insufficient time for the workload")

async def _validate_manual_schedule(connection, data: ScheduleDraft, exclude_id: UUID | None = None):
    course = await _row(connection, 'courses', data.course_id)
    room = await _row(connection, 'rooms', data.room_id)
    instructor = await _row(connection, 'instructors', data.instructor_id)
    group = await _row(connection, 'student_groups', data.group_id)
    slot = await _row(connection, 'time_slots', data.time_slot_id)
    if not all(row['is_active'] for row in (course, room, instructor, group, slot)) or slot['day_of_week'] > 6:
        raise HTTPException(409, 'Course, room, instructor, group and time slot must be active; Sundays are not allowed')
    if room['capacity'] < group['student_count']:
        raise HTTPException(409, 'Room capacity is below the group size')
    availability = await connection.fetch('SELECT * FROM instructor_availability WHERE instructor_id = $1', data.instructor_id)
    if any(row['time_slot_id'] == data.time_slot_id and row['status'] == 'unavailable' for row in availability):
        raise HTTPException(409, 'The instructor is unavailable in this time slot')
    slots = {row['id']: row for row in await connection.fetch('SELECT * FROM time_slots')}
    others = [row for row in await connection.fetch('SELECT * FROM schedules') if row['id'] != exclude_id]
    for existing in others:
        other = slots[existing['time_slot_id']]
        if slot['day_of_week'] == other['day_of_week'] and slot['start_time'] < other['end_time'] and slot['end_time'] > other['start_time']:
            for field, label in [('room_id', 'Room'), ('instructor_id', 'Instructor'), ('group_id', 'Group')]:
                if existing[field] == getattr(data, field):
                    raise HTTPException(409, f'{label} already has a class at this time')
    minutes = _minutes(slot)
    actual = minutes + sum(_minutes(slots[row['time_slot_id']]) for row in others if row['instructor_id'] == data.instructor_id)
    if actual > _instructor_limit(instructor):
        raise HTTPException(409, 'The instructor workload limit has been exceeded')
    course_minutes = minutes + sum(_minutes(slots[row['time_slot_id']]) for row in others if row['course_id'] == data.course_id and row['group_id'] == data.group_id)
    if course_minutes > course['max_hours'] * 60:
        raise HTTPException(409, 'The course and group curriculum limit has been exceeded')


@app.post('/api/v1/schedules', response_model=ManualScheduleRead, status_code=201)
async def create_manual_schedule(payload: ScheduleDraft, _: AdminUser, connection: DbConnection):
    await _validate_manual_schedule(connection, payload)
    data = payload.model_dump()
    columns = ', '.join(data)
    parameters = ', '.join(f'${index}' for index in range(1, len(data)+1))
    try:
        return dict(await connection.fetchrow(f'INSERT INTO schedules ({columns}) VALUES ({parameters}) RETURNING *', *data.values()))
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, 'Room, instructor or group already has a class in this slot') from None


@app.patch('/api/v1/schedules/{schedule_id}', response_model=ManualScheduleRead)
async def update_manual_schedule(schedule_id: UUID, payload: ScheduleUpdate, _: AdminUser, connection: DbConnection):
    current = await _row(connection, 'schedules', schedule_id)
    changes = payload.model_dump(exclude_unset=True)
    merged = ScheduleDraft(**{key: changes.get(key, current[key]) for key in ScheduleDraft.model_fields})
    await _validate_manual_schedule(connection, merged, schedule_id)
    try:
        return await _update_by_id(connection, 'schedules', schedule_id, changes, '*')
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, 'Room, instructor or group already has a class in this slot') from None
