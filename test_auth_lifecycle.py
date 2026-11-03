"""Account authorization uses current persisted identity, not only JWT claims."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from auth import JWT_ALGORITHM
from test_app import app_with_fake_database, anyio_backend, headers_for


@pytest.mark.anyio
@pytest.mark.parametrize('role', ['student', 'instructor'])
async def test_last_admin_cannot_be_demoted(app_with_fake_database, role):
    application, database = app_with_fake_database
    admin = database.users['admin@example.com']
    before = deepcopy(admin)
    async with AsyncClient(transport=ASGITransport(app=application), base_url='http://test',
                           headers=headers_for(database)) as client:
        response = await client.patch('/api/v1/users/' + str(admin['id']), json={'role': role})
    assert response.status_code == 409
    assert 'last administrator' in response.json()['detail']
    assert admin == before


@pytest.mark.anyio
async def test_demotion_with_second_admin_succeeds_and_old_token_stops_working(app_with_fake_database):
    application, database = app_with_fake_database
    database.add_user('second@example.com', 'SyntheticPassword1', 'admin')
    admin = database.users['admin@example.com']
    original_headers = headers_for(database)
    second_headers = headers_for(database, 'second@example.com')
    async with AsyncClient(transport=ASGITransport(app=application), base_url='http://test') as client:
        changed = await client.patch('/api/v1/users/' + str(admin['id']), json={'role': 'student'}, headers=original_headers)
        assert changed.status_code == 200
        assert changed.json()['role'] == 'student'
        stale = await client.get('/api/v1/users', headers=original_headers)
        assert stale.status_code == 401
        assert stale.headers['www-authenticate'] == 'Bearer'
        forbidden_write = await client.post('/api/v1/courses', headers=original_headers,
                                            json={'code': 'TEST', 'name': 'Synthetic'})
        assert forbidden_write.status_code == 401
        assert database.courses == []
        assert (await client.get('/api/v1/users', headers=second_headers)).status_code == 200
        # A newly issued token reflects the new role, but cannot use admin routes.
        assert (await client.get('/api/v1/users', headers=headers_for(database))).status_code == 403


@pytest.mark.anyio
async def test_deleted_account_token_is_rejected(app_with_fake_database):
    application, database = app_with_fake_database
    student = database.users['student@example.com']
    headers = headers_for(database, 'student@example.com')
    # Model a committed account deletion without involving unrelated fake FK behavior.
    del database.users['student@example.com']
    async with AsyncClient(transport=ASGITransport(app=application), base_url='http://test') as client:
        response = await client.get('/api/v1/schedules', headers=headers)
    assert response.status_code == 401
    assert student['role'] == 'student'


@pytest.mark.anyio
async def test_changed_email_invalidates_old_identity(app_with_fake_database):
    application, database = app_with_fake_database
    old_headers = headers_for(database)
    admin = database.users['admin@example.com']
    async with AsyncClient(transport=ASGITransport(app=application), base_url='http://test') as client:
        updated = await client.patch('/api/v1/users/' + str(admin['id']), headers=old_headers,
                                     json={'email': 'changed@example.com'})
        assert updated.status_code == 200
        assert (await client.get('/api/v1/users', headers=old_headers)).status_code == 401
        assert (await client.get('/api/v1/users', headers=headers_for(database))).status_code == 200


@pytest.mark.anyio
async def test_promoted_account_requires_new_token(app_with_fake_database):
    application, database = app_with_fake_database
    student = database.users['student@example.com']
    old_headers = headers_for(database, 'student@example.com')
    async with AsyncClient(transport=ASGITransport(app=application), base_url='http://test') as client:
        updated = await client.patch('/api/v1/users/' + str(student['id']), headers=headers_for(database),
                                     json={'role': 'admin'})
        assert updated.status_code == 200
        assert (await client.get('/api/v1/users', headers=old_headers)).status_code == 401
        assert (await client.get('/api/v1/users', headers=headers_for(database, 'student@example.com'))).status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize('missing_claim', ['exp', 'iat'])
async def test_required_token_time_claims(app_with_fake_database, missing_claim):
    application, database = app_with_fake_database
    admin = database.users['admin@example.com']
    now = datetime.now(timezone.utc)
    claims = dict(sub=str(admin['id']), email=admin['email'], role='admin', iat=now,
                  exp=now + timedelta(minutes=5))
    del claims[missing_claim]
    token = jwt.encode(claims, 'test-secret-key-with-at-least-32-characters', algorithm=JWT_ALGORITHM)
    async with AsyncClient(transport=ASGITransport(app=application), base_url='http://test') as client:
        response = await client.get('/api/v1/users', headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 401


@pytest.mark.anyio
async def test_rejected_demotion_does_not_invalidate_existing_admin_token(app_with_fake_database):
    application, database = app_with_fake_database
    headers = headers_for(database)
    async with AsyncClient(transport=ASGITransport(app=application), base_url='http://test') as client:
        response = await client.patch('/api/v1/users/' + str(database.users['admin@example.com']['id']),
                                      headers=headers, json={'role': 'student'})
        assert response.status_code == 409
        assert (await client.get('/api/v1/users', headers=headers)).status_code == 200


@pytest.mark.anyio
async def test_invalid_credentials_are_rejected_before_database_access(monkeypatch):
    from main import app
    monkeypatch.setenv('JWT_SECRET_KEY', 'test-offline-auth-key-at-least-32-characters')
    monkeypatch.setattr(app.state, 'pool', None, raising=False)
    previous = app.dependency_overrides.copy()
    app.dependency_overrides.clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/api/v1/users')).status_code == 401
            assert (await client.get('/api/v1/users', headers={'Authorization': 'Bearer invalid'})).status_code == 401
    finally:
        app.dependency_overrides.update(previous)


@pytest.mark.anyio
async def test_live_concurrent_admin_demotions_leave_one_admin(monkeypatch):
    import asyncio
    import os
    from pathlib import Path
    from uuid import uuid4
    import asyncpg
    from auth import create_access_token
    from main import app
    from models import CurrentUser

    dsn = os.getenv('SPRINT2_TEST_DSN')
    if not dsn:
        pytest.skip('Set SPRINT2_TEST_DSN to a disposable PostgreSQL test database')
    monkeypatch.setenv('JWT_SECRET_KEY', 'test-lifecycle-live-key-at-least-32-characters')
    connection = await asyncpg.connect(dsn)
    schema = 'lifecycle_test_' + uuid4().hex
    pool = None
    previous_pool = getattr(app.state, 'pool', None)
    previous_overrides = app.dependency_overrides.copy()
    try:
        await connection.execute(f'CREATE SCHEMA {schema}')
        await connection.execute(f'SET search_path TO {schema}, public')
        await connection.execute(Path(__file__).with_name('schema.sql').read_text())
        identifiers = [uuid4(), uuid4()]
        headers = []
        for number, identifier in enumerate(identifiers):
            email = f'admin{number}@example.com'
            await connection.execute("INSERT INTO users(id,email,password_hash,role) VALUES ($1,$2,'synthetic-unused-hash','admin')", identifier, email)
            token = create_access_token(CurrentUser(id=identifier, email=email, role='admin'))
            headers.append({'Authorization': 'Bearer ' + token})
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4,
                                        server_settings={'search_path': f'{schema},public'})
        app.state.pool = pool
        app.dependency_overrides.clear()
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            responses = await asyncio.gather(*[
                client.patch('/api/v1/users/' + str(identifier), json={'role': 'student'}, headers=auth)
                for identifier, auth in zip(identifiers, headers)
            ])
            assert sorted(response.status_code for response in responses) == [200, 409], [response.text for response in responses]
            assert await connection.fetchval("SELECT count(*) FROM users WHERE role='admin'") == 1
            for response, auth in zip(responses, headers):
                expected = 401 if response.status_code == 200 else 200
                result = await client.get('/api/v1/users', headers=auth)
                assert result.status_code == expected
    finally:
        app.state.pool = previous_pool
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        if pool:
            await pool.close()
        await connection.execute('SET search_path TO public')
        await connection.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
        await connection.close()
