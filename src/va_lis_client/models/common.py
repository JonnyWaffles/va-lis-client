"""Common/shared models — heartbeat and partner authentication."""

from datetime import datetime

from pydantic import BaseModel


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
