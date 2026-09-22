import os
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from auth import create_access_token, get_current_user, hash_password, require_roles, verify_password
from models import (
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


async def get_connection(request: Request) -> AsyncIterator[asyncpg.Connection]:
    pool: asyncpg.Pool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not ready",
        )
    async with pool.acquire() as connection:
        yield connection


DbConnection = Annotated[asyncpg.Connection, Depends(get_connection)]
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
    await _delete_by_id(connection, "users", "User", user_id)


@app.post("/api/v1/courses", response_model=CourseRead, status_code=status.HTTP_201_CREATED)
async def create_course(course: CourseCreate, _: AdminUser, connection: DbConnection) -> CourseRead:
    if await connection.fetchval("SELECT 1 FROM courses WHERE code = $1", course.code):
        raise _conflict("Course code already exists")
    try:
        record = await connection.fetchrow(
            """
            INSERT INTO courses (code, name, description)
            VALUES ($1, $2, $3)
            RETURNING id, code, name, description
            """,
            course.code,
            course.name,
            course.description,
        )
    except asyncpg.UniqueViolationError:
        raise _conflict("Course code already exists") from None
    return CourseRead.model_validate(_record_or_500(record))


@app.get("/api/v1/courses", response_model=list[CourseRead])
async def list_courses(_: AdminUser, connection: DbConnection) -> list[CourseRead]:
    records = await connection.fetch("SELECT id, code, name, description FROM courses ORDER BY code")
    return [CourseRead.model_validate(dict(record)) for record in records]


@app.patch("/api/v1/courses/{course_id}", response_model=CourseRead)
async def update_course(
    course_id: UUID,
    update: CourseUpdate,
    _: AdminUser,
    connection: DbConnection,
) -> CourseRead:
    try:
        record = await _update_by_id(
            connection,
            "courses",
            course_id,
            update.model_dump(exclude_unset=True),
            "id, code, name, description",
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
            INSERT INTO rooms (name, building, capacity, is_active)
            VALUES ($1, $2, $3, $4)
            RETURNING id, name, building, capacity, is_active
            """,
            room.name,
            room.building,
            room.capacity,
            room.is_active,
        )
    except asyncpg.UniqueViolationError:
        raise _conflict("A room with this name already exists in the building") from None
    return RoomRead.model_validate(_record_or_500(record))


@app.get("/api/v1/rooms", response_model=list[RoomRead])
async def list_rooms(_: AdminUser, connection: DbConnection) -> list[RoomRead]:
    records = await connection.fetch(
        "SELECT id, name, building, capacity, is_active FROM rooms ORDER BY building, name"
    )
    return [RoomRead.model_validate(dict(record)) for record in records]


@app.patch("/api/v1/rooms/{room_id}", response_model=RoomRead)
async def update_room(
    room_id: UUID,
    update: RoomUpdate,
    _: AdminUser,
    connection: DbConnection,
) -> RoomRead:
    try:
        record = await _update_by_id(
            connection,
            "rooms",
            room_id,
            update.model_dump(exclude_unset=True),
            "id, name, building, capacity, is_active",
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
            INSERT INTO student_groups (name, student_count)
            VALUES ($1, $2)
            RETURNING id, name, student_count
            """,
            group.name,
            group.student_count,
        )
    except asyncpg.UniqueViolationError:
        raise _conflict("Student group name already exists") from None
    return StudentGroupRead.model_validate(_record_or_500(record))


@app.get("/api/v1/student-groups", response_model=list[StudentGroupRead])
async def list_student_groups(_: AdminUser, connection: DbConnection) -> list[StudentGroupRead]:
    records = await connection.fetch("SELECT id, name, student_count FROM student_groups ORDER BY name")
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
            "id, name, student_count",
        )
    except asyncpg.UniqueViolationError:
        raise _conflict("Student group name already exists") from None
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
            INSERT INTO instructors (user_id, department, title)
            VALUES ($1, $2, $3)
            RETURNING id, user_id, department, title
            """,
            instructor.user_id,
            instructor.department,
            instructor.title,
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
        SELECT i.id, i.user_id, u.email, i.department, i.title
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
    if "user_id" in changes:
        await _ensure_instructor_user(connection, changes["user_id"])
    try:
        record = await _update_by_id(
            connection,
            "instructors",
            instructor_id,
            changes,
            "id, user_id, department, title",
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
    try:
        record = await connection.fetchrow(
            """
            INSERT INTO time_slots (day_of_week, start_time, end_time)
            VALUES ($1, $2, $3)
            RETURNING id, day_of_week, start_time, end_time
            """,
            time_slot.day_of_week,
            time_slot.start_time,
            time_slot.end_time,
        )
    except (asyncpg.UniqueViolationError, asyncpg.CheckViolationError):
        raise _conflict("Time slot already exists or has an invalid time range") from None
    return TimeSlotRead.model_validate(_record_or_500(record))


@app.get("/api/v1/time-slots", response_model=list[TimeSlotRead])
async def list_time_slots(_: AdminUser, connection: DbConnection) -> list[TimeSlotRead]:
    records = await connection.fetch(
        "SELECT id, day_of_week, start_time, end_time FROM time_slots ORDER BY day_of_week, start_time"
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
        "SELECT id, day_of_week, start_time, end_time FROM time_slots WHERE id = $1",
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
            detail="end_time must be later than start_time",
        )
    try:
        record = await _update_by_id(
            connection,
            "time_slots",
            time_slot_id,
            changes,
            "id, day_of_week, start_time, end_time",
        )
    except (asyncpg.UniqueViolationError, asyncpg.CheckViolationError):
        raise _conflict("Time slot already exists or has an invalid time range") from None
    return TimeSlotRead.model_validate(record)


@app.delete("/api/v1/time-slots/{time_slot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_time_slot(time_slot_id: UUID, _: AdminUser, connection: DbConnection) -> None:
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
    assignments = request_data.assignments
    groups = await _fetch_required_rows(
        connection,
        "student_groups",
        "id, name, student_count",
        "Student group",
        (assignment.group_id for assignment in assignments),
    )
    await _fetch_required_rows(
        connection,
        "courses",
        "id, code, name",
        "Course",
        (assignment.course_id for assignment in assignments),
    )
    instructors = await _fetch_required_rows(
        connection,
        "instructors",
        "id, user_id, department, title",
        "Instructor",
        (assignment.instructor_id for assignment in assignments),
    )
    rooms = await connection.fetch(
        "SELECT id, name, building, capacity, is_active FROM rooms WHERE is_active = TRUE"
    )
    time_slots = await connection.fetch(
        "SELECT id, day_of_week, start_time, end_time FROM time_slots ORDER BY day_of_week, start_time"
    )
    requirement_rows = [assignment.model_dump() for assignment in assignments]

    if request_data.replace_existing:
        existing_entries: list[asyncpg.Record] = []
    else:
        existing_entries = await connection.fetch(
            """
            SELECT s.id, s.course_id, s.room_id, s.instructor_id, s.group_id, s.time_slot_id,
                   t.day_of_week, t.start_time, t.end_time
            FROM schedules AS s
            JOIN time_slots AS t ON t.id = s.time_slot_id
            """
        )

    try:
        generated = generate_schedule_matrix(
            groups,
            requirement_rows,
            instructors,
            rooms,
            time_slots,
            existing_entries,
        )
    except SchedulingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": str(exc), "unscheduled": exc.unscheduled},
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


@app.get("/api/v1/schedules", response_model=list[ScheduleRead])
async def list_schedules(
    _: AuthenticatedUser,
    connection: DbConnection,
    group_id: UUID | None = Query(default=None),
    room_id: UUID | None = Query(default=None),
    instructor_id: UUID | None = Query(default=None),
) -> list[ScheduleRead]:
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
