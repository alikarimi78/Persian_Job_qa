import re

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from src.validators import blank_to_none

# The logo travels as a base64 data URI, so the bound is on the encoded string; the
# decoded byte limit that actually matters is `service.MAX_LOGO_BYTES`.
MAX_LOGO_URI = 800_000

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

_DIGIT_MAP = {ord(c): str(i % 10) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩")}


class OrganizationProfile(BaseModel):
    code: str | None = Field(default=None, max_length=64)
    address: str | None = Field(default=None, max_length=512)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    logo: str | None = Field(default=None, max_length=MAX_LOGO_URI)

    @field_validator("code", "address")
    @classmethod
    def _clean(cls, value):
        return blank_to_none(value)

    @field_validator("phone")
    @classmethod
    def _clean_phone(cls, value):
        value = blank_to_none(value)
        if value is None:
            return None
        value = value.translate(_DIGIT_MAP)
        if not re.fullmatch(r"[0-9+\-() ]{7,32}", value) or sum(c.isdigit() for c in value) < 7:
            raise ValueError("Phone must be 7-20 digits, optionally with + - ( ) or spaces")
        return value

    @field_validator("email")
    @classmethod
    def _clean_email(cls, value):
        value = blank_to_none(value)
        if value is None:
            return None
        value = value.lower()
        if not _EMAIL_RE.match(value):
            raise ValueError("Not a valid email address")
        return value


class OrganizationIn(OrganizationProfile):
    name: str = Field(min_length=2, max_length=128)


class OrganizationUpdateIn(OrganizationProfile):
    name: str | None = Field(default=None, min_length=2, max_length=128)


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    code: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    logo_mime: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def has_logo(self) -> bool:
        return self.logo_mime is not None


class OrganizationLogoOut(BaseModel):
    logo: str | None = None
