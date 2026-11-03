"""US16–25 acceptance checks against real, disposable PostgreSQL only."""
import json
import os
from datetime import date, timedelta, time
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from auth import create_access_token
from main import app
from models import CurrentUser
from planning import Preferences
from scheduler import generate_schedule_matrix, schedule_quality


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
async def live_planning(monkeypatch):
    dsn = os.getenv('SPRINT2_TEST_DSN')
    if not dsn:
        pytest.skip('Set SPRINT2_TEST_DSN to a disposable PostgreSQL database')
    monkeypatch.setenv('JWT_SECRET_KEY', 'planning-tests-only-secret-with-at-least-32-characters')
    c = await asyncpg.connect(dsn)
    schema = 'planning_test_' + uuid4().hex
    pool = None
    previous_pool = getattr(app.state, 'pool', None)
    previous_overrides = app.dependency_overrides.copy()
    try:
        await c.execute(f'CREATE SCHEMA {schema}')
        await c.execute(f'SET search_path TO {schema},public')
        await c.execute(Path('schema.sql').read_text())
        # Upgrade SQL must also be idempotent after a fresh schema.
        await c.execute(Path('migrations/004_planning.sql').read_text())
        await c.execute(Path('migrations/004_planning.sql').read_text())
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, server_settings={'search_path': f'{schema},public'})
        app.state.pool = pool
        app.dependency_overrides.clear()
        ids, headers = {}, {}
        for name, role in [('admin', 'admin'), ('teacher', 'instructor'), ('other', 'instructor'), ('student', 'student'), ('unlinked', 'student')]:
            ids[name] = uuid4()
            email = name + '@example.com'
            await c.execute("INSERT INTO users(id,email,password_hash,role) VALUES($1,$2,'synthetic-unused-hash',$3)", ids[name], email, role)
            token = create_access_token(CurrentUser(id=ids[name], email=email, role=role))
            headers[name] = {'Authorization': 'Bearer ' + token}
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test', headers=headers['admin']) as client:
            async def api(path, method='GET', body=None, role='admin', expected=200):
                response = await client.request(method, '/api/v1' + path, json=body, headers=headers[role])
                assert response.status_code == expected, (path, response.status_code, response.text)
                return response.json() if response.content else None
            courses, groups, teachers, rooms, slots = [], [], [], [], []
            for n in range(2):
                courses.append(await api('/courses', 'POST', {'code': f'TEST{n}', 'name': f'Course {n}'}, expected=201))
                groups.append(await api('/student-groups', 'POST', {'code': f'GR-{n}', 'academic_year': '2026-2027', 'student_count': 20}, expected=201))
                teachers.append(await api('/instructors', 'POST', {'user_id': str(ids[['teacher', 'other'][n]]), 'department': 'Test'}, expected=201))
            for name, kind, features, cap in [('D101', 'practical', ['computer-lab'], 25), ('D102', 'practical', [], 10), ('A1', 'lecture', [], 70)]:
                rooms.append(await api('/rooms', 'POST', {'name': name, 'building': name[0], 'capacity': cap, 'room_type': kind, 'features': features}, expected=201))
            for day in [1, 2]:
                for hour in [8, 9, 10, 11]:
                    slots.append(await api('/time-slots', 'POST', {'day_of_week': day, 'start_time': f'{hour:02d}:00', 'end_time': f'{hour+1:02d}:00'}, expected=201))
            for n in range(2):
                await api('/teaching-loads', 'POST', {'course_id': courses[n]['id'], 'group_id': groups[n]['id'], 'instructor_id': teachers[n]['id'], 'sessions_per_week': 2, 'session_minutes': 60, 'delivery_mode': 'lab' if n == 0 else 'lecture'}, expected=201)
            await api('/planning/course-rules/' + courses[0]['id'], 'PUT', {'room_type': 'practical', 'required_features': ['computer-lab']})
            await api('/planning/student-links/' + str(ids['student']), 'PUT', {'group_id': groups[0]['id']})
            yield dict(c=c, schema=schema, api=api, client=client, headers=headers, ids=ids, courses=courses, groups=groups, teachers=teachers, rooms=rooms, slots=slots)
    finally:
        app.state.pool = previous_pool
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        if pool:
            await pool.close()
        await c.execute('SET search_path TO public')
        await c.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
        await c.close()


@pytest.mark.anyio
async def test_generation_preferences_capacity_features_and_score(live_planning):
    d = live_planning; api = d['api']
    prefs = Preferences(avoid_early=True, balance_days=False).model_dump(mode='json')
    await api('/planning/settings', 'PUT', prefs)
    assert (await api('/planning/settings')) == prefs
    await api('/instructors/me/availability', 'PUT', {'entries': [dict(time_slot_id=s['id'], status='unavailable' if s['start_time'] == '08:00:00' else 'available') for s in d['slots']]}, role='teacher')
    result = await api('/planning/generate', 'POST', {'replace_existing': True}, expected=201)
    assert result['generated_count'] == 4
    assert 0 <= result['quality']['score'] <= 100
    assert len(result['log']) == 3
    draft = await api('/planning/timetable')
    assert len(draft) == 4
    assert all(row['room_id'] != d['rooms'][1]['id'] for row in draft)
    assert all(row['start_time'] >= '09:00:00' for row in draft)
    assert all(row['room_id'] == d['rooms'][0]['id'] for row in draft if row['course_id'] == d['courses'][0]['id'])
    assert all(row['room_id'] == d['rooms'][2]['id'] for row in draft if row['course_id'] == d['courses'][1]['id'])
    assert (await api('/planning/status'))['versions'][0]['kind'] == 'draft'
    assert await api('/planning/timetable', role='student') == []
    assert await api('/schedules', role='student') == []


@pytest.mark.anyio
async def test_publication_personal_isolation_filters_and_weeks(live_planning):
    d = live_planning; api = d['api']
    await api('/planning/generate', 'POST', {'replace_existing': True}, expected=201)
    monday = date.today() - timedelta(days=date.today().weekday())
    await api('/planning/term', 'PUT', {'start': str(monday), 'end': str(monday+timedelta(days=6))})
    await api('/planning/publish', 'POST', {'confirm': True}, role='student', expected=403)
    await api('/planning/publish', 'POST', {'confirm': True})
    own = await api('/planning/timetable', role='student')
    assert len(own) == 2
    assert all(row['group_id'] == d['groups'][0]['id'] for row in own)
    assert await api('/planning/timetable?source=draft&group_id='+d['groups'][1]['id'], role='student') == []
    assert await api('/planning/timetable', role='unlinked') == []
    assert all(row['instructor_id'] == d['teachers'][0]['id'] for row in await api('/planning/timetable', role='teacher'))
    assert await api('/schedules', role='student') == own
    filters = f"?group_id={d['groups'][0]['id']}&course_id={d['courses'][0]['id']}&q=Course"
    assert len(await api('/planning/timetable'+filters)) == 2
    assert await api('/planning/timetable'+filters+'&room_id='+d['rooms'][2]['id']) == []
    assert len(await api('/planning/timetable?week_start='+str(monday), role='student')) == 2
    assert await api('/planning/timetable?week_start='+str(monday+timedelta(days=7)), role='student') == []
    assert await api('/planning/timetable?week_start='+str(monday-timedelta(days=7)), role='student') == []
    # Rebuilding draft never changes the existing publication.
    await api('/planning/generate', 'POST', {'replace_existing': True}, expected=201)
    assert await api('/planning/timetable', role='student') == own
    await api('/planning/student-links/'+str(d['ids']['student']), 'DELETE', expected=204)
    assert await api('/planning/timetable', role='student') == []


@pytest.mark.anyio
async def test_locked_regeneration_and_infeasible_rollback(live_planning):
    d = live_planning; api = d['api']
    await api('/planning/generate', 'POST', {'replace_existing': True}, expected=201)
    before = await api('/planning/timetable')
    locked = next(row for row in before if row['course_id'] == d['courses'][0]['id'])
    await api('/planning/locks/'+locked['id'], 'PUT', {'locked': True})
    await api('/schedules/'+locked['id'], 'PATCH', {'room_id': locked['room_id']}, expected=409)
    await api('/schedules/'+locked['id'], 'DELETE', expected=409)
    await api('/schedules/batch', 'DELETE', {'schedule_ids': [row['id'] for row in before]}, expected=409)
    result = await api('/planning/generate', 'POST', {'replace_existing': True}, expected=201)
    assert result['retained_count'] == 1 and result['generated_count'] == 3
    after = await api('/planning/timetable')
    assert any(row['id'] == locked['id'] and row['is_locked'] for row in after)
    before = after
    await api('/planning/course-rules/'+d['courses'][1]['id'], 'PUT', {'room_type': 'practical', 'required_features': ['chemical-lab']})
    failed = await api('/planning/generate', 'POST', {'replace_existing': True}, expected=422)
    assert failed['detail']['unscheduled']
    assert await api('/planning/timetable') == before
    await api('/planning/publish', 'POST', {'confirm': True}, expected=409)
    assert (await api('/planning/status'))['published_version'] is None


@pytest.mark.anyio
async def test_publication_validation_and_immutability(live_planning):
    d = live_planning; api = d['api']
    await api('/planning/publish', 'POST', {'confirm': True}, expected=409)
    await api('/planning/generate', 'POST', {'replace_existing': True}, expected=201)
    await api('/planning/publish', 'POST', {'confirm': True})
    official = await api('/planning/timetable', role='student')
    # A changed hard rule invalidates the draft, not the published snapshot.
    await api('/planning/course-rules/'+d['courses'][0]['id'], 'PUT', {'room_type': 'lecture'})
    denied = await api('/planning/publish', 'POST', {'confirm': True}, expected=409)
    assert denied['detail']['conflicts']
    assert await api('/planning/timetable', role='student') == official
    await api('/planning/settings', 'PUT', {}, role='student', expected=403)
    await api('/planning/student-links/'+str(d['ids']['student']), 'PUT', {'group_id': d['groups'][1]['id']}, role='student', expected=403)
    await api('/planning/generate', 'POST', {}, role='teacher', expected=403)
    await api('/planning/term', 'PUT', {'start': '2026-12-01', 'end': '2026-01-01'}, expected=422)


def test_soft_preferences_never_block_the_only_hard_valid_slot():
    entries = generate_schedule_matrix([dict(id='g', student_count=10)],
        [dict(course_id='c', group_id='g', instructor_id='i', required_room_type='practical', required_features=['chemical-lab'])],
        [dict(id='i')], [dict(id='r', capacity=25, room_type='practical', features=['chemical-lab'])],
        [dict(id='s', day_of_week=1, start_time=time(8), end_time=time(9))], generation_preferences=Preferences(avoid_early=True).model_dump(mode='json'))
    assert entries[0]['time_slot_id'] == 's'


def test_quality_penalizes_real_gaps_early_classes_and_daily_overload():
    prefs = Preferences(avoid_early=True, balance_days=False, max_classes_per_day=1).model_dump(mode='json')
    rows = [dict(group_id='g', instructor_id='i', day_of_week=1, start_time='08:00', end_time='09:00'),
            dict(group_id='g', instructor_id='i', day_of_week=1, start_time='12:00', end_time='13:00')]
    score = schedule_quality(rows, prefs)
    assert score['details']['early_hours'] == 1
    assert score['details']['gap_hours'] == 6  # instructor + group
    assert score['details']['daily_excess_classes'] == 1
    assert score['score'] < 100


@pytest.mark.anyio
async def test_generation_failure_preserves_entire_draft_and_version_history(live_planning):
    d = live_planning; api = d['api']
    await api('/planning/generate', 'POST', {'replace_existing': True}, expected=201)
    before = await api('/planning/timetable')
    versions = (await api('/planning/status'))['versions']
    await d['c'].execute('CREATE SEQUENCE fail_insert_counter')
    await d['c'].execute("""CREATE FUNCTION fail_second_insert() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN IF nextval('fail_insert_counter')=2 THEN RAISE EXCEPTION 'Synthetic second-insert failure'; END IF; RETURN NEW; END $$""")
    await d['c'].execute('CREATE TRIGGER inject_insert_failure BEFORE INSERT ON schedules FOR EACH ROW EXECUTE FUNCTION fail_second_insert()')
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test') as client:
        response = await client.post('/api/v1/planning/generate', json={'replace_existing': True}, headers=d['headers']['admin'])
    assert response.status_code == 500
    assert await api('/planning/timetable') == before
    assert (await api('/planning/status'))['versions'] == versions


@pytest.mark.anyio
async def test_instructor_unresolvable_block_leaves_draft_unchanged(live_planning):
    d = live_planning; api = d['api']
    await api('/planning/generate', 'POST', {'replace_existing': True}, expected=201)
    before = await api('/planning/timetable')
    await d['c'].execute("INSERT INTO instructor_availability(instructor_id,time_slot_id,status) SELECT $1,id,'unavailable' FROM time_slots", UUID(d['teachers'][0]['id']))
    failed = await api('/planning/generate', 'POST', {'replace_existing': True}, expected=422)
    assert any(item['instructor_id'] == d['teachers'][0]['id'] for item in failed['detail']['unscheduled'])
    assert await api('/planning/timetable') == before


@pytest.mark.anyio
async def test_delivery_mode_rejects_manual_room_mismatch(live_planning):
    d = live_planning; api = d['api']
    await api('/schedules', 'POST', {'course_id': d['courses'][1]['id'], 'group_id': d['groups'][1]['id'],
              'instructor_id': d['teachers'][1]['id'], 'room_id': d['rooms'][0]['id'], 'time_slot_id': d['slots'][0]['id']}, expected=409)


@pytest.mark.anyio
async def test_live_browser_generation_publication_and_personal_views(live_planning):
    if os.getenv('RUN_BROWSER_E2E') != '1':
        pytest.skip('Set RUN_BROWSER_E2E=1 with Playwright/browser installed for real browser integration')
    import asyncio
    import socket
    import subprocess
    import sys
    import httpx

    d = live_planning
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    env = os.environ.copy()
    dsn = os.environ['SPRINT2_TEST_DSN']
    env['DATABASE_URL'] = dsn + ('&' if '?' in dsn else '?') + 'search_path=' + d['schema'] + ',public'
    env.pop('BOOTSTRAP_ADMIN_EMAIL', None)
    env.pop('BOOTSTRAP_ADMIN_PASSWORD', None)
    env['PLANNING_TEST_BASE'] = f'http://127.0.0.1:{port}'
    env['PLANNING_TEST_HEADERS'] = json.dumps(d['headers'])
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', str(port)],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            for _ in range(100):
                if server.poll() is not None:
                    pytest.fail('Test HTTP server exited before readiness')
                try:
                    if (await client.get(env['PLANNING_TEST_BASE'] + '/openapi.json')).status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                await asyncio.sleep(0.1)
            else:
                pytest.fail('Test HTTP server did not become ready')
        # Run browser in a child process so the async API/test pool can continue servicing.
        process = await asyncio.create_subprocess_exec('node', 'tests/planning_live_browser.cjs', env=env,
                         stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        output, errors = await asyncio.wait_for(process.communicate(), timeout=90)
        assert process.returncode == 0, (output.decode(), errors.decode())
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


@pytest.mark.anyio
async def test_time_budget_is_not_reported_as_infeasibility_and_preserves_draft(live_planning, monkeypatch):
    import planning
    d = live_planning; api = d['api']
    await api('/planning/generate', 'POST', {'replace_existing': True}, expected=201)
    before = await api('/planning/timetable')
    original = planning.generate_schedule_matrix
    def expired(*args):
        arguments = list(args)
        arguments[-1] = {**arguments[-1], '_max_seconds': 0}
        return original(*arguments)
    monkeypatch.setattr(planning, 'generate_schedule_matrix', expired)
    response = await api('/planning/generate', 'POST', {'replace_existing': True}, expected=503)
    assert 'time limit' in response['detail']['unscheduled'][0]['reason']
    assert await api('/planning/timetable') == before
