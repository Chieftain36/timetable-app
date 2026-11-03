"""Synthetic authorization checks: no database or deployed credentials required."""
import re
from datetime import datetime, time, timezone
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from auth import create_access_token
from main import app, get_connection
from models import CurrentUser
from test_app import app_with_fake_database


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def security_db(monkeypatch):
    monkeypatch.setenv('JWT_SECRET_KEY', 'security-test-only-key-at-least-32-characters')

    class Connection:
        def __init__(self):
            self.active = True
            self.profile_exists = True
            self.schedule_reads = 0
            self.last_query = ''
            self.last_values = ()
            self.owner = UUID(int=2)
            self.other = UUID(int=3)
            self.rows = [self.schedule(self.owner), self.schedule(self.other)]

        def schedule(self, instructor_id):
            return dict(id=instructor_id, course_id=UUID(int=4), room_id=UUID(int=5),
                        instructor_id=instructor_id, group_id=UUID(int=6), time_slot_id=UUID(int=7),
                        created_at=datetime.now(timezone.utc), course_code='TEST', course_name='Synthetic',
                        room_name='D101', room_building='D', group_name='TEST-1', student_count=20,
                        instructor_email='synthetic@example.com', day_of_week=1,
                        start_time=time(9), end_time=time(10))

        async def fetchrow(self, query, *values):
            if 'FROM timetable_versions' in query:
                from fastapi.encoders import jsonable_encoder
                return dict(entries=jsonable_encoder(self.rows), valid_from=None, valid_until=None)
            assert query == 'SELECT id, email, role FROM users WHERE id = $1'
            roles = {UUID(int=100): 'admin', UUID(int=1): 'instructor', UUID(int=200): 'student'}
            return dict(id=values[0], email='audit@example.com', role=roles[values[0]])

        async def fetchval(self, query, *values):
            if 'to_regclass' in query:
                return 'planning_settings'
            if 'FROM student_group_links' in query:
                return None
            if 'FROM instructors' in query:
                return self.owner if self.active and self.profile_exists else None
            raise AssertionError(query)

        async def fetch(self, query, *values):
            if 'FROM instructors WHERE user_id = $1' in query:
                return [dict(id=self.owner, is_active=self.active)] if self.profile_exists else []
            assert 'FROM schedules AS s' in query, query
            self.schedule_reads += 1
            self.last_query, self.last_values = query, values
            result = self.rows
            for column, position in re.findall(r's\.(group_id|room_id|instructor_id) = \$(\d+)', query):
                result = [row for row in result if row[column] == values[int(position) - 1]]
            return result

    connection = Connection()
    async def override():
        yield connection
    app.dependency_overrides[get_connection] = override
    yield connection
    app.dependency_overrides.clear()


def authorization(role):
    identifier = {'admin': 100, 'instructor': 1, 'student': 200}[role]
    user = CurrentUser(id=UUID(int=identifier), email='audit@example.com', role=role)
    return {'Authorization': 'Bearer ' + create_access_token(user)}


@pytest.mark.anyio
@pytest.mark.parametrize('query', ['', '?group_id=' + str(UUID(int=6)),
                                   '?instructor_id=' + str(UUID(int=2))])
async def test_students_fail_closed_without_membership(security_db, query):
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/schedules' + query, headers=authorization('student'))
    assert response.status_code == 200
    assert response.json() == []
    assert security_db.schedule_reads == 0


@pytest.mark.anyio
@pytest.mark.parametrize('query', ['', '?instructor_id=' + str(UUID(int=2)),
                                   '?group_id=' + str(UUID(int=6)) + '&room_id=' + str(UUID(int=5))])
async def test_instructor_filters_cannot_expand_ownership(security_db, query):
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/schedules' + query, headers=authorization('instructor'))
    assert response.status_code == 200
    assert [row['instructor_id'] for row in response.json()] == [str(security_db.owner)]


@pytest.mark.anyio
async def test_instructor_cannot_request_other_instructor(security_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/schedules', params={'instructor_id': str(security_db.other)},
                                    headers=authorization('instructor'))
    assert response.status_code == 403
    assert security_db.schedule_reads == 0


@pytest.mark.anyio
@pytest.mark.parametrize('missing', [False, True])
async def test_inactive_or_missing_profile_cannot_read(security_db, missing):
    security_db.active = False
    security_db.profile_exists = not missing
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/schedules', headers=authorization('instructor'))
    assert response.status_code == 403
    assert security_db.schedule_reads == 0


@pytest.mark.anyio
async def test_admin_global_and_optional_filter_access_preserved(security_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        all_rows = await client.get('/api/v1/schedules', headers=authorization('admin'))
        own = await client.get('/api/v1/schedules', params={'instructor_id': str(security_db.other)},
                               headers=authorization('admin'))
    assert all_rows.status_code == own.status_code == 200
    assert len(all_rows.json()) == 2
    assert [row['instructor_id'] for row in own.json()] == [str(security_db.other)]


@pytest.mark.anyio
@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer invalid.token.value'}])
async def test_missing_or_invalid_token_is_rejected(security_db, headers):
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/v1/schedules', headers=headers)
    assert response.status_code == 401
    assert security_db.schedule_reads == 0


@pytest.mark.anyio
async def test_replacement_failure_rolls_back_all_fake_database_writes(app_with_fake_database):
    from copy import deepcopy
    from test_app import FakeConnection, headers_for, seed_sprint2
    application, database = app_with_fake_database
    teacher, group, course, room = seed_sprint2(database)
    original = dict(id=UUID(int=100), course_id=course['id'], group_id=group['id'],
                    instructor_id=teacher['id'], room_id=room['id'],
                    time_slot_id=database.time_slots[0]['id'], created_at=datetime.now(timezone.utc))
    database.schedules.append(original)
    before = deepcopy(database.schedules)

    class FailingConnection(FakeConnection):
        inserts = 0
        async def execute(self, query, *values):
            if query.lstrip().startswith('INSERT INTO schedules'):
                self.inserts += 1
                if self.inserts == 2:
                    raise RuntimeError('Synthetic insert failure after deletion and first insert')
            return await super().execute(query, *values)

    connection = FailingConnection(database)
    async def override():
        yield connection
    application.dependency_overrides[get_connection] = override
    payload = dict(replace_existing=True, assignments=[dict(course_id=str(course['id']),
                   group_id=str(group['id']), instructor_id=str(teacher['id']), sessions_per_week=2)])
    async with AsyncClient(transport=ASGITransport(app=application, raise_app_exceptions=False),
                           base_url='http://test', headers=headers_for(database)) as client:
        response = await client.post('/api/v1/schedules/generate', json=payload)
    assert response.status_code == 500
    assert connection.inserts == 2
    assert database.schedules == before


@pytest.mark.anyio
async def test_live_postgres_schedule_ownership(monkeypatch):
    """Execute the actual joined SQL in a private schema of an explicitly supplied test DB."""
    import os
    from pathlib import Path
    from uuid import uuid4
    import asyncpg

    dsn = os.getenv('SPRINT2_TEST_DSN')
    if not dsn:
        pytest.skip('Set SPRINT2_TEST_DSN to a disposable PostgreSQL test database')
    monkeypatch.setenv('JWT_SECRET_KEY', 'security-test-only-key-at-least-32-characters')
    connection = await asyncpg.connect(dsn)
    schema = 'security_test_' + uuid4().hex
    previous = app.dependency_overrides.copy()
    try:
        await connection.execute(f'CREATE SCHEMA {schema}')
        await connection.execute(f'SET search_path TO {schema}, public')
        await connection.execute(Path('schema.sql').read_text())
        for user_id, email, role in [(UUID(int=1), 'owner@example.com', 'instructor'),
                                    (UUID(int=10), 'other@example.com', 'instructor'),
                                    (UUID(int=100), 'admin@example.com', 'admin'),
                                    (UUID(int=200), 'student@example.com', 'student')]:
            await connection.execute("INSERT INTO users (id,email,password_hash,role) VALUES ($1,$2,'unused-test-hash',$3)", user_id, email, role)
        for identifier, user_id in [(UUID(int=2), UUID(int=1)), (UUID(int=3), UUID(int=10))]:
            await connection.execute("INSERT INTO instructors (id,user_id,department) VALUES ($1,$2,'Test')", identifier, user_id)
        course = await connection.fetchval("INSERT INTO courses(code,name) VALUES ('TEST','Synthetic course') RETURNING id")
        room = await connection.fetchval("INSERT INTO rooms(name,building,capacity,room_type) VALUES ('D101','D',25,'practical') RETURNING id")
        group = await connection.fetchval("INSERT INTO student_groups(name,student_count,academic_year) VALUES ('TEST-1',20,'2026-2027') RETURNING id")
        for hour, instructor in [(9, UUID(int=2)), (10, UUID(int=3))]:
            slot = await connection.fetchval('INSERT INTO time_slots(day_of_week,start_time,end_time) VALUES (1,$1,$2) RETURNING id', time(hour), time(hour + 1))
            await connection.execute('INSERT INTO schedules(course_id,room_id,instructor_id,group_id,time_slot_id) VALUES ($1,$2,$3,$4,$5)', course, room, instructor, group, slot)
        async def override():
            yield connection
        app.dependency_overrides[get_connection] = override
        def live_authorization(role):
            identifiers = {'admin': 100, 'instructor': 1, 'student': 200}
            emails = {'admin': 'admin@example.com', 'instructor': 'owner@example.com', 'student': 'student@example.com'}
            user = CurrentUser(id=UUID(int=identifiers[role]), email=emails[role], role=role)
            return {'Authorization': 'Bearer ' + create_access_token(user)}
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            published = await client.post('/api/v1/planning/publish', json={'confirm': True}, headers=live_authorization('admin'))
            assert published.status_code == 200, published.text
            own = await client.get('/api/v1/schedules', headers=live_authorization('instructor'))
            assert own.status_code == 200, own.text
            assert [row['instructor_id'] for row in own.json()] == [str(UUID(int=2))]
            cross = await client.get('/api/v1/schedules', params={'instructor_id': str(UUID(int=3))}, headers=live_authorization('instructor'))
            assert cross.status_code == 403
            student = await client.get('/api/v1/schedules', params={'group_id': str(group)}, headers=live_authorization('student'))
            assert student.status_code == 200
            assert student.json() == []
            all_rows = await client.get('/api/v1/schedules', headers=live_authorization('admin'))
            assert all_rows.status_code == 200
            assert len(all_rows.json()) == 2
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await connection.execute('SET search_path TO public')
        await connection.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
        await connection.close()
