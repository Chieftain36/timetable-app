from datetime import datetime, time
from enum import Enum
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    BaseModel,
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


class UpdateModel(ApiModel):
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


class CourseCreate(ApiModel):
    code: Annotated[
        str,
        Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9][A-Z0-9_-]*$"),
    ]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str | None, Field(max_length=2_000)] = None

    @field_validator("code", mode="before")
    @classmethod
    def normalize_course_code(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class CourseUpdate(UpdateModel):
    code: Annotated[
        str | None,
        Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9][A-Z0-9_-]*$"),
    ] = None
    name: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    description: Annotated[str | None, Field(max_length=2_000)] = None

    @field_validator("code", mode="before")
    @classmethod
    def normalize_optional_course_code(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class CourseRead(ApiModel):
    id: UUID
    code: str
    name: str
    description: str | None


class RoomCreate(ApiModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    building: Annotated[str, Field(min_length=1, max_length=120)]
    capacity: Annotated[int, Field(gt=0)]
    is_active: bool = True


class RoomUpdate(UpdateModel):
    name: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    building: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    capacity: Annotated[int | None, Field(gt=0)] = None
    is_active: bool | None = None


class RoomRead(ApiModel):
    id: UUID
    name: str
    building: str
    capacity: int
    is_active: bool


class StudentGroupCreate(ApiModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    student_count: Annotated[int, Field(gt=0)]


class StudentGroupUpdate(UpdateModel):
    name: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    student_count: Annotated[int | None, Field(gt=0)] = None


class StudentGroupRead(ApiModel):
    id: UUID
    name: str
    student_count: int


class InstructorCreate(ApiModel):
    user_id: UUID
    department: Annotated[str, Field(min_length=1, max_length=120)]
    title: Annotated[str | None, Field(max_length=120)] = None


class InstructorUpdate(UpdateModel):
    user_id: UUID | None = None
    department: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    title: Annotated[str | None, Field(max_length=120)] = None


class InstructorRead(ApiModel):
    id: UUID
    user_id: UUID
    email: EmailStr
    department: str
    title: str | None


class TimeSlotCreate(ApiModel):
    day_of_week: Annotated[int, Field(ge=1, le=7)]
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        if self.start_time >= self.end_time:
            raise ValueError("end_time must be later than start_time")
        return self


class TimeSlotUpdate(UpdateModel):
    day_of_week: Annotated[int | None, Field(ge=1, le=7)] = None
    start_time: time | None = None
    end_time: time | None = None

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
    id: UUID
    day_of_week: int
    start_time: time
    end_time: time


class SchedulingRequirement(ApiModel):
    course_id: UUID
    group_id: UUID
    instructor_id: UUID
    sessions_per_week: Annotated[int, Field(gt=0, le=14)] = 1


class ScheduleGenerationRequest(ApiModel):
    assignments: Annotated[list[SchedulingRequirement], Field(min_length=1, max_length=500)]
    replace_existing: bool = False


class ScheduleDraft(ApiModel):
    course_id: UUID
    room_id: UUID
    instructor_id: UUID
    group_id: UUID
    time_slot_id: UUID


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
