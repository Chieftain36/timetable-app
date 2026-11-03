CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    CREATE TYPE user_role AS ENUM ('admin', 'instructor', 'student');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(320) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role user_role NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS courses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS rooms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(120) NOT NULL,
    building VARCHAR(120) NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT rooms_name_building_unique UNIQUE (name, building)
);

CREATE TABLE IF NOT EXISTS student_groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(120) NOT NULL UNIQUE,
    student_count INTEGER NOT NULL CHECK (student_count > 0)
);

CREATE TABLE IF NOT EXISTS instructors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    department VARCHAR(120) NOT NULL,
    title VARCHAR(120)
);

CREATE TABLE IF NOT EXISTS time_slots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    day_of_week SMALLINT NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    CONSTRAINT time_slots_valid_range CHECK (start_time < end_time),
    CONSTRAINT time_slots_unique_range UNIQUE (day_of_week, start_time, end_time)
);

CREATE TABLE IF NOT EXISTS schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    room_id UUID NOT NULL REFERENCES rooms(id) ON DELETE RESTRICT,
    instructor_id UUID NOT NULL REFERENCES instructors(id) ON DELETE RESTRICT,
    group_id UUID NOT NULL REFERENCES student_groups(id) ON DELETE RESTRICT,
    time_slot_id UUID NOT NULL REFERENCES time_slots(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT schedules_room_slot_unique UNIQUE (room_id, time_slot_id),
    CONSTRAINT schedules_instructor_slot_unique UNIQUE (instructor_id, time_slot_id),
    CONSTRAINT schedules_group_slot_unique UNIQUE (group_id, time_slot_id)
);

CREATE INDEX IF NOT EXISTS schedules_group_id_idx ON schedules (group_id);
CREATE INDEX IF NOT EXISTS schedules_room_id_idx ON schedules (room_id);
CREATE INDEX IF NOT EXISTS schedules_instructor_id_idx ON schedules (instructor_id);

-- Sprint 2: also safe to run on an existing Sprint 1 schema.
BEGIN;
CREATE EXTENSION IF NOT EXISTS btree_gist;
-- Existing unclassified rooms remain readable until explicitly classified.
ALTER TABLE rooms ADD COLUMN IF NOT EXISTS room_type VARCHAR(16);
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'rooms_type_rules' AND conrelid = 'rooms'::regclass) THEN
        ALTER TABLE rooms ADD CONSTRAINT rooms_type_rules CHECK (
            room_type IS NULL OR (
                building = left(name, 1) AND (
                    (room_type = 'practical' AND name ~ '^[D-I][1-3](0[1-9]|1[0-2])$' AND capacity BETWEEN 5 AND 25)
                    OR (room_type = 'lecture' AND name ~ '^[A-D][12]$' AND capacity BETWEEN 1 AND 100)
                )
            )
        );
    END IF;
END $$;
-- Preserve existing rooms; enforce the new limit on subsequent writes.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'rooms_lecture_capacity_v25' AND conrelid = 'rooms'::regclass) THEN
        ALTER TABLE rooms ADD CONSTRAINT rooms_lecture_capacity_v25
            CHECK (room_type IS DISTINCT FROM 'lecture' OR capacity BETWEEN 1 AND 70) NOT VALID;
    END IF;
END $$;
ALTER TABLE student_groups ADD COLUMN IF NOT EXISTS academic_year VARCHAR(9);
-- Legacy records are assigned to the academic year containing the migration date
-- (September through August). Review this assignment before migrating real data.
UPDATE student_groups SET academic_year =
    (EXTRACT(YEAR FROM CURRENT_DATE)::int - CASE WHEN EXTRACT(MONTH FROM CURRENT_DATE) < 9 THEN 1 ELSE 0 END)::text
    || '-' ||
    (EXTRACT(YEAR FROM CURRENT_DATE)::int + CASE WHEN EXTRACT(MONTH FROM CURRENT_DATE) < 9 THEN 0 ELSE 1 END)::text
WHERE academic_year IS NULL;
ALTER TABLE student_groups ALTER COLUMN academic_year SET NOT NULL;
ALTER TABLE student_groups DROP CONSTRAINT IF EXISTS student_groups_name_key;
CREATE UNIQUE INDEX IF NOT EXISTS student_groups_name_year_unique ON student_groups (name, academic_year);
CREATE UNIQUE INDEX IF NOT EXISTS student_groups_normalized_name_year_unique ON student_groups (upper(btrim(name)), academic_year);
ALTER TABLE student_groups ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS max_hours INTEGER NOT NULL DEFAULT 40;
ALTER TABLE instructors ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE instructors ADD COLUMN IF NOT EXISTS max_hours INTEGER NOT NULL DEFAULT 40;
-- Preserve exact minute-based limits from the uploaded application.
-- NULL means that max_hours supplies the limit in the hours-based API.
ALTER TABLE instructors ADD COLUMN IF NOT EXISTS max_weekly_minutes INTEGER;
ALTER TABLE instructors ALTER COLUMN max_weekly_minutes DROP NOT NULL;
ALTER TABLE instructors ALTER COLUMN max_weekly_minutes DROP DEFAULT;
ALTER TABLE instructors ADD COLUMN IF NOT EXISTS max_unavailable_slots INTEGER NOT NULL DEFAULT 10;
ALTER TABLE instructors ADD COLUMN IF NOT EXISTS availability_deadline TIMESTAMPTZ;
ALTER TABLE time_slots ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'groups_student_count_range' AND conrelid = 'student_groups'::regclass) THEN
        -- Fail atomically on out-of-range legacy data; never change counts silently.
        ALTER TABLE student_groups ADD CONSTRAINT groups_student_count_range CHECK (student_count BETWEEN 5 AND 50);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'instructor_minutes_positive' AND conrelid = 'instructors'::regclass) THEN
        ALTER TABLE instructors ADD CONSTRAINT instructor_minutes_positive CHECK (max_weekly_minutes > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'groups_year_format' AND conrelid = 'student_groups'::regclass) THEN
        ALTER TABLE student_groups ADD CONSTRAINT groups_year_format CHECK (academic_year ~ '^[0-9]{4}-[0-9]{4}$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'instructor_workload_limits' AND conrelid = 'instructors'::regclass) THEN
        ALTER TABLE instructors ADD CONSTRAINT instructor_workload_limits CHECK (max_hours > 0 AND max_unavailable_slots >= 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'course_workload_limits' AND conrelid = 'courses'::regclass) THEN
        ALTER TABLE courses ADD CONSTRAINT course_workload_limits CHECK (max_hours > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'slots_no_sunday' AND conrelid = 'time_slots'::regclass) THEN
        -- Existing Sundays remain readable, but cannot be newly created/updated.
        ALTER TABLE time_slots ADD CONSTRAINT slots_no_sunday CHECK (day_of_week BETWEEN 1 AND 6) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'slots_no_overlap' AND conrelid = 'time_slots'::regclass) THEN
        -- Migration fails rather than silently discarding overlapping legacy slots.
        ALTER TABLE time_slots ADD CONSTRAINT slots_no_overlap EXCLUDE USING gist (
            day_of_week WITH =,
            int4range(EXTRACT(EPOCH FROM start_time)::int, EXTRACT(EPOCH FROM end_time)::int, '[)') WITH &&
        );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS teaching_loads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    group_id UUID NOT NULL REFERENCES student_groups(id) ON DELETE RESTRICT,
    instructor_id UUID NOT NULL REFERENCES instructors(id) ON DELETE RESTRICT,
    sessions_per_week INTEGER NOT NULL CHECK (sessions_per_week BETWEEN 1 AND 14),
    session_minutes INTEGER NOT NULL DEFAULT 60 CHECK (session_minutes > 0 AND session_minutes <= 1440),
    delivery_mode VARCHAR(20) NOT NULL DEFAULT 'lecture' CHECK (delivery_mode IN ('lecture','practice','lab')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (course_id, group_id, instructor_id, delivery_mode)
);
CREATE INDEX IF NOT EXISTS teaching_loads_instructor_idx ON teaching_loads(instructor_id);
-- Upgrade the uploaded schema without losing planned_minutes or timestamps.
ALTER TABLE teaching_loads ADD COLUMN IF NOT EXISTS session_minutes INTEGER;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'teaching_loads'::regclass AND attname = 'planned_minutes' AND NOT attisdropped AND attgenerated = '') THEN
        IF EXISTS (SELECT 1 FROM teaching_loads WHERE session_minutes IS NULL AND (planned_minutes % sessions_per_week <> 0 OR planned_minutes / sessions_per_week NOT BETWEEN 1 AND 1440)) THEN
            RAISE EXCEPTION 'Cannot infer whole-minute session lengths from legacy planned_minutes; review teaching_loads before migrating';
        END IF;
        UPDATE teaching_loads SET session_minutes = planned_minutes / sessions_per_week WHERE session_minutes IS NULL;
        ALTER TABLE teaching_loads DROP COLUMN planned_minutes;
    END IF;
END $$;
UPDATE teaching_loads SET session_minutes = 60 WHERE session_minutes IS NULL;
ALTER TABLE teaching_loads ALTER COLUMN session_minutes SET DEFAULT 60;
ALTER TABLE teaching_loads ALTER COLUMN session_minutes SET NOT NULL;
ALTER TABLE teaching_loads ADD COLUMN IF NOT EXISTS delivery_mode VARCHAR(20) NOT NULL DEFAULT 'lecture';
ALTER TABLE teaching_loads ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE teaching_loads ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE teaching_loads ADD COLUMN IF NOT EXISTS planned_minutes INTEGER GENERATED ALWAYS AS (sessions_per_week * session_minutes) STORED;
ALTER TABLE teaching_loads DROP CONSTRAINT IF EXISTS teaching_loads_assignment_unique;
CREATE UNIQUE INDEX IF NOT EXISTS teaching_loads_assignment_mode_unique ON teaching_loads(course_id, group_id, instructor_id, delivery_mode);
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'teaching_loads_duration_mode_valid' AND conrelid = 'teaching_loads'::regclass) THEN
        ALTER TABLE teaching_loads ADD CONSTRAINT teaching_loads_duration_mode_valid CHECK (session_minutes BETWEEN 1 AND 1440 AND delivery_mode IN ('lecture','practice','lab'));
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS instructor_availability (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instructor_id UUID NOT NULL REFERENCES instructors(id) ON DELETE CASCADE,
    time_slot_id UUID NOT NULL REFERENCES time_slots(id) ON DELETE CASCADE,
    status VARCHAR(16) NOT NULL CHECK (status IN ('available','unavailable','preferred')),
    is_available BOOLEAN GENERATED ALWAYS AS (status <> 'unavailable') STORED,
    UNIQUE (instructor_id, time_slot_id)
);
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'instructor_availability'::regclass AND attname = 'is_available' AND NOT attisdropped AND attgenerated = '') THEN
        ALTER TABLE instructor_availability DROP CONSTRAINT IF EXISTS instructor_availability_status_consistency;
        ALTER TABLE instructor_availability DROP COLUMN is_available;
        ALTER TABLE instructor_availability ADD COLUMN is_available BOOLEAN GENERATED ALWAYS AS (status <> 'unavailable') STORED;
    END IF;
END $$;

-- Sprint 4–5: drafts, publications and personal timetable access

CREATE TABLE IF NOT EXISTS planning_settings (
 id integer PRIMARY KEY CHECK(id=1), preferences jsonb NOT NULL DEFAULT '{}'::jsonb
);
INSERT INTO planning_settings(id) VALUES(1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS course_room_rules (
 course_id uuid PRIMARY KEY REFERENCES courses(id) ON DELETE CASCADE,
 room_type text NOT NULL CHECK(room_type IN ('lecture','practical'))
);

CREATE TABLE IF NOT EXISTS student_group_links (
 user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 group_id uuid NOT NULL REFERENCES student_groups(id) ON DELETE RESTRICT
);
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS is_locked boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS timetable_versions (
 id bigserial PRIMARY KEY, kind text NOT NULL CHECK(kind IN ('draft','published')),
 created_at timestamptz NOT NULL DEFAULT now(), created_by uuid REFERENCES users(id) ON DELETE SET NULL,
 entries jsonb NOT NULL, preferences jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS timetable_versions_kind_id ON timetable_versions(kind,id DESC);

ALTER TABLE rooms ADD COLUMN IF NOT EXISTS features text[] NOT NULL DEFAULT '{}';
ALTER TABLE course_room_rules ADD COLUMN IF NOT EXISTS required_features text[] NOT NULL DEFAULT '{}';
ALTER TABLE planning_settings ADD COLUMN IF NOT EXISTS term_start date;
ALTER TABLE planning_settings ADD COLUMN IF NOT EXISTS term_end date;
ALTER TABLE timetable_versions ADD COLUMN IF NOT EXISTS valid_from date;
ALTER TABLE timetable_versions ADD COLUMN IF NOT EXISTS valid_until date;

COMMIT;
