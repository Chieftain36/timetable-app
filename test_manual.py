from copy import deepcopy
from datetime import time
from uuid import uuid4
import pytest
from httpx import ASGITransport, AsyncClient
from test_app import app_with_fake_database, anyio_backend, headers_for, seed_sprint2


def seed(database):
    teacher,group,course,room=seed_sprint2(database)
    return dict(course_id=str(course['id']),instructor_id=str(teacher['id']),group_id=str(group['id']),room_id=str(room['id']),time_slot_id=str(database.time_slots[0]['id']))


@pytest.mark.anyio
async def test_manual_create_edit_delete(app_with_fake_database):
    app,db=app_with_fake_database;payload=seed(db)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers=headers_for(db)) as client:
        created=await client.post('/api/v1/schedules',json=payload)
        assert created.status_code==201,created.text
        identifier=created.json()['id']
        # Updating a class must not collide with itself.
        assert (await client.patch('/api/v1/schedules/'+identifier,json=payload)).status_code==200
        changed=await client.patch('/api/v1/schedules/'+identifier,json={'time_slot_id':str(db.time_slots[1]['id'])})
        assert changed.status_code==200,changed.text
        assert changed.json()['id']==identifier and changed.json()['time_slot_id']==str(db.time_slots[1]['id'])
        assert (await client.delete('/api/v1/schedules/'+identifier)).status_code==200
        assert not db.schedules


@pytest.mark.anyio
@pytest.mark.parametrize('resource,label',[('room_id','Room'),('instructor_id','Instructor'),('group_id','Group')])
async def test_manual_overlap_and_failed_edit(app_with_fake_database,resource,label):
    app,db=app_with_fake_database;first=seed(db)
    second=deepcopy(first)
    for field,table in [('room_id','rooms'),('instructor_id','instructors'),('group_id','student_groups')]:
        row=deepcopy(getattr(db,table)[0]);row['id']=uuid4();getattr(db,table).append(row);second[field]=str(row['id'])
    second['time_slot_id']=str(db.time_slots[1]['id'])
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers=headers_for(db)) as client:
        assert (await client.post('/api/v1/schedules',json=first)).status_code==201
        existing=await client.post('/api/v1/schedules',json=second);assert existing.status_code==201
        overlap=dict(id=uuid4(),day_of_week=1,start_time=time(9,30),end_time=time(10,30),is_active=True);db.time_slots.append(overlap)
        candidate={**second,resource:first[resource],'time_slot_id':str(overlap['id'])}
        result=await client.post('/api/v1/schedules',json=candidate)
        assert result.status_code==409 and label in result.json()['detail']
        before=deepcopy(db.schedules)
        result=await client.patch('/api/v1/schedules/'+existing.json()['id'],json=candidate)
        assert result.status_code==409 and label in result.json()['detail']
        assert db.schedules==before


@pytest.mark.anyio
@pytest.mark.parametrize('reason',['unavailable','capacity','instructor','group','course','room','slot','sunday','instructor_limit','course_limit'])
async def test_manual_rejects_invalid_assignments(app_with_fake_database,reason):
    app,db=app_with_fake_database;payload=seed(db)
    if reason=='unavailable':db.instructor_availability.append(dict(id=uuid4(),instructor_id=db.instructors[0]['id'],time_slot_id=db.time_slots[0]['id'],status='unavailable'))
    elif reason=='capacity':db.rooms[0]['capacity']=5
    elif reason=='sunday':db.time_slots[0]['day_of_week']=7
    elif reason=='instructor_limit':db.instructors[0]['max_weekly_minutes']=30
    elif reason=='course_limit':db.courses[0]['max_hours']=0
    else:getattr(db,{'instructor':'instructors','group':'student_groups','course':'courses','room':'rooms','slot':'time_slots'}[reason])[0]['is_active']=False
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers=headers_for(db)) as client:
        response=await client.post('/api/v1/schedules',json=payload)
        assert response.status_code==409,response.text
        assert not db.schedules


@pytest.mark.anyio
async def test_manual_permissions_and_unknown_ids(app_with_fake_database):
    app,db=app_with_fake_database;payload=seed(db)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        for method,path in [('POST','/api/v1/schedules'),('PATCH','/api/v1/schedules/'+str(uuid4()))]:
            assert (await client.request(method,path,json=payload)).status_code==401
            assert (await client.request(method,path,json=payload,headers=headers_for(db,'student@example.com'))).status_code==403
        assert (await client.post('/api/v1/schedules',json={**payload,'room_id':str(uuid4())},headers=headers_for(db))).status_code==404
        assert (await client.patch('/api/v1/schedules/'+str(uuid4()),json={},headers=headers_for(db))).status_code==422


@pytest.mark.anyio
@pytest.mark.parametrize('kind,name,building,count,expected',[
    ('practical','D101','D',5,201),('practical','I312','I',25,201),
    ('practical','D101','D',4,422),('practical','D101','D',26,422),
    ('practical','D113','D',20,422),('practical','D401','D',20,422),
    ('practical','C101','C',20,422),('practical','I301','D',20,422),
    ('lecture','A1','A',1,201),('lecture','D2','D',70,201),
    ('lecture','A3','A',20,422),('lecture','E1','E',20,422),
    ('lecture','A1','A',0,422),('lecture','A1','A',71,422),
    ('lecture','A1','A',5.5,422),('practical','D101','D','20',422),
])
async def test_room_type_rules(app_with_fake_database,kind,name,building,count,expected):
    app,db=app_with_fake_database
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers=headers_for(db)) as client:
        response=await client.post('/api/v1/rooms',json=dict(room_type=kind,name=name,building=building,capacity=count))
        assert response.status_code==expected,response.text
        if expected==201:
            invalid=await client.patch('/api/v1/rooms/'+response.json()['id'],json={'capacity':26 if kind=='practical' else 101})
            assert invalid.status_code==422,invalid.text
