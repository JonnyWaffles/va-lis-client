"""Common/shared models — heartbeat, partner authentication, and the base model."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator


class LISModel(BaseModel):
    """A model that strips the stray whitespace LIS puts on string values.

    LIS ships padded strings across unrelated fields, and which fields carry
    the padding moves between sessions.  On the member roster alone:

    ==================  =====  =====  =====  =====
    Field               20241  20251  20261  20271
    ==================  =====  =====  =====  =====
    GABEmailAddress        57     57     50     45
    ListDisplayName         1      1      4      4
    MemberDisplayName       0      0      3      3
    RoomNumber              1      0      0      0
    ==================  =====  =====  =====  =====

    The padding carries no meaning, and chasing it with a per-field accessor
    loses to the next field LIS pads.  Every string on a subclass is stripped
    at validation instead, so ``MemberNumber == "H0386"`` and
    ``ResponseCode == "Y"`` hold whatever LIS sends.

    A string of only whitespace becomes ``""``, not ``None``.
    """

    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()

        if isinstance(value, list):
            return [v.strip() if isinstance(v, str) else v for v in value]

        return value


class Heartbeat(BaseModel):
    """Health-check response from any ``/{Service}/api/heartbeatasync``.

    Does NOT validate the API key — returns ``Success=True`` for any value.

    Example::

        {"User": "Guest", "DateTime": "3/31/2026 7:29:35 PM",
         "Environment": "Production", "ActionName": "Heartbeat",
         "Service": "Legislation", "Success": true, "FailureMessage": null}
    """

    User: str  # always "Guest" for API-key auth
    DateTime: str  # server-local timestamp string (not ISO 8601)
    Environment: str  # e.g. "Production"
    ActionName: str  # always "Heartbeat"
    Service: str  # e.g. "Legislation", "Session", "Authentication"
    Success: bool
    FailureMessage: str | None = None


class Partner(BaseModel):
    """Partner record from ``/PartnerAuthentication/api/checkpartnerkeyasync/{apiKey}``.

    This is the only endpoint that actually validates whether an API key is
    registered and active.  Returns 204 (no content) if the key is unknown.

    Example::

        {"PartnerID": 123, "IdentityID": 4567,
         "OrganizationName": "Example Agency",
         "ContactName": "Jane Smith", "APIKey": "XXXXXXXX-...",
         "IsActive": true, "IsPublic": false, ...}
    """

    PartnerID: int  # surrogate PK
    IdentityID: int  # linked identity in LIS auth system
    OrganizationName: str
    ContactName: str
    PhoneNumber: str | None = None
    EmailAddress: str | None = None
    APIKey: str  # the GUID key itself
    URL: str | None = None
    IsActive: bool  # False = key is revoked/disabled
    IsPublic: bool  # whether the partner is listed publicly
    ModificationDate: datetime | None = None
