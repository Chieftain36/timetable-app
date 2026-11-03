"""Cross-sprint regression checks using disposable PostgreSQL, never production."""
from uuid import UUID

import pytest

from test_planning import anyio_backend, live_planning


@pytest.mark.anyio
async def test_live_account_password_role_and_catalog_lifecycle(live_planning):
    d = live_planning
    api, client = d['api'], d['client']
    password = 'Synthetic-test-password-123'
    user = await api('/users', 'POST', {
        'email': 'lifecycle@example.com', 'password': password, 'role': 'student',
    }, expected=201)
    assert 'password_hash' not in user

    async def login(email, secret, expected=200):
        r = await client.post('/api/v1/auth/login', json={'email': email, 'password': secret})
        assert r.status_code == expected, r.text
        return r.json()

    await login(user['email'], 'incorrect-password', 401)
    token = (await login(user['email'], password))['access_token']
    personal = {'Authorization': 'Bearer ' + token}
    assert (await client.get('/api/v1/planning/timetable', headers=personal)).json() == []
    assert (await client.get('/api/v1/users', headers=personal)).status_code == 403
    await api('/users/' + user['id'], 'PATCH', {'email': 'changed@example.com', 'role': 'instructor'})
    assert (await client.get('/api/v1/planning/status', headers=personal)).status_code == 401
    new_password = password + '-updated'
    await api('/users/' + user['id'], 'PATCH', {'password': new_password})
    await login('changed@example.com', password, 401)
    await login('changed@example.com', new_password)

    course = await api('/courses', 'POST', {'code': 'LIFECYCLE', 'name': 'Lifecycle course'}, expected=201)
    assert (await api('/courses/' + course['id'], 'PATCH', {'name': 'Updated course'}))['name'] == 'Updated course'
    group = await api('/student-groups', 'POST', {'code': 'LIFE', 'academic_year': '2026-2027', 'student_count': 15}, expected=201)
    assert (await api('/student-groups/' + group['id'], 'PATCH', {'student_count': 18}))['student_count'] == 18
    room = await api('/rooms', 'POST', {'name': 'E101', 'building': 'E', 'room_type': 'practical', 'capacity': 20}, expected=201)
    assert (await api('/rooms/' + room['id'], 'PATCH', {'capacity': 25}))['capacity'] == 25
    instructor = await api('/instructors', 'POST', {'user_id': user['id'], 'department': 'QA'}, expected=201)
    assert (await api('/instructors/' + instructor['id'], 'PATCH', {'department': 'Updated QA'}))['department'] == 'Updated QA'
    assert not (await api('/instructors/' + instructor['id'] + '/status', 'PATCH', {'is_active': False}))['is_active']
    assert (await api('/instructors/' + instructor['id'] + '/status', 'PATCH', {'is_active': True}))['is_active']
    slot = await api('/time-slots', 'POST', {'day_of_week': 3, 'start_time': '09:00', 'end_time': '10:00'}, expected=201)
    assert (await api('/time-slots/' + slot['id'], 'PATCH', {'start_time': '10:00', 'end_time': '11:00'}))['start_time'] == '10:00:00'
    load = await api('/teaching-loads', 'POST', {'course_id': course['id'], 'group_id': group['id'], 'instructor_id': instructor['id'], 'sessions_per_week': 1, 'session_minutes': 60, 'delivery_mode': 'practice'}, expected=201)
    await api('/teaching-loads/' + load['id'], 'PUT', {k: load[k] for k in ('course_id', 'group_id', 'instructor_id', 'sessions_per_week', 'session_minutes', 'delivery_mode')})
    # Dependent records cannot be removed accidentally.
    await api('/courses/' + course['id'], 'DELETE', expected=409)
    for path, row in [('teaching-loads', load), ('instructors', instructor), ('users', user),
                      ('courses', course), ('student-groups', group), ('rooms', room), ('time-slots', slot)]:
        await api('/' + path + '/' + row['id'], 'DELETE', expected=204)
        assert all(r['id'] != row['id'] for r in await api('/' + path))


@pytest.mark.anyio
@pytest.mark.parametrize('resource,label', [('room_id', 'Room'), ('instructor_id', 'Instructor'), ('group_id', 'Group')])
async def test_live_manual_resource_conflict_rejects_create_and_edit(live_planning, resource, label):
    d = live_planning
    api = d['api']
    course = await api('/courses', 'POST', {'code': 'MANUAL', 'name': 'Manual independent course'}, expected=201)
    def assignment(n, slot):
        return {'course_id': course['id'], 'room_id': d['rooms'][0 if n == 0 else 2]['id'],
                'group_id': d['groups'][n]['id'], 'instructor_id': d['teachers'][n]['id'],
                'time_slot_id': d['slots'][slot]['id']}
    first = assignment(0, 0)
    original = await api('/schedules', 'POST', first, expected=201)
    second = await api('/schedules', 'POST', assignment(1, 1), expected=201)
    candidate = {**assignment(1, 0), resource: first[resource]}
    rejected = await d['client'].post('/api/v1/schedules', json=candidate)
    assert rejected.status_code == 409 and label in rejected.json()['detail']
    rejected = await d['client'].patch('/api/v1/schedules/' + second['id'], json=candidate)
    assert rejected.status_code == 409 and label in rejected.json()['detail']
    unchanged = await d['c'].fetchrow('SELECT * FROM schedules WHERE id=$1', UUID(second['id']))
    assert str(unchanged['time_slot_id']) == second['time_slot_id']
    assert len(await api('/planning/timetable')) == 2
    assert (await api('/schedules/conflicts'))['conflicts'] == []
    edited = await api('/schedules/' + second['id'], 'PATCH', {'time_slot_id': d['slots'][2]['id']})
    assert edited['id'] == second['id']
    for row in (original, second):
        await api('/schedules/' + row['id'], 'DELETE')
    assert await api('/planning/timetable') == []
