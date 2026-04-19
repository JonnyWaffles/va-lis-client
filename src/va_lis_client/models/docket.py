"""Docket models (Senate-only committee agendas)."""

from datetime import datetime

from pydantic import BaseModel, Field

from va_lis_client.models.calendar import CalendarFile, Staff
from va_lis_client.models.committee import CommitteeMember
from va_lis_client.models.legislation import Patron
from va_lis_client.models.schedule import Schedule


class DocketItem(BaseModel):
    """A bill on a Senate committee docket.

    Includes the bill summary, patrons, and draft title — everything a
    committee member needs to prepare for the hearing.

    Example::

        {"DocketItemID": 286452, "LegislationID": 98706,
         "LegislationNumber": "HB69",
         "LegislationDescription": "Retail franchise agreements...",
         "Summary": "<p>...</p>",
         "Patrons": [{"MemberDisplayName": "Dan I. Helmer", ...}]}
    """

    DocketItemID: int | None = None
    Sequence: int | None = None
    DocketID: int | None = None
    DocketCategoryID: int | None = None
    CategoryType: str | None = None
    CategoryCode: str | None = None
    Description: str | None = None
    LegislationID: int | None = None  # FK to Legislation
    LegislationNumber: str | None = None  # e.g. "HB69"
    LegislationKey: int | None = None
    LegislationDescription: str | None = None
    LDNumber: str | None = None
    Summary: str | None = None  # HTML bill summary
    DraftTitle: str | None = None  # HTML "A BILL to ..."
    IsActive: bool = True
    CalendarCategoryTemplateID: int | None = None
    CommunicationID: int | None = None
    CommitteeID: int | None = None
    CandidateDate: datetime | None = None
    Patrons: list[Patron] = []


class DocketCategory(BaseModel):
    """A section within a docket (e.g. "Senate Bills in Committee").

    Example::

        {"CalendarCategoryID": 10985, "CategoryCode": "CSGEN",
         "Description": "Senate Bill in Committee",
         "IsLegislationCategory": true,
         "DocketItems": [{bills...}]}
    """

    CalendarCategoryID: int | None = None
    CalendarCategoryTypeID: int | None = None
    CategoryCode: str | None = None  # e.g. "CSGEN"
    CategoryType: str | None = None  # e.g. "General"
    Description: str | None = None  # e.g. "Senate Bill in Committee"
    PluralDescription: str | None = None
    Sequence: int | None = None
    DisplayType: bool = False
    IsLegislationCategory: bool = False
    IsPrint: bool = False
    DocketItems: list[DocketItem] = []


class DocketListItem(BaseModel):
    """A docket list entry from ``/Calendar/api/getdocketlistasync``.

    **Senate only** — House returns 400 with "Dockets can only be for
    ChamberCode = S".

    Key: ``DocketID``.  Query by ``committeeID`` or ``committeeNumber``
    + ``chamberCode`` + ``sessionCode``.

    Example::

        {"DocketID": 21114, "DocketDate": "2026-03-09T16:30:00",
         "DocketType": "Committee", "CommitteeID": 23,
         "CommitteeName": "Commerce and Labor", "ChamberCode": "S",
         "DocketFiles": [{"FileURL": "...1192418.PDF", ...}]}
    """

    DocketID: int  # surrogate PK
    DocketDate: datetime | None = None
    MeetingTime: str | None = None
    DocketNumber: int | None = None
    Description: str | None = None
    IsPublic: bool = True
    DocketTypeID: int | None = None
    DocketType: str | None = None  # "Committee"
    ChamberCode: str | None = None  # always "S"
    CommitteeID: int | None = None  # FK to Committee
    CommitteeName: str | None = None
    ParentCommitteeName: str | None = None
    SessionID: int | None = None
    SessionCode: str | None = None
    VoteRoomID: int | None = None
    Comments: str | None = None
    PendingChange: bool = False
    ReferenceNumber: str | None = None
    IsProforma: bool = False
    DeletionDate: datetime | None = None
    DocketFiles: list[CalendarFile] = []


class CalendarDisplay(BaseModel):
    """Display column configuration for a docket."""

    DisplayColumn: str | None = None
    IsDisplayed: bool = False


class DocketDetail(BaseModel):
    """Full docket detail from ``/Calendar/api/getdocketsbyidasync``.

    **Senate only.**  Includes the complete agenda with bills, committee
    members, staff, and linked schedules (when/where).

    The ``Schedules`` array links back to the Schedule service for the
    actual meeting time and room.  Chain: Docket -> Schedule -> Room.

    Key: ``DocketID``.

    Example (truncated)::

        {"DocketID": 21114, "DocketDate": "2026-03-09T16:30:00",
         "CommitteeName": "Commerce and Labor",
         "Schedules": [{"RoomDescription": "Senate Room A, ...",
                        "ScheduleTime": "15 min after adjournment"}],
         "CommitteeMember": [{"Title": "Chair", ...}],
         "DocketCategories": [{"DocketItems": [{bills...}]}]}
    """

    DocketID: int  # surrogate PK
    DocketDate: datetime | None = None
    MeetingTime: str | None = None
    DocketNumber: int | None = None
    Description: str | None = None
    IsPublic: bool = True
    DocketTypeID: int | None = None
    DocketType: str | None = None
    ChamberCode: str | None = None
    CommitteeID: int | None = None
    CommitteeName: str | None = None
    ParentCommitteeName: str | None = None
    SessionID: int | None = None
    SessionCode: str | None = None
    VoteRoomID: int | None = None
    Comments: str | None = None
    PendingChange: bool = False
    ReferenceNumber: str | None = None
    IsProforma: bool = False
    DeletionDate: datetime | None = None
    calendar_display: list[CalendarDisplay] = Field(default=[], alias="CalendarDisplay")
    committee_members: list[CommitteeMember] = Field(default=[], alias="CommitteeMember")
    staff: list[Staff] = Field(default=[], alias="Staff")
    DocketCategories: list[DocketCategory] = []
    DocketFiles: list[CalendarFile] = []
    Schedules: list[Schedule] = []
