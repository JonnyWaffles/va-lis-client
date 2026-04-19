"""Legislative session models."""

from datetime import datetime

from pydantic import BaseModel


class SessionEvent(BaseModel):
    """A milestone within a legislative session (start, adjourn, reconvene, etc.).

    Example::

        {"SessionEventID": 302, "DisplayName": "Session Start",
         "ActualDate": "2026-01-14T00:00:00", "EventTypeID": 1}
    """

    SessionEventID: int
    DisplayName: str  # e.g. "Session Start", "Adjournment", "Reconvene"
    ActualDate: datetime | None = None
    InternalOnly: bool = False
    EventTypeID: int  # 1=Start, 2=Prefile Date, 9=Reconvene, 11=Adjournment
    ProjectedDate: datetime | None = None


class Session(BaseModel):
    """A legislative session from ``/Session/api/getsessionlistasync``.

    Key: ``SessionCode`` or ``SessionID``.

    ``SessionCode`` encodes year + sequence: ``"20261"`` = 2026 Regular,
    ``"20262"`` = 2026 Special Session I.  Most API endpoints accept
    either ``sessionCode`` or ``sessionID`` as a query parameter.

    Example::

        {"SessionID": 59, "SessionCode": "20261",
         "DisplayName": "Regular Session", "SessionYear": 2026,
         "SessionType": "Regular", "IsDefault": true, "IsActive": true,
         "SessionEvents": [...]}
    """

    SessionID: int  # surrogate PK (e.g. 59)
    SessionCode: str  # "{year}{seq}" e.g. "20261", "20262"
    DisplayName: str  # e.g. "Regular Session", "Special Session I"
    SessionYear: int  # e.g. 2026
    SessionTypeID: int  # 1=Regular, 2=Special
    SessionType: str  # "Regular" or "Special"
    IsDefault: bool  # True for the "current" session
    LegacySessionID: int | None = None
    IsActive: bool
    SessionEvents: list[SessionEvent] = []
