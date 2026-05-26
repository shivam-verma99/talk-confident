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
    pronouns: str | None
    native_languages: list[str]
    preferred_analogy_domains: list[str]
    created_at: datetime
    last_login_at: datetime | None


class UserUpdateRequest(BaseModel):
    """Patch payload for ``PATCH /auth/me`` — only mutable user-facing fields.

    All fields are optional; omitted fields are left unchanged. ``pronouns`` may
    be set to ``null`` (explicit JSON ``null``) to clear a previously chosen value.
    """

    pronouns: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Free-form pronoun phrase ('she/her', 'he/him', 'they/them', or "
            "custom). Pass an empty string or null to clear."
        ),
    )


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Lifetime of the access token in seconds.")
    user: UserOut
