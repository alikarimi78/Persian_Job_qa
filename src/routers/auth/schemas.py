from pydantic import BaseModel, Field, field_validator

from src.validators import validate_password_strength
from src.routers.accounts.schemas import UserOut
from src.routers.orgs.schemas import OrganizationOut


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class SelfPasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        return validate_password_strength(value)


class MeOut(UserOut):
    organization: OrganizationOut | None = None
