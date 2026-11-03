import os
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, ValidationError

from models import CurrentUser, UserRole
from database import DbConnection


JWT_ALGORITHM = "HS256"
password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


class TokenPayload(BaseModel):
    sub: UUID
    email: EmailStr
    role: UserRole


def hash_password(password: str) -> str:
    """Create a bcrypt password hash. Password length is validated by request models."""
    return password_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Return False instead of exposing malformed or unsupported password hashes."""
    try:
        return password_context.verify(plain_password, password_hash)
    except (ValueError, TypeError):
        return False


def _jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY environment variable is required")
    return secret


def _token_lifetime() -> timedelta:
    try:
        minutes = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    except ValueError as exc:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES must be an integer") from exc
    if minutes <= 0:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES must be positive")
    return timedelta(minutes=minutes)


def create_access_token(user: CurrentUser) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": str(user.email),
        "role": user.role.value,
        "iat": now,
        "exp": now + _token_lifetime(),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _validated_token_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> CurrentUser:
    """Reject invalid credentials before acquiring a database connection."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _credentials_error()

    try:
        payload = jwt.decode(
            credentials.credentials,
            _jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat"]},
        )
        token = TokenPayload.model_validate(payload)
    except (jwt.InvalidTokenError, ValidationError, ValueError):
        raise _credentials_error() from None

    return CurrentUser(id=token.sub, email=token.email, role=token.role)


async def get_current_user(
    token_user: Annotated[CurrentUser, Depends(_validated_token_user)],
    connection: DbConnection,
) -> CurrentUser:
    """Require an existing account whose identity still matches the token.

    Mutation requests share the route's transaction/advisory lock, so a role
    change committed by the previous writer is checked before any new write.
    """
    record = await connection.fetchrow(
        "SELECT id, email, role FROM users WHERE id = $1", token_user.id
    )
    if record is None:
        raise _credentials_error()
    current = CurrentUser.model_validate(dict(record))
    if current.role != token_user.role or current.email != token_user.email:
        raise _credentials_error()
    return current


def require_roles(roles: Sequence[UserRole | str]):
    """Build a FastAPI dependency that accepts only users with one of the given roles."""
    allowed_roles = frozenset(UserRole(role) for role in roles)

    def role_dependency(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return role_dependency
