from datetime import datetime, time
from enum import Enum
from typing import Annotated, Self, Literal
from uuid import UUID
import re
import unicodedata

from pydantic_core import PydanticCustomError

from pydantic import (
    BaseModel,
    AliasChoices,
    computed_field,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


class UserRole(str, Enum):
    ADMIN = "admin"
    INSTRUCTOR = "instructor"
    STUDENT = "student"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def validate_safe_text(value: str) -> str:
    if not value.strip():
        raise PydanticCustomError("text_empty", "field must not be empty")
    if any(
        character not in "0123456789- "
        and not (
            unicodedata.category(character).startswith("L")
            and unicodedata.name(character, "").startswith(("LATIN ", "CYRILLIC "))
        )
        for character in value
    ):
        raise PydanticCustomError("text_characters", "forbidden characters")
    return value.strip()


class TextInputModel(ApiModel):
    # Only input models inherit this validator; existing stored rows remain readable.
    @field_validator("name", "code", "department", "title", "building", "description", check_fields=False, mode="before")
    @classmethod
    def validate_text_fields(cls, value: object) -> object:
        return validate_safe_text(value) if isinstance(value, str) else value


class UpdateModel(TextInputModel):
    @model_validator(mode="before")
    @classmethod
    def reject_null_required_fields(cls, values: object) -> object:
        if isinstance(values, dict):
            for name, value in values.items():
                if value is None and name not in {"description", "title", "availability_deadline"}:
                    raise PydanticCustomError("null_update", "field {field} cannot be null", {"field": name})
        return values

    @model_validator(mode="after")
    def require_at_least_one_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one field must be supplied for an update")
        return self


class LoginRequest(ApiModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=72)]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()


class UserCreate(LoginRequest):
    password: Annotated[str, Field(min_length=8, max_length=72)]
    role: UserRole


class UserUpdate(UpdateModel):
    email: EmailStr | None = None
    password: Annotated[str | None, Field(min_length=8, max_length=72)] = None
    role: UserRole | None = None

    @field_validator("email")
    @classmethod
    def normalize_optional_email(cls, value: EmailStr | None) -> str | None:
        return str(value).lower() if value is not None else None


class UserRead(ApiModel):
    id: UUID
    email: EmailStr
    role: UserRole
    created_at: datetime


class CurrentUser(ApiModel):
    id: UUID
    email: EmailStr
    role: UserRole


class TokenResponse(ApiModel):
    access_token: str
    token_type: str = "bearer"


class CourseCreate(TextInputModel):
    is_active: bool = True
    max_hours: Annotated[int, Field(strict=True, gt=0, le=168)] = 40
    code: Annotated[
        str,
        Field(min_length=2, max_length=32),
    ]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str | None, Field(max_length=2_000)] = None

    @field_validator("code", mode="before")
    @classmethod
    def normalize_course_code(cls, value: object) -> object:
        return validate_safe_text(value).upper() if isinstance(value, str) else value


class CourseUpdate(UpdateModel):
    is_active: bool | None = None
    max_hours: Annotated[int | None, Field(strict=True, gt=0, le=168)] = None
    code: Annotated[
        str | None,
        Field(min_length=2, max_length=32),
    ] = None
    name: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    description: Annotated[str | None, Field(max_length=2_000)] = None

    @field_validator("code", mode="before")
    @classmethod
    def normalize_optional_course_code(cls, value: object) -> object:
        return validate_safe_text(value).upper() if isinstance(value, str) else value


class CourseRead(ApiModel):
    is_active: bool = True
    max_hours: int = 40
    id: UUID
    code: str
    name: str
    description: str | None


class RoomCreate(TextInputModel):
    room_type: Literal['practical', 'lecture'] = 'practical'
    name: Annotated[str, Field(min_length=1, max_length=120)]
    building: Annotated[str, Field(min_length=1, max_length=120)]
    capacity: Annotated[int, Field(strict=True, ge=1, le=70)]
    is_active: bool = True

    @model_validator(mode="after")
    def room_rules(self) -> Self:
        self.name = self.name.upper()
        self.building = self.building.upper()
        pattern = r'[D-I][1-3](?:0[1-9]|1[0-2])' if self.room_type == 'practical' else r'[A-D][12]'
        if not re.fullmatch(pattern, self.name) or self.building != self.name[0]:
            raise PydanticCustomError('room_code', 'Choose a valid room number and matching building')
        if self.room_type == 'practical' and not 5 <= self.capacity <= 25:
            raise PydanticCustomError('room_capacity', 'Practical room capacity must be 5 to 25')
        return self


class RoomUpdate(UpdateModel):
    room_type: Literal['practical', 'lecture'] | None = None
    name: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    building: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    capacity: Annotated[int | None, Field(strict=True, ge=1, le=70)] = None
    is_active: bool | None = None


class RoomRead(ApiModel):
    room_type: str | None = None
    id: UUID
    name: str
    building: str
    capacity: int
    is_active: bool


class GroupYearMixin(TextInputModel):
    @field_validator("academic_year", check_fields=False, mode="before")
    @classmethod
    def validate_year(cls, value: object) -> object:
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{4}", value) is None:
            raise PydanticCustomError("academic_year_format", "expected YYYY-YYYY format")
        return value

    @field_validator("name", check_fields=False, mode="before")
    @classmethod
    def normalize_group_code(cls, value: object) -> object:
        return validate_safe_text(value).upper() if isinstance(value, str) else value


class StudentGroupCreate(GroupYearMixin):
    name: Annotated[str, Field(min_length=1, max_length=120, validation_alias=AliasChoices("code", "name"))]
    academic_year: str
    student_count: Annotated[int, Field(strict=True, ge=5, le=50)]
    is_active: bool = True


class StudentGroupUpdate(UpdateModel, GroupYearMixin):
    name: Annotated[str | None, Field(min_length=1, max_length=120, validation_alias=AliasChoices("code", "name"))] = None
    academic_year: str | None = None
    student_count: Annotated[int | None, Field(strict=True, ge=5, le=50)] = None
    is_active: bool | None = None


class StudentGroupRead(ApiModel):
    id: UUID
    name: str
    academic_year: str
    student_count: int
    is_active: bool

    @computed_field
    @property
    def code(self) -> str:
        return self.name


StudentGroupResponse = StudentGroupRead


class InstructorCreate(TextInputModel):
    max_weekly_minutes: Annotated[int | None, Field(strict=True, gt=0, le=10080)] = None
    is_active: bool = True
    max_hours: Annotated[int, Field(strict=True, gt=0, le=168)] = 40
    max_unavailable_slots: Annotated[int, Field(strict=True, ge=0)] = 10
    user_id: UUID
    department: Annotated[str, Field(min_length=1, max_length=120)]
    title: Annotated[str | None, Field(max_length=120)] = None


class InstructorUpdate(UpdateModel):
    max_weekly_minutes: Annotated[int | None, Field(strict=True, gt=0, le=10080)] = None
    is_active: bool | None = None
    confirm_deactivation: bool = False
    user_id: UUID | None = None
    department: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    title: Annotated[str | None, Field(max_length=120)] = None


class InstructorRead(ApiModel):
    max_weekly_minutes: int | None = None
    is_active: bool
    max_hours: int
    max_unavailable_slots: int
    availability_deadline: datetime | None = None
    id: UUID
    user_id: UUID
    email: EmailStr
    department: str
    title: str | None


class TimeSlotCreate(ApiModel):
    is_active: bool = True
    day_of_week: Annotated[int, Field(strict=True, ge=1, le=6)]
    start_time: time
    end_time: time

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def validate_clock_time(cls, value: object) -> object:
        if not isinstance(value, str) or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9])?", value) is None:
            raise PydanticCustomError("clock_format", "expected HH:MM or HH:MM:SS time")
        return value

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        if self.start_time >= self.end_time:
            raise ValueError("end_time must be later than start_time")
        return self


class TimeSlotUpdate(UpdateModel):
    is_active: bool | None = None
    day_of_week: Annotated[int | None, Field(strict=True, ge=1, le=6)] = None
    start_time: time | None = None
    end_time: time | None = None

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def validate_clock_time(cls, value: object) -> object:
        return TimeSlotCreate.validate_clock_time(value)

    @model_validator(mode="after")
    def validate_partial_time_range(self) -> Self:
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.start_time >= self.end_time
        ):
            raise ValueError("end_time must be later than start_time")
        return self


class TimeSlotRead(ApiModel):
    is_active: bool = True
    id: UUID
    day_of_week: int
    start_time: time
    end_time: time


class SchedulingRequirement(ApiModel):
    session_minutes: Annotated[int | None, Field(strict=True, gt=0, le=1440)] = None
    course_id: UUID
    group_id: UUID
    instructor_id: UUID
    sessions_per_week: Annotated[int, Field(strict=True, gt=0, le=14)] = 1


class ScheduleGenerationRequest(ApiModel):
    assignments: Annotated[list[SchedulingRequirement] | None, Field(min_length=1, max_length=500)] = None
    replace_existing: bool = False


class ScheduleDraft(ApiModel):
    course_id: UUID
    room_id: UUID
    instructor_id: UUID
    group_id: UUID
    time_slot_id: UUID


class ScheduleUpdate(UpdateModel):
    course_id: UUID | None = None
    room_id: UUID | None = None
    instructor_id: UUID | None = None
    group_id: UUID | None = None
    time_slot_id: UUID | None = None


class ManualScheduleRead(ScheduleDraft):
    id: UUID
    created_at: datetime


class ScheduleGenerationResponse(ApiModel):
    generated_count: int
    replace_existing: bool
    entries: list[ScheduleDraft]


class ScheduleRead(ApiModel):
    id: UUID
    course_id: UUID
    room_id: UUID
    instructor_id: UUID
    group_id: UUID
    time_slot_id: UUID
    created_at: datetime
    course_code: str
    course_name: str
    room_name: str
    room_building: str
    group_name: str
    student_count: int
    instructor_email: EmailStr
    day_of_week: int
    start_time: time
    end_time: time


class ScheduleConflict(ApiModel):
    type: str
    schedule_ids: list[str]
    detail: str


class ConflictResponse(ApiModel):
    conflicts: list[ScheduleConflict]


class InstructorStatusUpdate(ApiModel):
    is_active: bool
    confirm_deactivation: bool = False


class WorkloadUpdate(UpdateModel):
    max_weekly_minutes: Annotated[int | None, Field(strict=True, gt=0, le=10080)] = None

    @model_validator(mode="after")
    def one_limit(self) -> Self:
        if {"max_hours", "max_weekly_minutes"} <= self.model_fields_set:
            raise ValueError("Supply either max_hours or max_weekly_minutes")
        return self

    max_hours: Annotated[int | None, Field(strict=True, gt=0, le=168)] = None
    max_unavailable_slots: Annotated[int | None, Field(strict=True, ge=0)] = None
    availability_deadline: datetime | None = None

    @field_validator("availability_deadline")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("Include the deadline time zone")
        return value


class WorkloadRead(ApiModel):
    max_weekly_minutes: int

    @computed_field
    @property
    def scheduled_minutes(self) -> float:
        return self.actual_minutes

    @computed_field
    @property
    def remaining_minutes(self) -> float:
        return self.max_weekly_minutes - max(self.planned_minutes, self.actual_minutes)

    instructor_id: UUID
    max_hours: int
    planned_minutes: int
    actual_minutes: float
    max_unavailable_slots: int
    availability_deadline: datetime | None


class TeachingLoadCreate(ApiModel):
    course_id: UUID
    group_id: UUID
    instructor_id: UUID
    sessions_per_week: Annotated[int, Field(strict=True, gt=0, le=14)]
    session_minutes: Annotated[int, Field(strict=True, gt=0, le=1440)] = 60
    delivery_mode: str = "lecture"
    is_active: bool = True

    @field_validator("delivery_mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in {"lecture", "practice", "lab"}:
            raise ValueError("Allowed values: lecture, practice, lab")
        return value


class TeachingLoadRead(TeachingLoadCreate):
    id: UUID
    planned_minutes: int | None = None
    created_at: datetime | None = None


class AvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    PREFERRED = "preferred"


class AvailabilityEntry(ApiModel):
    time_slot_id: UUID
    status: AvailabilityStatus


class AvailabilityWrite(ApiModel):
    entries: Annotated[list[AvailabilityEntry], Field(max_length=10000)]

    @model_validator(mode="after")
    def unique_slots(self) -> Self:
        if len({entry.time_slot_id for entry in self.entries}) != len(self.entries):
            raise ValueError("A slot was supplied more than once")
        return self


class AvailabilityRead(ApiModel):
    slots: list[TimeSlotRead]
    entries: list[AvailabilityEntry]
    max_unavailable_slots: int
    min_available_minutes: float