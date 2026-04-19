"""Legislation (bill) models — patrons, statuses, versions, and bill detail."""

from datetime import datetime

from pydantic import BaseModel


class Patron(BaseModel):
    """A bill patron (sponsor/co-sponsor).

    ``PatronTypeID`` determines the role:
    - 1 = Chief Patron (primary sponsor)
    - 2 = Chief Co-Patron
    - 4 = Co-Patron

    ``MemberNumber`` is the LIS member code: ``"H0173"`` (House member 173),
    ``"S0121"`` (Senate member 121).

    Example::

        {"LegislationID": 98525, "ChamberCode": "H", "MemberID": 419,
         "MemberNumber": "H0173", "PatronTypeID": 1,
         "Name": "Chief Patron", "MemberDisplayName": "Jeion A. Ward",
         "PatronDisplayName": "Ward"}
    """

    LegislationID: int
    ChamberCode: str  # "H" = House, "S" = Senate
    MemberID: int  # surrogate PK for the member
    MemberNumber: str  # e.g. "H0173", "S0121"
    PatronTypeID: int  # 1=Chief Patron, 2=Chief Co-Patron, 4=Co-Patron
    Name: str  # role name, e.g. "Chief Patron", "Co-Patron"
    DisplayName: str | None = None  # e.g. "(Chief Patron)"
    MemberDisplayName: str | None = None  # full name, e.g. "Jeion A. Ward"
    PatronDisplayName: str | None = None  # last name, e.g. "Ward"
    LegislationNumber: str | None = None
    Sequence: int | None = None  # display order
    IsIntroducing: bool | None = None
    ByRequest: bool | None = None  # True if introduced "by request"
    LegislationTextID: int | None = None


class LegislationSession(BaseModel):
    """Session cross-reference on a bill (which sessions it appeared in).

    Example::

        {"SessionID": 59, "SessionCode": "20261", "IsPrefile": true}
    """

    SessionID: int
    SessionCode: str
    IsPrefile: bool = False


class LegislationSummaryItem(BaseModel):
    """Lightweight bill record from the per-session list endpoint.

    Returned by ``/Legislation/api/getlegislationsessionlistasync``.
    Use ``session_code`` (e.g. ``20261``) to query.

    Key: ``LegislationID`` is globally unique; ``LegislationNumber`` is
    unique within a session (e.g. "HB1" in 20261).

    ``LegislationTypeCode``: ``"B"`` = Bill, ``"J"`` = Joint Resolution,
    ``"R"`` = Resolution.

    Example::

        {"LegislationID": 98525, "LegislationNumber": "HB1",
         "Description": "Minimum wage; increases incrementally...",
         "ChamberCode": "H", "LegislationTypeCode": "B",
         "LegislationStatus": "Awaiting Governor's Action",
         "Patrons": [{"MemberDisplayName": "Jeion A. Ward", ...}]}
    """

    LegislationID: int  # globally unique surrogate PK
    LegislationNumber: str  # e.g. "HB1", "SB234" (unpadded)
    Description: str  # short description
    LegislationTitle: str | None = None  # full formal title (often null here)
    ChamberCode: str  # "H" or "S"
    LegislationTypeCode: str  # "B"=Bill, "J"=Joint Resolution, "R"=Resolution
    LegislationKey: int | None = None  # numeric part of bill number (1 for HB1)
    LegislationStatus: str | None = None  # display name, e.g. "In Committee"
    Patrons: list[Patron] = []


class Legislation(BaseModel):
    """Full bill detail from ``/Legislation/api/getlegislationbyidasync/{id}``.

    Query by ``LegislationID`` (the surrogate PK).  Returns one record.

    ``FullNumber`` is the zero-padded form (``"HB0001"``), while
    ``LegislationNumber`` is unpadded (``"HB1"``).

    Example::

        {"LegislationID": 98525, "LegislationNumber": "HB1",
         "FullNumber": "HB0001",
         "Description": "Minimum wage; increases incrementally...",
         "LegislationTitle": "An Act to amend and reenact ...",
         "IntroductionDate": "2025-11-17T07:28:00",
         "ChamberCode": "H", "LegislationTypeCode": "B",
         "LegislationStatusID": 38, "LegislationStatus": null,
         "LegislationClass": "Legislation",
         "Patrons": [{...}, ...],  // 65 patrons for HB1
         "Sessions": [{"SessionCode": "20261", ...}]}
    """

    LegislationID: int  # globally unique surrogate PK
    LegislationNumber: str  # e.g. "HB1" (unpadded)
    Description: str  # short description
    LegislationTitle: str | None = None  # full "An Act to ..." title
    OfferedDate: datetime | None = None
    IntroductionDate: datetime | None = None
    ChamberCode: str  # "H" or "S"
    LegislationTypeCode: str  # "B", "J", "R"
    FullNumber: str | None = None  # zero-padded, e.g. "HB0001"
    LegislationStatusID: int | None = None  # FK to LegislationStatus reference
    LegislationKey: int | None = None  # numeric part (1 for HB1)
    LegislationStatus: str | None = None  # may be null in detail response
    LegislationClassID: int | None = None  # 1=Legislation
    LegislationClass: str | None = None  # "Legislation"
    EffectiveType: str | None = None  # "Standard" etc.
    EffectiveTypeID: int | None = None
    PendingChange: bool = False
    SessionID: int | None = None
    SessionCode: str | None = None
    # NOTE: CommitteeName, CommitteeID, ParentCommitteeName, and
    # CommitteeNumber are ALWAYS NULL from the LIS API.  Committee data
    # must be derived from LegislationEvent records instead, where each
    # event carries CommitteeName and ActorID for committee-related actions.
    CommitteeName: str | None = None  # ALWAYS NULL — use LegislationEvent.CommitteeName
    CommitteeID: int | None = None  # ALWAYS NULL — use LegislationEvent.ActorID
    ParentCommitteeName: str | None = None  # ALWAYS NULL
    CommitteeNumber: str | None = None  # ALWAYS NULL — was expected to be "H14"/"S02" format
    # NOTE: HousePassageDate and SenatePassageDate are ALWAYS NULL from the
    # LIS API.  Passage dates must be derived from LegislationEvent records
    # by matching events with Status in {"Passed House"} or {"Passed Senate"}.
    HousePassageDate: str | None = None  # ALWAYS NULL — derive from events
    SenatePassageDate: str | None = None  # ALWAYS NULL — derive from events
    IsComplete: bool = False
    Patrons: list[Patron] = []
    Sessions: list[LegislationSession] = []


class LegislationStatus(BaseModel):
    """Reference entry from ``/Legislation/api/getlegislationstatuslistasync``.

    52 statuses in total.  ``LegislationVersionID`` is set on statuses that
    correspond to a specific bill version (e.g. Enrolled -> version 3).

    Examples::

        {"LegislationStatusID": 1, "Name": "Introduced", "DisplayName": "Introduced"}
        {"LegislationStatusID": 8, "Name": "Approved", "DisplayName": "Approved",
         "LegislationVersionID": 4}
        {"LegislationStatusID": 38, "Name": "Awaiting Governor's Action", ...}
    """

    LegislationStatusID: int  # PK, referenced by Legislation.LegislationStatusID
    Name: str  # internal name
    DisplayName: str  # user-facing label
    LegislationVersionID: int | None = None  # linked version type, if any


class LegislationVersion(BaseModel):
    """Reference entry from ``/LegislationText/api/getlegislationversionlistasync``.

    13 version types.  ``Suffix`` is the code appended to the bill number
    to form the ``DocumentCode`` (e.g. HB1 + ``"ER"`` -> ``"HB1ER"``).
    Introduced has no suffix (the DocumentCode equals the bill number).

    Examples::

        {"LegislationVersionID": 1, "Name": "Introduced", "VersionCode": "INT",
         "Suffix": null}
        {"LegislationVersionID": 3, "Name": "Enrolled", "VersionCode": "ENR",
         "Suffix": "ER"}
    """

    LegislationVersionID: int  # PK, referenced by LegislationTextItem/Detail
    Name: str  # e.g. "Introduced", "Enrolled", "Engrossed"
    VersionCode: str  # e.g. "INT", "ENR", "ENG", "SUB"
    LegislationStatusID: int | None = None  # always null in observed data
    Suffix: str | None = None  # appended to bill number -> DocumentCode
    AuthoringLabel: str | None = None  # e.g. "Enrollment Ready"
