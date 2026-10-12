"""Legislative session models."""

from datetime import datetime

from va_lis_client.models.common import LISModel


class SessionEvent(LISModel):
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


class Session(LISModel):
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


class ShallowSession(LISModel):
    """A session without its events, from ``/Session/api/getsessionshallowlistasync``.

    Key: ``SessionCode`` or ``SessionID``.

    The cheap way to list sessions: 59 rows and 9 KB for every session back
    to 1994 (measured 2026-10-12).  It drops ``SessionTypeID``,
    ``LegacySessionID`` and the ``SessionEvents`` list that
    :class:`Session` carries, so read ``SessionType`` for the kind.

    Example::

        {"SessionID": 59, "SessionCode": "20261",
         "DisplayName": "Regular Session", "SessionYear": 2026,
         "SessionType": "Regular", "IsDefault": false, "IsActive": true}
    """

    SessionID: int
    SessionCode: str
    DisplayName: str | None = None
    SessionYear: int | None = None
    SessionType: str | None = None  # "Regular" or "Special"
    IsDefault: bool = False
    IsActive: bool = True  # true for three sessions at once in October 2026


class SessionEventType(LISModel):
    """A kind of session milestone, from ``getsessioneventtypereferencesasync``.

    Key: ``EventTypeID``, which is ``SessionEvent.EventTypeID``.

    11 rows.  **Adjournment is 8 and Closing is 11**, and LIS files a regular
    session's sine die under 11 with the display name ``"Adjournment"``:
    20261 carries ``{"DisplayName": "Adjournment", "EventTypeID": 11}``.  Join
    on the id and expect the label to disagree.

    Example::

        {"EventTypeID": 5, "Name": "Crossover"}
    """

    EventTypeID: int
    Name: str | None = None


class SessionType(LISModel):
    """A session kind, from ``/Session/api/getsessiontypereferencesasync``.

    Two rows: 1 ``Regular`` and 2 ``Special``.

    Example::

        {"SessionTypeID": 2, "Name": "Special"}
    """

    SessionTypeID: int
    Name: str | None = None
