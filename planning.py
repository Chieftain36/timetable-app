"""Sprint 4–5: versioned publications, owned views and safe draft generation.

Schema is installed explicitly (schema.sql for new DBs, migrations/004_planning.sql
for upgrades). Importing or starting the app never performs migrations.
"""
import json
from datetime import date, timedelta, time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from auth import get_current_user, require_roles
from database import DbConnection
from models import CurrentUser, ScheduleDraft, ScheduleRead, ScheduleGenerationRequest, UserRole
from scheduler import SchedulingError, generate_schedule_matrix, schedule_quality

router = APIRouter(prefix='/api/v1/planning', tags=['Planning'])
Admin = Annotated[CurrentUser, Depends(require_roles([UserRole.ADMIN]))]
User = Annotated[CurrentUser, Depends(get_current_user)]


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    avoid_early: bool = False
    earliest_preferred: time = time(9)
    minimize_gaps: bool = True
    balance_days: bool = True
    max_classes_per_day: int = Field(default=4, strict=True, ge=1, le=14)
    early_weight: int = Field(default=2, strict=True, ge=0, le=10)
    gap_weight: int = Field(default=3, strict=True, ge=0, le=10)
    balance_weight: int = Field(default=1, strict=True, ge=0, le=10)
    daily_weight: int = Field(default=2, strict=True, ge=0, le=10)


class Term(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: date | None = None
    end: date | None = None

    @model_validator(mode='after')
    def ordered(self):
        if self.start and self.end and self.end < self.start:
            raise ValueError('End date must not precede start date')
        return self


class RoomRule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    room_type: Literal['lecture', 'practical']
    required_features: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def valid_features(self):
        self.required_features = clean_features(self.required_features)
        return self


class StudentLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    group_id: UUID


class LockWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    locked: bool


class PublishWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm: Literal[True]


def clean_features(values):
    import re
    if any(not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,49}', value) for value in values):
        raise ValueError('Use lowercase feature tags, for example computer-lab or chemical-lab')
    return sorted(set(values))


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


async def ensure_ready(c):
    if not await c.fetchval("SELECT to_regclass('planning_settings')"):
        raise HTTPException(503, 'Planning schema is not installed. Apply migrations/004_planning.sql to a backed-up database.')


async def settings(c):
    await ensure_ready(c)
    row = await c.fetchrow('SELECT * FROM planning_settings WHERE id=1')
    return Preferences(**decode(row['preferences'])), Term(start=row['term_start'], end=row['term_end'])


async def all_draft(c):
    from main import SCHEDULE_SELECT
    return [dict(row) for row in await c.fetch(SCHEDULE_SELECT + ' ORDER BY t.day_of_week,t.start_time,r.name')]


async def latest_publication(c):
    return await c.fetchrow("SELECT * FROM timetable_versions WHERE kind='published' ORDER BY id DESC LIMIT 1")


async def read_timetable(c, user, source='draft', week_start=None, group_id=None,
                         room_id=None, instructor_id=None, course_id=None, q=None):
    await ensure_ready(c)
    # Choose owner scope BEFORE loading/filtering an immutable publication.
    owner_group = owner_instructor = None
    if user.role == UserRole.STUDENT:
        owner_group = await c.fetchval('SELECT group_id FROM student_group_links WHERE user_id=$1', user.id)
        if not owner_group:
            return []
    elif user.role == UserRole.INSTRUCTOR:
        owner_instructor = await c.fetchval('SELECT id FROM instructors WHERE user_id=$1 AND is_active', user.id)
        if not owner_instructor:
            return []
    valid_from = valid_until = None
    if user.role == UserRole.ADMIN and source == 'draft':
        rows = jsonable_encoder(await all_draft(c))
        _, term = await settings(c)
        valid_from, valid_until = term.start, term.end
    else:
        publication = await latest_publication(c)
        rows = decode(publication['entries']) if publication else []
        if publication:
            valid_from, valid_until = publication['valid_from'], publication['valid_until']
        if owner_group:
            rows = [row for row in rows if row['group_id'] == str(owner_group)]
        if owner_instructor:
            rows = [row for row in rows if row['instructor_id'] == str(owner_instructor)]
    if week_start:
        monday = week_start - timedelta(days=week_start.weekday())
        rows = [row for row in rows if (not valid_from or monday + timedelta(days=row['day_of_week']-1) >= valid_from)
                and (not valid_until or monday + timedelta(days=row['day_of_week']-1) <= valid_until)]
    for field, value in [('group_id', group_id), ('instructor_id', instructor_id), ('course_id', course_id), ('room_id', room_id)]:
        if value:
            rows = [row for row in rows if row[field] == str(value)]
    if q:
        rows = [row for row in rows if q.casefold() in ' '.join(str(row.get(k, '')) for k in
                ['course_code', 'course_name', 'group_name', 'instructor_email', 'room_name']).casefold()]
    return rows


async def room_rule_check(c, data, room):
    rule = await c.fetchrow('SELECT * FROM course_room_rules WHERE course_id = $1', data.course_id)
    if rule and (room.get('room_type') != rule['room_type'] or
                 not set(rule['required_features']).issubset(room.get('features', []))):
        raise HTTPException(409, 'Room does not match the course type or required laboratory features')
    if not rule:
        loads = await c.fetch('SELECT * FROM teaching_loads WHERE course_id = $1', data.course_id)
        modes = {row['delivery_mode'] for row in loads if row['is_active']
                 and row['group_id'] == data.group_id and row['instructor_id'] == data.instructor_id}
        allowed = {'lecture' if mode == 'lecture' else 'practical' for mode in modes}
        if allowed and room.get('room_type') not in allowed:
            raise HTTPException(409, 'Room type does not match the teaching assignment delivery mode')


async def validate_all(c, raw):
    from main import _validate_manual_schedule
    problems = []
    for row in raw:
        try:
            await _validate_manual_schedule(c, ScheduleDraft(**{k: row[k] for k in ScheduleDraft.model_fields}), row['id'])
        except HTTPException as error:
            problems.append({'schedule_id': str(row['id']), 'detail': error.detail})
    if problems:
        raise HTTPException(409, detail={'message': 'Draft validation failed; nothing was published or replaced.', 'conflicts': problems})


@router.get('/settings')
async def get_settings(_: Admin, connection: DbConnection):
    prefs, _ = await settings(connection)
    return prefs


@router.put('/settings')
async def put_settings(payload: Preferences, _: Admin, connection: DbConnection):
    await ensure_ready(connection)
    await connection.execute('UPDATE planning_settings SET preferences=$1::jsonb WHERE id=1', payload.model_dump_json())
    return payload


@router.get('/term')
async def get_term(_: Admin, connection: DbConnection):
    _, term = await settings(connection)
    return term


@router.put('/term')
async def put_term(payload: Term, _: Admin, connection: DbConnection):
    await ensure_ready(connection)
    await connection.execute('UPDATE planning_settings SET term_start=$1,term_end=$2 WHERE id=1', payload.start, payload.end)
    return payload


@router.get('/course-rules')
async def get_rules(_: Admin, connection: DbConnection):
    await ensure_ready(connection)
    return [dict(row) for row in await connection.fetch("SELECT c.id AS course_id,c.code,c.name,r.room_type,COALESCE(r.required_features,'{}'::text[]) AS required_features FROM courses c LEFT JOIN course_room_rules r ON r.course_id=c.id ORDER BY c.code")]


@router.put('/course-rules/{course_id}')
async def put_rule(course_id: UUID, payload: RoomRule, _: Admin, connection: DbConnection):
    await ensure_ready(connection)
    if not await connection.fetchval('SELECT 1 FROM courses WHERE id=$1', course_id):
        raise HTTPException(404, 'Course not found')
    await connection.execute('INSERT INTO course_room_rules(course_id,room_type,required_features) VALUES($1,$2,$3) ON CONFLICT(course_id) DO UPDATE SET room_type=EXCLUDED.room_type,required_features=EXCLUDED.required_features', course_id, payload.room_type, payload.required_features)
    return payload


@router.get('/student-links')
async def get_links(_: Admin, connection: DbConnection):
    await ensure_ready(connection)
    return [dict(row) for row in await connection.fetch("SELECT u.id AS user_id,u.email,l.group_id,g.name AS group_name FROM users u LEFT JOIN student_group_links l ON l.user_id=u.id LEFT JOIN student_groups g ON g.id=l.group_id WHERE u.role='student' ORDER BY u.email")]


@router.delete('/course-rules/{course_id}', status_code=204)
async def clear_rule(course_id: UUID, _: Admin, connection: DbConnection):
    await ensure_ready(connection)
    await connection.execute('DELETE FROM course_room_rules WHERE course_id=$1', course_id)


@router.put('/student-links/{user_id}')
async def put_link(user_id: UUID, payload: StudentLink, _: Admin, connection: DbConnection):
    await ensure_ready(connection)
    if not await connection.fetchval("SELECT 1 FROM users WHERE id=$1 AND role='student'", user_id):
        raise HTTPException(404, 'Student account not found')
    if not await connection.fetchval('SELECT 1 FROM student_groups WHERE id=$1 AND is_active', payload.group_id):
        raise HTTPException(409, 'Choose an active group')
    await connection.execute('INSERT INTO student_group_links(user_id,group_id) VALUES($1,$2) ON CONFLICT(user_id) DO UPDATE SET group_id=EXCLUDED.group_id', user_id, payload.group_id)
    return payload


@router.delete('/student-links/{user_id}', status_code=204)
async def unlink(user_id: UUID, _: Admin, connection: DbConnection):
    await ensure_ready(connection)
    await connection.execute('DELETE FROM student_group_links WHERE user_id=$1', user_id)


@router.get('/status')
async def get_status(user: User, connection: DbConnection):
    await ensure_ready(connection)
    published = await latest_publication(connection)
    result = {'published_version': published['id'] if published else None,
              'published_at': published['created_at'] if published else None,
              'valid_from': published['valid_from'] if published else None,
              'valid_until': published['valid_until'] if published else None}
    if user.role == UserRole.STUDENT:
        result['group_name'] = await connection.fetchval('SELECT g.name FROM student_groups g JOIN student_group_links l ON l.group_id=g.id WHERE l.user_id=$1', user.id)
    if user.role == UserRole.ADMIN:
        result.update(draft_count=await connection.fetchval('SELECT count(*) FROM schedules'),
                      locked_count=await connection.fetchval('SELECT count(*) FROM schedules WHERE is_locked'))
        result['versions'] = [dict(row) for row in await connection.fetch('SELECT id,kind,created_at,jsonb_array_length(entries) AS class_count FROM timetable_versions ORDER BY id DESC LIMIT 12')]
    return result


@router.get('/timetable', response_model=list[ScheduleRead])
async def timetable(user: User, connection: DbConnection,
                    source: Literal['draft', 'published'] = 'draft', week_start: date | None = None,
                    group_id: UUID | None = None, room_id: UUID | None = None,
                    instructor_id: UUID | None = None, course_id: UUID | None = None,
                    q: str | None = Query(default=None, max_length=100)):
    return await read_timetable(connection, user, source, week_start, group_id, room_id, instructor_id, course_id, q)


@router.put('/locks/{schedule_id}')
async def set_lock(schedule_id: UUID, payload: LockWrite, _: Admin, connection: DbConnection):
    await ensure_ready(connection)
    row = await connection.fetchrow('UPDATE schedules SET is_locked=$2 WHERE id=$1 RETURNING id,is_locked', schedule_id, payload.locked)
    if not row:
        raise HTTPException(404, 'Class not found')
    return dict(row)


@router.post('/publish')
async def publish(payload: PublishWrite, user: Admin, connection: DbConnection):
    await ensure_ready(connection)
    raw = await connection.fetch('SELECT * FROM schedules')
    if not raw:
        raise HTTPException(409, 'The draft is empty. Add or generate classes first.')
    await validate_all(connection, raw)
    prefs, term = await settings(connection)
    rows = await all_draft(connection)
    version = await connection.fetchval("INSERT INTO timetable_versions(kind,created_by,entries,preferences,valid_from,valid_until) VALUES('published',$1,$2::jsonb,$3::jsonb,$4,$5) RETURNING id", user.id, json.dumps(jsonable_encoder(rows)), prefs.model_dump_json(), term.start, term.end)
    return {'version': version, 'published_count': len(rows)}


@router.post('/generate', status_code=201)
async def generate(payload: ScheduleGenerationRequest, user: Admin, connection: DbConnection):
    await ensure_ready(connection)
    async with connection.transaction():
        # The shared request advisory lock already serializes cooperating writers.
        tables = {}
        for name in ['student_groups', 'instructors', 'rooms', 'time_slots', 'courses']:
            tables[name] = {row['id']: dict(row) for row in await connection.fetch('SELECT * FROM ' + name)}
        availability = await connection.fetch('SELECT * FROM instructor_availability')
        rules = {row['course_id']: dict(row) for row in await connection.fetch('SELECT * FROM course_room_rules')}
        existing = [dict(row) for row in await connection.fetch('SELECT * FROM schedules')]
        keep = [row for row in existing if row['is_locked'] or not payload.replace_existing]
        requirements = ([dict(row) for row in await connection.fetch('SELECT * FROM teaching_loads WHERE is_active')]
                        if payload.assignments is None else [row.model_dump() for row in payload.assignments])
        if not requirements:
            raise HTTPException(422, 'Add at least one active teaching load or assignment before generation.')
        if sum(item['sessions_per_week'] for item in requirements) > 2000 or len(tables['time_slots']) > 2000:
            raise HTTPException(422, 'This basic generator supports at most 2000 requested classes and 2000 time slots per run.')
        slots = tables['time_slots']
        retained = [{**row, 'day_of_week': slots[row['time_slot_id']]['day_of_week'],
                     'start_time': slots[row['time_slot_id']]['start_time'], 'end_time': slots[row['time_slot_id']]['end_time']} for row in keep]
        pending, consumed, identities = [], set(), set()
        for item in requirements:
            course = tables['courses'].get(item['course_id'])
            if not course or not course['is_active']:
                raise HTTPException(409, 'An assigned course is missing or inactive.')
            rule = rules.get(item['course_id'])
            mode = item.get('delivery_mode', 'lecture')
            item['required_room_type'] = rule['room_type'] if rule else ('lecture' if mode == 'lecture' else 'practical')
            item['required_features'] = rule['required_features'] if rule else []
            item['course_max_hours'] = course['max_hours']
            identity = tuple(item.get(k) for k in ['course_id', 'group_id', 'instructor_id', 'session_minutes']) + (mode,)
            if identity in identities:
                raise HTTPException(422, 'Duplicate assignments are not allowed.')
            identities.add(identity)
            if payload.replace_existing:
                count = 0
                for row in keep:
                    if row['id'] in consumed or not all(row[k] == item[k] for k in ['course_id', 'group_id', 'instructor_id']):
                        continue
                    slot, room = slots[row['time_slot_id']], tables['rooms'][row['room_id']]
                    minutes = (slot['end_time'].hour*60+slot['end_time'].minute)-(slot['start_time'].hour*60+slot['start_time'].minute)
                    if item.get('session_minutes') is not None and minutes != item['session_minutes']:
                        continue
                    if room['room_type'] == item['required_room_type'] and set(item['required_features']).issubset(room['features']):
                        count += 1
                        consumed.add(row['id'])
                if count > item['sessions_per_week']:
                    raise HTTPException(409, 'Locked classes exceed the requested count. Unlock them first.')
                item['sessions_per_week'] -= count
            if item['sessions_per_week']:
                pending.append(item)
        if payload.replace_existing and len(consumed) != len(keep):
            raise HTTPException(409, 'A locked class no longer matches the teaching requirements. Unlock or update the requirement first.')
        prefs, term = await settings(connection)
        try:
            generated = await run_in_threadpool(generate_schedule_matrix,
                tables['student_groups'].values(), pending, tables['instructors'].values(),
                tables['rooms'].values(), slots.values(), retained, availability,
                {**prefs.model_dump(mode='json'), '_max_seconds': 10}) if pending else []
        except SchedulingError as error:
            if any(item.get('reason', '').startswith('Generation time limit') for item in error.unscheduled):
                raise HTTPException(503, detail=jsonable_encoder({'message': 'Generation exceeded the 10 second limit; the previous draft is unchanged.',
                                                                 'unscheduled': error.unscheduled})) from None
            raise HTTPException(422, detail=jsonable_encoder({'message': 'Infeasible schedule: no complete placement satisfies the hard rules.', 'unscheduled': error.unscheduled})) from None
        if payload.replace_existing:
            await connection.execute('DELETE FROM schedules WHERE NOT is_locked')
        for row in generated:
            await connection.execute('INSERT INTO schedules(course_id,room_id,instructor_id,group_id,time_slot_id) VALUES($1,$2,$3,$4,$5)', *(row[k] for k in ScheduleDraft.model_fields))
        # Validate retained AND generated rows in the final draft, not against
        # unlocked old rows that replacement has deliberately removed.
        await validate_all(connection, await connection.fetch('SELECT * FROM schedules'))
        rows = await all_draft(connection)
        quality = schedule_quality(rows, prefs.model_dump(mode='json'))
        version = await connection.fetchval("INSERT INTO timetable_versions(kind,created_by,entries,preferences,valid_from,valid_until) VALUES('draft',$1,$2::jsonb,$3::jsonb,$4,$5) RETURNING id", user.id, json.dumps(jsonable_encoder(rows)), prefs.model_dump_json(), term.start, term.end)
        return {'generated_count': len(generated), 'retained_count': len(keep), 'draft_version': version,
                'entries': generated, 'quality': quality, 'hard_conflicts': 0,
                'log': ['Validated hard constraints (0 conflicts)', f'Placed {len(generated)} classes; retained {len(keep)} locked/existing classes', 'Saved draft; official publication unchanged']}
