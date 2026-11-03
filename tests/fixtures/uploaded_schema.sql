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
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
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
    name VARCHAR(120) NOT NULL,
    academic_year VARCHAR(9) NOT NULL,
    student_count INTEGER NOT NULL CHECK (student_count > 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT student_groups_academic_year_format CHECK (academic_year ~ '^[0-9]{4}-[0-9]{4}$'),
    CONSTRAINT student_groups_name_academic_year_unique UNIQUE (name, academic_year)
);

CREATE TABLE IF NOT EXISTS instructors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    department VARCHAR(120) NOT NULL,
    title VARCHAR(120),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    max_weekly_minutes INTEGER NOT NULL DEFAULT 240 CHECK (max_weekly_minutes > 0)
);

CREATE TABLE IF NOT EXISTS time_slots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    day_of_week SMALLINT NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT time_slots_valid_range CHECK (start_time < end_time),
    CONSTRAINT time_slots_unique_range UNIQUE (day_of_week, start_time, end_time)
);

CREATE TABLE IF NOT EXISTS teaching_loads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    group_id UUID NOT NULL REFERENCES student_groups(id) ON DELETE RESTRICT,
    instructor_id UUID NOT NULL REFERENCES instructors(id) ON DELETE RESTRICT,
    sessions_per_week INTEGER NOT NULL CHECK (sessions_per_week > 0 AND sessions_per_week <= 14),
    planned_minutes INTEGER NOT NULL CHECK (planned_minutes > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT teaching_loads_assignment_unique UNIQUE (course_id, group_id, instructor_id)
);

CREATE TABLE IF NOT EXISTS instructor_availability (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instructor_id UUID NOT NULL REFERENCES instructors(id) ON DELETE CASCADE,
    time_slot_id UUID NOT NULL REFERENCES time_slots(id) ON DELETE CASCADE,
    status VARCHAR(12) NOT NULL DEFAULT 'available'
        CHECK (status IN ('available', 'unavailable', 'preferred')),
    is_available BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT instructor_availability_instructor_slot_unique UNIQUE (instructor_id, time_slot_id),
    CONSTRAINT instructor_availability_status_consistency CHECK (
        (status = 'unavailable' AND is_available = FALSE)
        OR (status IN ('available', 'preferred') AND is_available = TRUE)
    )
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

-- Migration-safe alterations for databases created by the Sprint 1 schema.
ALTER TABLE courses ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE student_groups ADD COLUMN IF NOT EXISTS academic_year VARCHAR(9);
ALTER TABLE student_groups ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
UPDATE student_groups SET academic_year = '2026-2027' WHERE academic_year IS NULL;
ALTER TABLE student_groups ALTER COLUMN academic_year SET NOT NULL;
ALTER TABLE student_groups DROP CONSTRAINT IF EXISTS student_groups_name_key;
ALTER TABLE student_groups DROP CONSTRAINT IF EXISTS student_groups_academic_year_format;
ALTER TABLE student_groups ADD CONSTRAINT student_groups_academic_year_format
    CHECK (academic_year ~ '^[0-9]{4}-[0-9]{4}$') NOT VALID;
ALTER TABLE student_groups VALIDATE CONSTRAINT student_groups_academic_year_format;
ALTER TABLE student_groups DROP CONSTRAINT IF EXISTS student_groups_name_academic_year_unique;
ALTER TABLE student_groups ADD CONSTRAINT student_groups_name_academic_year_unique UNIQUE (name, academic_year);
ALTER TABLE instructors ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE instructors ADD COLUMN IF NOT EXISTS max_weekly_minutes INTEGER NOT NULL DEFAULT 240;
ALTER TABLE instructors DROP CONSTRAINT IF EXISTS instructors_max_weekly_minutes_positive;
ALTER TABLE instructors ADD CONSTRAINT instructors_max_weekly_minutes_positive CHECK (max_weekly_minutes > 0);
ALTER TABLE time_slots ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS schedules_group_id_idx ON schedules (group_id);
CREATE INDEX IF NOT EXISTS schedules_room_id_idx ON schedules (room_id);
CREATE INDEX IF NOT EXISTS schedules_instructor_id_idx ON schedules (instructor_id);
CREATE INDEX IF NOT EXISTS teaching_loads_instructor_id_idx ON teaching_loads (instructor_id);
CREATE INDEX IF NOT EXISTS instructor_availability_instructor_id_idx ON instructor_availability (instructor_id);
