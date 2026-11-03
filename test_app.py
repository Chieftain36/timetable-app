from datetime import datetime, time, timezone
from uuid import uuid4
from copy import deepcopy
from contextlib import asynccontextmanager
import re
import asyncpg

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
        self.student_groups = []
        self.instructors = []
        self.time_slots = []
        self.teaching_loads = []
        self.instructor_availability = []
        self.schedules = []
        self.fail_availability_insert = False
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
    """Small stateful asyncpg adapter; domain rules remain in production endpoints.

    HTTP tests exercise FastAPI dependencies, validation, authorization and business
    logic. PostgreSQL-specific constraints require the optional live DB tests.
    """
    def __init__(self, database):
        self.database = database

    @asynccontextmanager
    async def transaction(self):
        snapshot = deepcopy(self.database.__dict__)
        try:
            yield self
        except BaseException:
            self.database.__dict__.clear()
            self.database.__dict__.update(snapshot)
            raise

    def table(self, name):
        value = getattr(self.database, name)
        return list(value.values()) if isinstance(value, dict) else value

    def select(self, query, args):
        if "JOIN users" in query:
            return [{**row, "email": next(user["email"] for user in self.database.users.values() if user["id"] == row["user_id"])} for row in self.database.instructors]
        table = re.search(r"FROM (\w+)", query).group(1)
        rows = self.table(table)
        where = re.search(r"WHERE (\w+) = \$(\d+)", query)
        if where:
            column, index = where.groups()
            rows = [row for row in rows if row[column] == args[int(index)-1]]
        excluded = re.search(r"AND id <> \$(\d+)", query)
        if excluded:
            rows = [row for row in rows if row['id'] != args[int(excluded.group(1))-1]]
        projection = re.match(r"\s*SELECT ([\w, ]+) FROM", query)
        if projection and projection.group(1) != '1':
            columns = [column.strip() for column in projection.group(1).split(',')]
            rows = [{column: row[column] for column in columns} for row in rows]
        return deepcopy(rows)

    def insert(self, query, args):
        match = re.search(r"INSERT INTO (\w+) \(([^)]+)\)", query)
        table, columns = match.groups()
        row = dict(zip([column.strip() for column in columns.split(',')], args))
        row['id'] = uuid4()
        defaults = {
            'courses': {'description': None, 'is_active': True, 'max_hours': 40},
            'student_groups': {'is_active': True},
            'instructors': {'is_active': True, 'max_hours': 40, 'max_unavailable_slots': 10, 'availability_deadline': None, 'title': None, 'max_weekly_minutes': None},
            'time_slots': {'is_active': True},
            'schedules': {'created_at': datetime.now(timezone.utc)},
        }
        row = {**defaults.get(table, {}), **row}
        self.validate_unique(table, row)
        if table == 'instructor_availability' and self.database.fail_availability_insert:
            raise RuntimeError('Injected database failure')
        self.table(table).append(row)
        return deepcopy(row)

    def validate_unique(self, table, row):
        keys = {'student_groups': ('name','academic_year'), 'instructors': ('user_id',),
                'teaching_loads': ('course_id','group_id','instructor_id','delivery_mode'),
                'instructor_availability': ('instructor_id','time_slot_id')}.get(table)
        if keys and any(old['id'] != row['id'] and all(str(old[key]).strip().upper() == str(row[key]).strip().upper() for key in keys) for old in self.table(table)):
            raise asyncpg.UniqueViolationError('duplicate')

    async def fetchval(self, query, *args):
        return 1 if self.select(query, args) else None

    async def fetchrow(self, query, *args):
        if query.lstrip().startswith('INSERT'):
            return self.insert(query, args)
        if query.lstrip().startswith('UPDATE'):
            table = re.search(r"UPDATE (\w+)", query).group(1)
            row = next((row for row in self.table(table) if row['id'] == args[-1]), None)
            if row is None:
                return None
            updated = dict(row)
            for column, index in re.findall(r"(\w+) = \$(\d+)", query.split(' WHERE ')[0]):
                updated[column] = args[int(index)-1]
            self.validate_unique(table, updated)
            row.update(updated)
            returning = query.split(' RETURNING ')[1]
            result = row if returning == '*' else {key.strip(): row[key.strip()] for key in returning.split(',')}
            return deepcopy(result)
        rows = self.select(query, args)
        return rows[0] if rows else None

    async def fetch(self, query, *args):
        if query.lstrip().startswith('DELETE'):
            return self.delete(query, args)
        return self.select(query, args)

    def delete(self, query, args):
        table = re.search(r"DELETE FROM (\w+)", query).group(1)
        rows = self.table(table)
        if 'ANY(' in query:
            deleted = [row for row in rows if row['id'] in args[0]]
        elif 'WHERE' in query:
            column = re.search(r"WHERE (\w+) =", query).group(1)
            deleted = [row for row in rows if row[column] == args[0]]
        else:
            deleted = list(rows)
        rows[:] = [row for row in rows if row not in deleted]
        return deepcopy(deleted)

    async def execute(self, query, *args):
        if query.lstrip().startswith('INSERT'):
            self.insert(query, args)
            return 'INSERT 0 1'
        if query.lstrip().startswith('DELETE'):
            return f"DELETE {len(self.delete(query, args))}"
        if 'pg_advisory_xact_lock' in query:
            return 'SELECT 1'
        raise AssertionError(query)


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


def seed_sprint2(database):
    teacher_user = {**database.users['student@example.com'], 'id': uuid4(), 'email': 'teacher@example.com', 'role': 'instructor'}
    database.users[teacher_user['email']] = teacher_user
    teacher = dict(id=uuid4(), user_id=teacher_user['id'], department='IT', title=None,
                   is_active=True, max_hours=40, max_unavailable_slots=2, availability_deadline=None, max_weekly_minutes=None)
    group = dict(id=uuid4(), name='CS-101', academic_year='2026-2027', student_count=20, is_active=True)
    course = dict(id=uuid4(), code='CS-1', name='Math', description=None, is_active=True, max_hours=40)
    room = dict(id=uuid4(), name='101', building='A', capacity=30, is_active=True)
    database.instructors.append(teacher)
    database.student_groups.append(group)
    database.courses.append(course)
    database.rooms.append(room)
    database.time_slots.extend(dict(id=uuid4(), day_of_week=1, start_time=time(hour), end_time=time(hour+1), is_active=True) for hour in (9,10,11))
    return teacher, group, course, room


def headers_for(database, email='admin@example.com'):
    from auth import create_access_token
    from models import CurrentUser
    user = database.users[email]
    return {'Authorization': 'Bearer ' + create_access_token(CurrentUser(id=user['id'], email=user['email'], role=user['role']))}


@pytest.mark.anyio
async def test_group_year_uniqueness_and_edit(app_with_fake_database):
    application, database = app_with_fake_database
    async with AsyncClient(transport=ASGITransport(app=application), base_url='http://test', headers=headers_for(database)) as client:
        payload = dict(code='cs-101', academic_year='2026-2027', student_count=20)
        first = await client.post('/api/v1/student-groups', json=payload)
        assert first.status_code == 201, first.text
        assert first.json()['code'] == first.json()['name'] == 'CS-101'
        assert (await client.post('/api/v1/student-groups', json=payload)).status_code == 409
        second = await client.post('/api/v1/student-groups', json={**payload, 'academic_year':'2027-2028'})
        assert second.status_code == 201
        conflict = await client.patch('/api/v1/student-groups/'+second.json()['id'], json={'academic_year':'2026-2027'})
        assert conflict.status_code == 409
        assert database.student_groups[1]['academic_year'] == '2027-2028'
        assert (await client.get('/api/v1/student-groups')).json()[0]['academic_year'] == '2026-2027'


@pytest.mark.anyio
@pytest.mark.parametrize('changes', [{'academic_year':'26-27'}, {'student_count':0}, {'student_count':1.5}, {'student_count':'2'}, {'code':'<bad>'}])
async def test_group_validation(app_with_fake_database, changes):
    application, database = app_with_fake_database
    async with AsyncClient(transport=ASGITransport(app=application), base_url='http://test', headers=headers_for(database)) as client:
        response = await client.post('/api/v1/student-groups', json={**dict(code='CS-1', academic_year='2026-2027',student_count=10),**changes})
        assert response.status_code == 422
        assert isinstance(response.json()['detail'],str)
        assert not database.student_groups


@pytest.mark.anyio
@pytest.mark.parametrize('binding', ['load','schedule','none'])
async def test_instructor_deactivation_confirmation_and_activation(app_with_fake_database, binding):
    application, database = app_with_fake_database
    teacher, group, course, room = seed_sprint2(database)
    if binding == 'load':
        database.teaching_loads.append(dict(id=uuid4(), instructor_id=teacher['id'], is_active=True, sessions_per_week=1, session_minutes=60))
    if binding == 'schedule':
        database.schedules.append(dict(id=uuid4(), instructor_id=teacher['id'], time_slot_id=database.time_slots[0]['id']))
    async with AsyncClient(transport=ASGITransport(app=application), base_url='http://test', headers=headers_for(database)) as client:
        path = f"/api/v1/instructors/{teacher['id']}"
        response = await client.patch(path, json={'is_active':False})
        if binding != 'none':
            assert response.status_code == 409
            assert response.json()['detail']['code'] == 'confirmation_required'
            assert teacher['is_active']
            response = await client.patch(path, json={'is_active':False,'confirm_deactivation':True})
        assert response.status_code == 200, response.text
        assert response.json()['is_active'] is False
        response = await client.patch(path+'/status', json={'is_active':True})
        assert response.status_code == 200 and response.json()['is_active']


@pytest.mark.anyio
async def test_availability_save_read_and_ownership(app_with_fake_database):
    application, database = app_with_fake_database
    teacher, *_ = seed_sprint2(database)
    async with AsyncClient(transport=ASGITransport(app=application), base_url='http://test', headers=headers_for(database,'teacher@example.com')) as client:
        response = await client.get('/api/v1/instructors/me/availability')
        assert response.status_code == 200, response.text
        payload = {'entries':[dict(time_slot_id=str(slot['id']), status=state) for slot,state in zip(database.time_slots,['unavailable','preferred','available'])]}
        response = await client.put('/api/v1/instructors/me/availability',json=payload)
        assert response.status_code == 200, response.text
        assert response.json()['entries'] == payload['entries']
        assert (await client.get(f"/api/v1/instructors/{teacher['id']}/availability")).json()['entries'] == payload['entries']
        other = await client.put(f'/api/v1/instructors/{uuid4()}/availability',json=payload)
        assert other.status_code == 403
        duplicate = {'entries': [payload['entries'][0],payload['entries'][0]]}
        assert (await client.put('/api/v1/instructors/me/availability',json=duplicate)).status_code == 422
        assert len(database.instructor_availability) == 3


@pytest.mark.anyio
@pytest.mark.parametrize('case', ['all','hours','unknown','missing','deadline','scheduled','inactive'])
async def test_availability_rejects_invalid_changes(app_with_fake_database, case):
    from datetime import timedelta
    application, database = app_with_fake_database
    teacher, *_ = seed_sprint2(database)
    payload = {'entries':[dict(time_slot_id=str(slot['id']),status='available') for slot in database.time_slots]}
    expected = 422
    if case == 'all':
        for entry in payload['entries']: entry['status'] = 'unavailable'
    elif case == 'hours':
        database.teaching_loads.append(dict(id=uuid4(), instructor_id=teacher['id'], is_active=True, sessions_per_week=3, session_minutes=60))
        payload['entries'][0]['status']='unavailable'
    elif case == 'unknown': payload['entries'][0]['time_slot_id']=str(uuid4())
    elif case == 'missing': payload['entries'].pop()
    elif case == 'deadline':
        teacher['availability_deadline']=datetime.now(timezone.utc)-timedelta(seconds=1); expected=409
    elif case == 'scheduled':
        database.schedules.append(dict(id=uuid4(),instructor_id=teacher['id'],time_slot_id=database.time_slots[0]['id']))
        payload['entries'][0]['status']='unavailable'; expected=409
    elif case == 'inactive': teacher['is_active']=False; expected=403
    async with AsyncClient(transport=ASGITransport(app=application), base_url='http://test', headers=headers_for(database,'teacher@example.com')) as client:
        response=await client.put('/api/v1/instructors/me/availability',json=payload)
        assert response.status_code == expected, response.text
        assert database.instructor_availability == []


@pytest.mark.anyio
async def test_availability_transaction_rollback(app_with_fake_database):
    application, database = app_with_fake_database
    teacher, *_ = seed_sprint2(database)
    original=dict(id=uuid4(),instructor_id=teacher['id'],time_slot_id=database.time_slots[0]['id'],status='preferred')
    database.instructor_availability.append(original)
    database.fail_availability_insert=True
    payload={'entries':[dict(time_slot_id=str(slot['id']),status='available') for slot in database.time_slots]}
    async with AsyncClient(transport=ASGITransport(app=application,raise_app_exceptions=False),base_url='http://test',headers=headers_for(database,'teacher@example.com')) as client:
        assert (await client.put('/api/v1/instructors/me/availability',json=payload)).status_code == 500
    assert database.instructor_availability == [original]


@pytest.mark.anyio
async def test_teaching_load_and_workload_crud(app_with_fake_database):
    application,database=app_with_fake_database
    teacher,group,course,_=seed_sprint2(database)
    payload=dict(instructor_id=str(teacher['id']),group_id=str(group['id']),course_id=str(course['id']),sessions_per_week=2)
    async with AsyncClient(transport=ASGITransport(app=application),base_url='http://test',headers=headers_for(database)) as client:
        response=await client.post('/api/v1/teaching-loads',json=payload)
        assert response.status_code == 201, response.text
        load_id=response.json()['id']
        assert (await client.post('/api/v1/teaching-loads',json=payload)).status_code == 409
        assert len((await client.get('/api/v1/teaching-loads')).json()) == 1
        summary=await client.get(f"/api/v1/instructors/{teacher['id']}/workload")
        assert summary.json()['planned_minutes']==120 and summary.json()['actual_minutes']==0
        path=f"/api/v1/instructors/{teacher['id']}/workload"
        assert (await client.put(path,json={'max_hours':1})).status_code == 409
        assert (await client.put(path,json={'max_hours':2})).status_code == 200
        response=await client.put('/api/v1/teaching-loads/'+load_id,json={**payload,'sessions_per_week':1})
        assert response.status_code==200
        assert (await client.delete('/api/v1/teaching-loads/'+load_id)).status_code==204
        assert database.teaching_loads==[]


@pytest.mark.anyio
@pytest.mark.parametrize('entity',['student_groups','instructors','courses'])
async def test_load_rejects_inactive_entities(app_with_fake_database,entity):
    application,database=app_with_fake_database
    teacher,group,course,_=seed_sprint2(database)
    getattr(database,entity)[0]['is_active']=False
    async with AsyncClient(transport=ASGITransport(app=application),base_url='http://test',headers=headers_for(database)) as client:
        response=await client.post('/api/v1/teaching-loads',json=dict(instructor_id=str(teacher['id']),group_id=str(group['id']),course_id=str(course['id']),sessions_per_week=1))
        assert response.status_code==409
        assert not database.teaching_loads


@pytest.mark.anyio
async def test_slot_overlap_and_sunday(app_with_fake_database):
    application,database=app_with_fake_database
    seed_sprint2(database)
    async with AsyncClient(transport=ASGITransport(app=application),base_url='http://test',headers=headers_for(database)) as client:
        assert (await client.post('/api/v1/time-slots',json=dict(day_of_week=1,start_time='09:30',end_time='10:30'))).status_code==409
        assert (await client.post('/api/v1/time-slots',json=dict(day_of_week=7,start_time='09:00',end_time='10:00'))).status_code==422
        assert (await client.post('/api/v1/time-slots',json=dict(day_of_week=1,start_time='12:00',end_time='13:00'))).status_code==201


@pytest.mark.anyio
async def test_generation_uses_availability_and_stored_loads(app_with_fake_database):
    application,database=app_with_fake_database
    teacher,group,course,_=seed_sprint2(database)
    database.instructor_availability.extend([
        dict(id=uuid4(),instructor_id=teacher['id'],time_slot_id=database.time_slots[0]['id'],status='unavailable'),
        dict(id=uuid4(),instructor_id=teacher['id'],time_slot_id=database.time_slots[2]['id'],status='preferred'),
    ])
    database.teaching_loads.append(dict(id=uuid4(),instructor_id=teacher['id'],group_id=group['id'],course_id=course['id'],sessions_per_week=1,session_minutes=60,is_active=True,delivery_mode='lecture'))
    async with AsyncClient(transport=ASGITransport(app=application),base_url='http://test',headers=headers_for(database)) as client:
        response=await client.post('/api/v1/schedules/generate',json={})
        assert response.status_code==201,response.text
        assert response.json()['entries'][0]['time_slot_id']==str(database.time_slots[2]['id'])
        summary=await client.get(f"/api/v1/instructors/{teacher['id']}/workload")
        assert summary.json()['actual_minutes']==60
        teacher['is_active']=False
        response=await client.post('/api/v1/schedules/generate',json={'replace_existing':True})
        assert response.status_code==422
        assert len(database.schedules)==1  # Failed replacement preserves the existing timetable.


@pytest.mark.anyio
@pytest.mark.parametrize('method,path,payload', [
    ('post','/api/v1/student-groups',dict(code='CS-1',academic_year='2026-2027',student_count=1)),
    ('post','/api/v1/teaching-loads',{}),
    ('patch','/api/v1/instructors/{id}/status',{'is_active':False}),
    ('put','/api/v1/instructors/{id}/workload',{'max_hours':1}),
    ('put','/api/v1/instructors/me/availability',{'entries':[]}),
])
async def test_sprint2_authorization(app_with_fake_database,method,path,payload):
    application,database=app_with_fake_database
    teacher,*_=seed_sprint2(database)
    path=path.replace('{id}',str(teacher['id']))
    async with AsyncClient(transport=ASGITransport(app=application),base_url='http://test') as client:
        assert (await client.request(method,path,json=payload)).status_code==401
        response=await client.request(method,path,json=payload,headers=headers_for(database,'student@example.com'))
        assert response.status_code==403,response.text


def test_scheduler_workload_limit():
    group={'id':'g','student_count':1}
    teacher={'id':'i','max_hours':1}
    room={'id':'r','capacity':1}
    slots=[{'id':str(h),'day_of_week':1,'start_time':time(h),'end_time':time(h+1)} for h in [9,10]]
    with pytest.raises(SchedulingError):
        generate_schedule_matrix([group],[{'course_id':'c','group_id':'g','instructor_id':'i','sessions_per_week':2}],[teacher],[room],slots)


@pytest.mark.anyio
async def test_live_postgres_sprint2(monkeypatch):
    """Optional real SQL test; creates/drops only its own isolated test schema."""
    import os
    import asyncio
    from pathlib import Path
    from auth import create_access_token
    from models import CurrentUser
    dsn = os.getenv('SPRINT2_TEST_DSN')
    if not dsn:
        pytest.skip('Set SPRINT2_TEST_DSN to run real PostgreSQL integration')
    monkeypatch.setenv('JWT_SECRET_KEY','live-test-secret-at-least-thirty-two-characters')
    schema = 'sprint2_test_' + uuid4().hex
    connection = await asyncpg.connect(dsn)
    pool = None
    previous_pool = getattr(app.state, 'pool', None)
    try:
        await connection.execute(f'CREATE SCHEMA {schema}')
        await connection.execute(f'SET search_path TO {schema}, public')
        ddl = Path(__file__).with_name('schema.sql').read_text(encoding='utf-8')
        await connection.execute(ddl.split('-- Sprint 2:')[0])
        legacy_id = await connection.fetchval("INSERT INTO student_groups(name,student_count) VALUES('LEGACY',10) RETURNING id")
        await connection.execute(ddl)
        await connection.execute(ddl)  # Migration is repeatable.
        legacy = await connection.fetchrow('SELECT * FROM student_groups WHERE id=$1', legacy_id)
        assert legacy['is_active'] and re.fullmatch(r'[0-9]{4}-[0-9]{4}', legacy['academic_year'])
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, server_settings={'search_path':f'{schema},public'})
        app.state.pool = pool
        admin_id, teacher_user_id = uuid4(), uuid4()
        for identifier,email,role in [(admin_id,'admin@example.com','admin'), (teacher_user_id,'teacher@example.com','instructor')]:
            await connection.execute('INSERT INTO users(id,email,password_hash,role) VALUES($1,$2,$3,$4)',identifier,email,'not-used',role)
        def token(identifier,email,role):
            return {'Authorization':'Bearer '+create_access_token(CurrentUser(id=identifier,email=email,role=role))}
        admin_headers=token(admin_id,'admin@example.com','admin')
        teacher_headers=token(teacher_user_id,'teacher@example.com','instructor')
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers=admin_headers) as client:
            async def create(path,payload):
                response=await client.post('/api/v1/'+path,json=payload)
                assert response.status_code==201,response.text
                return response.json()
            group_payload=dict(code='CS-101',academic_year='2026-2027',student_count=20)
            concurrent=await asyncio.gather(*[client.post('/api/v1/student-groups',json=group_payload) for _ in range(2)])
            assert sorted(response.status_code for response in concurrent)==[201,409]
            group=next(response.json() for response in concurrent if response.status_code==201)
            await create('student-groups',{**group_payload,'academic_year':'2027-2028'})
            teacher=await create('instructors',dict(user_id=str(teacher_user_id),department='IT'))
            course=await create('courses',dict(code='CS-1',name='Math'))
            room=await create('rooms',dict(name='A1',building='A',room_type='lecture',capacity=30))
            slots=[await create('time-slots',dict(day_of_week=1,start_time=f'{hour:02}:00',end_time=f'{hour+1:02}:00')) for hour in [9,10,11]]
            assert (await client.post('/api/v1/time-slots',json=dict(day_of_week=1,start_time='09:30',end_time='10:30'))).status_code==409
            await create('teaching-loads',dict(instructor_id=teacher['id'],group_id=group['id'],course_id=course['id'],sessions_per_week=1))
            payload={'entries':[{'time_slot_id':slot['id'],'status':state} for slot,state in zip(slots,['unavailable','available','preferred'])]}
            result=await client.put('/api/v1/instructors/me/availability',json=payload,headers=teacher_headers)
            assert result.status_code==200,result.text
            assert await connection.fetchval('SELECT count(*) FROM instructor_availability WHERE NOT is_available')==1
            generated=await client.post('/api/v1/schedules/generate',json={})
            assert generated.status_code==201,generated.text
            assert generated.json()['entries'][0]['time_slot_id']==slots[2]['id']
            path=f"/api/v1/instructors/{teacher['id']}"
            assert (await client.get(path+'/workload')).json()['actual_minutes']==60
            response=await client.patch(path,json={'is_active':False})
            assert response.status_code==409 and response.json()['detail']['confirmation_required']
            assert (await client.patch(path,json={'is_active':False,'confirm_deactivation':True})).status_code==200
            assert (await client.post('/api/v1/schedules/generate',json={'replace_existing':True})).status_code==422
            assert await connection.fetchval('SELECT count(*) FROM schedules')==1
            schedule_id=await connection.fetchval('SELECT id FROM schedules')
            failure=await client.request('DELETE','/api/v1/schedules/batch',json={'schedule_ids':[str(schedule_id),str(uuid4())]})
            assert failure.status_code==404
            assert await connection.fetchval('SELECT count(*) FROM schedules')==1
            assert (await client.delete(f'/api/v1/schedules/{schedule_id}')).status_code==200
            assert (await client.delete('/api/v1/time-slots/'+slots[0]['id'])).status_code==204
            assert await connection.fetchval('SELECT count(*) FROM instructor_availability WHERE time_slot_id=$1', __import__('uuid').UUID(slots[0]['id']))==0
            assert (await client.patch(path,json={'is_active':True})).status_code==200
            manual=dict(course_id=course['id'],group_id=group['id'],instructor_id=teacher['id'],room_id=room['id'],time_slot_id=slots[1]['id'])
            created=await client.post('/api/v1/schedules',json=manual)
            assert created.status_code==201,created.text
            assert (await client.post('/api/v1/schedules',json=manual)).status_code==409
            edited=await client.patch('/api/v1/schedules/'+created.json()['id'],json={'time_slot_id':slots[2]['id']})
            assert edited.status_code==200,edited.text
            concurrent=await asyncio.gather(*[client.post('/api/v1/schedules',json=manual) for _ in range(2)])
            assert sorted(response.status_code for response in concurrent)==[201,409]
            with pytest.raises(asyncpg.CheckViolationError):
                await connection.execute("INSERT INTO rooms(name,building,capacity,room_type) VALUES('D113','D',20,'practical')")
            with pytest.raises(asyncpg.CheckViolationError):
                await connection.execute("INSERT INTO rooms(name,building,capacity,room_type) VALUES('D101','D',26,'practical')")
    finally:
        app.state.pool=previous_pool
        if pool is not None: await pool.close()
        await connection.execute('SET search_path TO public')
        await connection.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
        await connection.close()


@pytest.mark.anyio
@pytest.mark.parametrize('count,expected', [(4,422),(5,201),(50,201),(51,422),(5.5,422),('5',422),(True,422)])
async def test_group_student_count_bounds(app_with_fake_database,count,expected):
    application,database=app_with_fake_database
    async with AsyncClient(transport=ASGITransport(app=application),base_url='http://test',headers=headers_for(database)) as client:
        payload=dict(code='BOUNDARY',academic_year='2026-2027',student_count=count)
        response=await client.post('/api/v1/student-groups',json=payload)
        assert response.status_code==expected,response.text
        existing=await client.post('/api/v1/student-groups',json={**payload,'code':'EDIT','student_count':20})
        assert existing.status_code==201
        updated=await client.patch('/api/v1/student-groups/'+existing.json()['id'],json={'student_count':count})
        assert updated.status_code==(200 if expected==201 else 422),updated.text
        if expected==422:
            assert '5 to 50' in response.json()['detail']
            assert not re.search('[А-Яа-яЁё]',response.json()['detail'])


@pytest.mark.anyio
async def test_uploaded_minute_workload_api(app_with_fake_database):
    application,database=app_with_fake_database
    teacher,group,course,_=seed_sprint2(database)
    teacher['max_weekly_minutes']=90
    path=f"/api/v1/instructors/{teacher['id']}"
    async with AsyncClient(transport=ASGITransport(app=application),base_url='http://test',headers=headers_for(database)) as client:
        payload=dict(instructor_id=str(teacher['id']),group_id=str(group['id']),course_id=str(course['id']),sessions_per_week=2)
        assert (await client.post('/api/v1/teaching-loads',json=payload)).status_code==409
        response=await client.put(path+'/workload',json={'max_weekly_minutes':120})
        assert response.status_code==200,response.text
        assert response.json()['max_weekly_minutes']==120
        assert (await client.post('/api/v1/teaching-loads',json=payload)).status_code==201
        assert (await client.patch(path,json={'max_weekly_minutes':90})).status_code==409
        assert (await client.put(path+'/workload',json={'max_weekly_minutes':90})).status_code==409
        assert (await client.put(path+'/workload',json={'max_hours':1,'max_weekly_minutes':120})).status_code==422
        assert (await client.get('/api/v1/teaching-loads',params={'instructor_id':str(uuid4())})).json()==[]
        response=await client.put(path+'/workload',json={'max_hours':3})
        assert response.json()['max_weekly_minutes']==180
        assert response.json()['remaining_minutes']==60


def test_scheduler_preserves_minute_limit():
    group={'id':'g','student_count':5}
    teacher={'id':'i','max_hours':40,'max_weekly_minutes':90}
    room={'id':'r','capacity':50}
    slots=[{'id':str(h),'day_of_week':1,'start_time':time(h),'end_time':time(h+1)} for h in [9,10]]
    with pytest.raises(SchedulingError):
        generate_schedule_matrix([group],[{'course_id':'c','group_id':'g','instructor_id':'i','sessions_per_week':2}],[teacher],[room],slots)


@pytest.mark.anyio
async def test_migrate_uploaded_schema():
    import os
    from pathlib import Path
    dsn=os.getenv('SPRINT2_TEST_DSN')
    if not dsn: pytest.skip('Set SPRINT2_TEST_DSN for migration tests')
    connection=await asyncpg.connect(dsn)
    schema='uploaded_test_'+uuid4().hex
    try:
        await connection.execute(f'CREATE SCHEMA {schema}')
        await connection.execute(f'SET search_path TO {schema}, public')
        root=Path(__file__).parent
        await connection.execute((root/'tests/fixtures/uploaded_schema.sql').read_text(encoding='utf-8'))
        user=await connection.fetchval("INSERT INTO users(email,password_hash,role) VALUES('teacher@example.com','unused','instructor') RETURNING id")
        teacher=await connection.fetchval("INSERT INTO instructors(user_id,department,max_weekly_minutes) VALUES($1,'IT',275) RETURNING id",user)
        group=await connection.fetchval("INSERT INTO student_groups(name,academic_year,student_count) VALUES('CS-1','2026-2027',20) RETURNING id")
        course=await connection.fetchval("INSERT INTO courses(code,name) VALUES('CS-1','Math') RETURNING id")
        slot=await connection.fetchval("INSERT INTO time_slots(day_of_week,start_time,end_time) VALUES(1,'09:00','10:30') RETURNING id")
        load=await connection.fetchval("INSERT INTO teaching_loads(instructor_id,group_id,course_id,sessions_per_week,planned_minutes) VALUES($1,$2,$3,2,180) RETURNING id",teacher,group,course)
        await connection.execute("INSERT INTO instructor_availability(instructor_id,time_slot_id,status,is_available) VALUES($1,$2,'unavailable',false)",teacher,slot)
        ddl=(root/'schema.sql').read_text(encoding='utf-8')
        await connection.execute(ddl)
        await connection.execute(ddl)
        row=await connection.fetchrow('SELECT * FROM teaching_loads WHERE id=$1',load)
        assert row['planned_minutes']==180 and row['session_minutes']==90 and row['is_active']
        assert await connection.fetchval('SELECT max_weekly_minutes FROM instructors WHERE id=$1',teacher)==275
        await connection.execute("UPDATE instructor_availability SET status='preferred'")
        assert await connection.fetchval('SELECT is_available FROM instructor_availability') is True
        await connection.execute('UPDATE teaching_loads SET sessions_per_week=3 WHERE id=$1',load)
        assert await connection.fetchval('SELECT planned_minutes FROM teaching_loads WHERE id=$1',load)==270
        for count in (4,51):
            with pytest.raises(asyncpg.CheckViolationError):
                await connection.execute('UPDATE student_groups SET student_count=$1 WHERE id=$2',count,group)
        for count in (5,50):
            await connection.execute('UPDATE student_groups SET student_count=$1 WHERE id=$2',count,group)
    finally:
        if connection.is_in_transaction(): await connection.execute('ROLLBACK')
        await connection.execute('SET search_path TO public')
        await connection.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
        await connection.close()

@pytest.mark.anyio
async def test_admin_accounts_cannot_be_deleted(app_with_fake_database):
    application,database=app_with_fake_database
    database.add_user('anotheradmin@example.com','AdminPassword123','admin')
    async with AsyncClient(transport=ASGITransport(app=application),base_url='http://test',headers=headers_for(database)) as client:
        for email in ['admin@example.com','anotheradmin@example.com']:
            identifier=database.users[email]['id']
            response=await client.delete(f'/api/v1/users/{identifier}')
            assert response.status_code==403,response.text
            assert response.json()['detail']=='Administrator accounts cannot be deleted'
            assert email in database.users
        student=database.users['student@example.com']['id']
        assert (await client.delete(f'/api/v1/users/{student}')).status_code==204
