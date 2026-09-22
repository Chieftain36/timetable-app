"""Deterministic, dependency-free timetable constraint solver."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import time
from typing import Any


class SchedulingError(ValueError):
    """Raised when one or more requested classes cannot be scheduled safely."""

    def __init__(self, unscheduled: list[dict[str, Any]]) -> None:
        self.unscheduled = unscheduled
        summary = ", ".join(
            f"course={item['course_id']}, group={item['group_id']}"
            for item in unscheduled
        )
        super().__init__(f"No valid room/time-slot placement for: {summary}")


def _value(record: Mapping[str, Any] | object, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    try:
        return record[name]  # type: ignore[index]
    except (KeyError, TypeError, IndexError):
        return getattr(record, name, default)


def _identity(value: Any) -> str:
    return str(value)


def _as_time(value: time | str | None) -> time | None:
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        return time.fromisoformat(value)
    return None


def _slot_sort_key(slot: Mapping[str, Any] | object) -> tuple[int, time, time, str]:
    start = _as_time(_value(slot, "start_time"))
    end = _as_time(_value(slot, "end_time"))
    if start is None or end is None:
        raise ValueError("Each time slot must have start_time and end_time")
    return int(_value(slot, "day_of_week")), start, end, _identity(_value(slot, "id"))


def _slots_overlap(
    first: Mapping[str, Any] | object,
    second: Mapping[str, Any] | object,
) -> bool:
    first_id = _value(first, "time_slot_id", _value(first, "id"))
    second_id = _value(second, "time_slot_id", _value(second, "id"))
    if first_id is not None and second_id is not None and first_id == second_id:
        return True

    first_day = _value(first, "day_of_week")
    second_day = _value(second, "day_of_week")
    first_start = _as_time(_value(first, "start_time"))
    first_end = _as_time(_value(first, "end_time"))
    second_start = _as_time(_value(second, "start_time"))
    second_end = _as_time(_value(second, "end_time"))
    if None in (first_day, second_day, first_start, first_end, second_start, second_end):
        return False
    return int(first_day) == int(second_day) and first_start < second_end and second_start < first_end


def _entry_slot(entry: Mapping[str, Any] | object) -> Mapping[str, Any] | object:
    return {
        "id": _value(entry, "time_slot_id"),
        "day_of_week": _value(entry, "day_of_week"),
        "start_time": _value(entry, "start_time"),
        "end_time": _value(entry, "end_time"),
    }


def _entry_conflicts_with_assignments(
    candidate: Mapping[str, Any],
    assignments: Iterable[Mapping[str, Any]],
) -> bool:
    candidate_slot = _entry_slot(candidate)
    for assigned in assignments:
        if not _slots_overlap(candidate_slot, _entry_slot(assigned)):
            continue
        if _identity(assigned["room_id"]) == _identity(candidate["room_id"]):
            return True
        if _identity(assigned["instructor_id"]) == _identity(candidate["instructor_id"]):
            return True
        if _identity(assigned["group_id"]) == _identity(candidate["group_id"]):
            return True
    return False


def generate_schedule_matrix(
    groups: Iterable[Mapping[str, Any] | object],
    courses: Iterable[Mapping[str, Any] | object],
    instructors: Iterable[Mapping[str, Any] | object],
    rooms: Iterable[Mapping[str, Any] | object],
    time_slots: Iterable[Mapping[str, Any] | object],
    existing_entries: Iterable[Mapping[str, Any] | object] = (),
) -> list[dict[str, Any]]:
    """Generate a reproducible set of non-conflicting schedule entries.

    Each item in ``courses`` is a scheduling requirement. In addition to ``id`` or
    ``course_id``, it must include ``group_id`` and ``instructor_id``; optional
    ``sessions_per_week`` defaults to one. Existing entries reserve their resources
    when the caller is adding to an already-published timetable.
    """
    group_map = {
        _identity(_value(group, "id")): group
        for group in groups
    }
    instructor_ids = {_identity(_value(instructor, "id")) for instructor in instructors}
    available_rooms = sorted(
        (
            room
            for room in rooms
            if bool(_value(room, "is_active", True))
        ),
        key=lambda room: (
            int(_value(room, "capacity")),
            str(_value(room, "building", "")),
            str(_value(room, "name", "")),
            _identity(_value(room, "id")),
        ),
    )
    ordered_slots = sorted(time_slots, key=_slot_sort_key)
    if not ordered_slots:
        raise SchedulingError(
            [{"course_id": "unknown", "group_id": "unknown", "reason": "No time slots exist"}]
        )

    expanded_requirements: list[dict[str, Any]] = []
    invalid_requirements: list[dict[str, Any]] = []
    for requirement in courses:
        course_id = _value(requirement, "course_id", _value(requirement, "id"))
        group_id = _value(requirement, "group_id")
        instructor_id = _value(requirement, "instructor_id")
        requirement_snapshot = {
            "course_id": course_id,
            "group_id": group_id,
            "instructor_id": instructor_id,
        }
        group = group_map.get(_identity(group_id))
        if course_id is None or group is None or _identity(instructor_id) not in instructor_ids:
            requirement_snapshot["reason"] = "Unknown course, group, or instructor"
            invalid_requirements.append(requirement_snapshot)
            continue

        student_count = int(_value(group, "student_count"))
        eligible_rooms = [
            room for room in available_rooms if int(_value(room, "capacity")) >= student_count
        ]
        if not eligible_rooms:
            requirement_snapshot["reason"] = "No active room has sufficient capacity"
            invalid_requirements.append(requirement_snapshot)
            continue

        sessions_per_week = int(_value(requirement, "sessions_per_week", 1))
        if sessions_per_week <= 0:
            requirement_snapshot["reason"] = "sessions_per_week must be positive"
            invalid_requirements.append(requirement_snapshot)
            continue
        for session_number in range(sessions_per_week):
            expanded_requirements.append(
                {
                    **requirement_snapshot,
                    "student_count": student_count,
                    "candidate_rooms": eligible_rooms,
                    "session_number": session_number,
                }
            )

    if invalid_requirements:
        raise SchedulingError(invalid_requirements)

    expanded_requirements.sort(
        key=lambda item: (
            len(item["candidate_rooms"]),
            -item["student_count"],
            _identity(item["course_id"]),
            _identity(item["group_id"]),
            _identity(item["instructor_id"]),
            item["session_number"],
        )
    )

    assignments: list[dict[str, Any]] = []
    for existing in existing_entries:
        assignments.append(
            {
                "id": _value(existing, "id"),
                "course_id": _value(existing, "course_id"),
                "room_id": _value(existing, "room_id"),
                "instructor_id": _value(existing, "instructor_id"),
                "group_id": _value(existing, "group_id"),
                "time_slot_id": _value(existing, "time_slot_id"),
                "day_of_week": _value(existing, "day_of_week"),
                "start_time": _value(existing, "start_time"),
                "end_time": _value(existing, "end_time"),
            }
        )

    generated: list[dict[str, Any]] = []
    unscheduled: list[dict[str, Any]] = []
    for requirement in expanded_requirements:
        placed: dict[str, Any] | None = None
        for slot in ordered_slots:
            for room in requirement["candidate_rooms"]:
                candidate = {
                    "course_id": requirement["course_id"],
                    "room_id": _value(room, "id"),
                    "instructor_id": requirement["instructor_id"],
                    "group_id": requirement["group_id"],
                    "time_slot_id": _value(slot, "id"),
                    "day_of_week": _value(slot, "day_of_week"),
                    "start_time": _value(slot, "start_time"),
                    "end_time": _value(slot, "end_time"),
                }
                if not _entry_conflicts_with_assignments(candidate, assignments):
                    placed = candidate
                    break
            if placed is not None:
                break
        if placed is None:
            unscheduled.append(
                {
                    "course_id": requirement["course_id"],
                    "group_id": requirement["group_id"],
                    "instructor_id": requirement["instructor_id"],
                    "reason": "All eligible rooms and time slots are occupied",
                }
            )
            continue
        assignments.append(placed)
        generated.append(
            {
                key: placed[key]
                for key in ("course_id", "room_id", "instructor_id", "group_id", "time_slot_id")
            }
        )

    if unscheduled:
        raise SchedulingError(unscheduled)
    return generated


def detect_schedule_conflicts(
    schedule_entries: Iterable[Mapping[str, Any] | object],
) -> list[dict[str, Any]]:
    """Return all capacity and resource-overlap violations in deterministic order."""
    entries = list(schedule_entries)
    conflicts: list[dict[str, Any]] = []

    def schedule_id(entry: Mapping[str, Any] | object) -> str:
        value = _value(entry, "id")
        if value is not None:
            return _identity(value)
        return ":".join(
            _identity(_value(entry, field, "unknown"))
            for field in ("course_id", "group_id", "instructor_id", "room_id", "time_slot_id")
        )

    for entry in sorted(entries, key=schedule_id):
        capacity = _value(entry, "room_capacity")
        student_count = _value(entry, "student_count", _value(entry, "group_student_count"))
        if capacity is not None and student_count is not None and int(capacity) < int(student_count):
            conflicts.append(
                {
                    "type": "room_capacity_violation",
                    "schedule_ids": [schedule_id(entry)],
                    "detail": f"Room capacity {capacity} is below group size {student_count}",
                }
            )

    resource_fields = (
        ("room_id", "room_double_booking", "Room"),
        ("instructor_id", "instructor_double_booking", "Instructor"),
        ("group_id", "group_double_booking", "Student group"),
    )
    for left_index, left in enumerate(entries):
        for right in entries[left_index + 1 :]:
            if not _slots_overlap(_entry_slot(left), _entry_slot(right)):
                continue
            for field, conflict_type, label in resource_fields:
                if _identity(_value(left, field)) != _identity(_value(right, field)):
                    continue
                conflicts.append(
                    {
                        "type": conflict_type,
                        "schedule_ids": sorted([schedule_id(left), schedule_id(right)]),
                        "detail": f"{label} is assigned to overlapping time slots",
                    }
                )

    return sorted(
        conflicts,
        key=lambda conflict: (conflict["type"], tuple(conflict["schedule_ids"])),
    )
