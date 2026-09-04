from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from src.models import join_name
from src.validators import validate_password_strength


class NameIn(BaseModel):
    first_name: str = Field(min_length=1, max_length=64)
    last_name: str = Field(min_length=1, max_length=64)

    @field_validator("first_name", "last_name")
    @classmethod
    def _clean(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank")
        return value


class AccountIn(NameIn):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        return validate_password_strength(value)


class OrgAdminIn(AccountIn):
    organization_id: int


class UserAccountIn(AccountIn):
    organization_id: int | None = None


class MoveOrganizationIn(BaseModel):
    organization_id: int


class PasswordResetIn(BaseModel):
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        return validate_password_strength(value)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    first_name: str | None = None
    last_name: str | None = None
    role: str
    is_active: bool = True
    organization_id: int | None = None

    @computed_field
    @property
    def full_name(self) -> str | None:
        return join_name(self.first_name, self.last_name)
