"""Committee models."""

from datetime import datetime

from pydantic import BaseModel


class CommitteeFile(BaseModel):
    """A file attachment on a committee (e.g. roster PDF).

    Example::

        {"CommitteeFileID": 123, "CommitteeID": 14,
         "FileURL": "https://lis.blob.core.windows.net/files/123.PDF",
         "TextFormatID": 1, "IsPublic": true, "IsActive": true}
    """

    CommitteeFileID: int
    CommitteeID: int
    FileURL: str | None = None
    TextFormatID: int | None = None
    IsGenerated: bool = False
    IsPublic: bool = True
    IsActive: bool = True
    Description: str | None = None


class Committee(BaseModel):
    """A legislative committee from ``/Committee/api/getcommitteelistasync``.

    Key: ``CommitteeID`` (surrogate PK) or ``CommitteeNumber`` (e.g. ``"H14"``).

    ``CommitteeNumber`` is chamber-prefixed: ``"H01"`` through ``"H24"`` for
    House, ``"S01"`` through ``"S13"`` for Senate.  Subcommittees use a
    parent-relative suffix: ``"H10001"`` = Appropriations subcommittee 1.

    Example::

        {"CommitteeID": 14, "Name": "Labor and Commerce",
         "CommitteeNumber": "H14", "ChamberCode": "H",
         "Abbreviation": "HLC     ",
         "ServiceBeginDate": "1993-11-15T00:00:00"}
    """

    CommitteeID: int  # surrogate PK
    Name: str  # e.g. "Labor and Commerce"
    CommitteeNumber: str  # e.g. "H14", "S02", "H10001" (subcommittee)
    ChamberCode: str  # "H" or "S"
    SessionCode: str | None = None
    TwitterHandle: str | None = None
    TwitterEmail: str | None = None
    ParentCommitteeID: int | None = None  # set for subcommittees
    ServiceBeginDate: datetime | None = None
    ServiceEndDate: datetime | None = None
    EffectiveBeginDate: datetime | None = None
    EffectiveEndDate: datetime | None = None
    Abbreviation: str | None = None  # e.g. "HLC", "HAPP", "SFIN"
    Description: str | None = None
    MeetingNote: str | None = None
    PendingChange: bool = False
    SubPendingChange: bool = False
    IsPublic: bool = True
    AgendaURL: str | None = None
    CommitteeFiles: list[CommitteeFile] = []


class CommitteeMember(BaseModel):
    """A member's role on a committee, nested in docket/calendar detail.

    Example::

        {"CommitteeMemberID": 33672, "CommitteeID": 23,
         "CommitteeNumber": "S02", "MemberID": 114,
         "MemberNumber": "S0062",
         "MemberDisplayName": "R. Creigh Deeds",
         "Title": "Chair", "PartyCode": "D"}
    """

    CommitteeMemberID: int
    CommitteeID: int
    CommitteeNumber: str | None = None  # e.g. "S02"
    MemberID: int
    MemberNumber: str | None = None  # e.g. "S0062"
    MemberDisplayName: str | None = None
    PatronDisplayName: str | None = None
    PartyCode: str | None = None  # "D", "R"
    VotingSequence: int | None = None
    DisplaySequence: int | None = None
    CommitteeRoleID: int | None = None  # 1=Chair, etc.
    Title: str | None = None  # "Chair", etc.
    IsPublic: bool = True
    EffectiveDate: datetime | None = None


class CommitteeAction(BaseModel):
    """Committee action reference from
    ``/CommitteeLegislationReferral/api/getcommitteeactionreferencesasync``.

    The actions a committee can take on referred legislation (e.g. Reported,
    Passed by indefinitely, Referred to subcommittee).

    Example::

        {"CommitteeActionID": 1, "Description": "Reported",
         "EventCode": "H0205", "IsComplete": true}
    """

    CommitteeActionID: int
    Description: str | None = None
    EventCode: str | None = None
    IsComplete: bool = False
