"""Auth-related DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class GoogleLoginRequest(BaseModel):
    """Payload posted by the Android client after a successful Google Sign-In."""

    id_token: str = Field(..., description="Google ID token from the Android Credential Manager.")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    picture_url: str | None
    role: str
    native_languages: list[str]
    preferred_analogy_domains: list[str]
    created_at: datetime
    last_login_at: datetime | None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Lifetime of the access token in seconds.")
    user: UserOut
