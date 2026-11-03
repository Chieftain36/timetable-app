-- Additive Sprint 4–5 migration. Back up existing databases before applying.
BEGIN;

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
