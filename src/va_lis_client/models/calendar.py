"""Calendar models (House floor agendas + both chambers)."""

from datetime import datetime

from pydantic import BaseModel, Field

from va_lis_client.models.legislation import Patron
from va_lis_client.models.vote import VoteMember

__all__ = [
    "Agenda",
    "AgendaItem",
    "CalendarCategory",
    "CalendarComment",
    "CalendarDetail",
    "CalendarFile",
    "CalendarItem",
    "Staff",
    "VoteMember",
]


class CalendarFile(BaseModel):
    """A file attachment on a calendar or docket (PDF, JSON).

    Example::

        {"CalendarFileID": 1211452, "CalendarID": 21146,
         "FileURL": "https://lis.blob.core.windows.net/files/1211452.PDF",
         "TextFormatID": 1, "IsPublic": true, "IsActive": true}
    """

    CalendarFileID: int
    CalendarID: int
    FileURL: str | None = None
    TextFormatID: int | None = None  # 1=PDF, 5=JSON
    IsGenerated: bool = False
    IsPublic: bool = True
    IsActive: bool = True
    ModificationDate: datetime | None = None


class CalendarComment(BaseModel):
    """A comment/note on a calendar entry.

    Example::

        {"CalendarCommentID": 123, "CalendarID": 21146,
         "Comment": "Session will reconvene at 4pm", "Sequence": 1}
    """

    CalendarCommentID: int
    CalendarID: int
    Comment: str | None = None
    Sequence: int | None = None
    DeletionDate: datetime | None = None


class CalendarItem(BaseModel):
    """A calendar list entry from ``/Calendar/api/getcalendarlistasync``.

    These are floor calendars (House and Senate).  For full detail with
    agendas and votes, use ``getcalendarsbyidasync``.

    Key: ``CalendarID``.  ``ReferenceNumber`` is a human-readable code
    like ``"HC10314"`` (House Calendar 1, March 14).

    Example::

        {"CalendarID": 21146, "ReferenceNumber": "HC10314",
         "CalendarDate": "2026-03-14T10:00:00",
         "CalendarType": "Chamber", "ChamberCode": "H",
         "SessionCode": "20261",
         "CalendarFiles": [{"FileURL": "...1211452.PDF", ...}]}
    """

    CalendarID: int  # surrogate PK
    ReferenceNumber: str | None = None  # e.g. "HC10314"
    SessionCode: str | None = None
    CalendarDate: datetime | None = None
    MeetingTime: str | None = None
    CalendarNumber: int | None = None
    Description: str | None = None
    IsPublic: bool = True
    CalendarType: str | None = None  # "Chamber", "Committee"
    CalendarTypeID: int | None = None
    ChamberCode: str | None = None  # "H" or "S"
    SessionID: int | None = None
    VoteRoomID: int | None = None
    RoomDescription: str | None = None
    Comments: str | None = None
    PendingChange: bool = False
    IsProforma: bool = False
    DeletionDate: datetime | None = None
    CalendarFiles: list[CalendarFile] = []
    CalendarComments: list[CalendarComment] = []


class AgendaItem(BaseModel):
    """A sub-item on an agenda entry (action taken, vote result, etc.).

    Example::

        {"AgendaItemId": 66041, "AgendaId": 298539,
         "Description": "<p>Reported from Committee... March 13, 2026</p>",
         "VoteID": 302080,
         "VoteMember": [{"ResponseCode": "Y", ...}]}
    """

    AgendaItemId: int | None = None
    AgendaId: int | None = None
    Description: str | None = None  # HTML
    CalendarDescription: str | None = None
    LDTitle: str | None = None
    LegislationTextId: int | None = None
    LDNumber: str | None = None
    DraftText: str | None = None
    VoteID: int | None = None
    LegislationEventID: int | None = None
    IsActive: bool = True
    Sequence: int | None = None
    vote_members: list[VoteMember] = Field(default=[], alias="VoteMember")


class Agenda(BaseModel):
    """An agenda entry on a calendar — typically one bill or action.

    Nested inside ``CalendarCategory.Agendas`` in calendar detail.

    Example::

        {"AgendaID": 298539, "LegislationID": 101934,
         "LegislationNumber": "SJ209",
         "LegislationDescription": "Governor; confirming appointments.",
         "Patrons": [...], "AgendaItems": [{...vote tallies...}]}
    """

    AgendaID: int | None = None
    Sequence: int | None = None
    Ranking: int | None = None
    CalendarCategoryID: int | None = None
    Description: str | None = None
    LegislationID: int | None = None
    LegislationKey: int | None = None
    LegislationNumber: str | None = None
    LegislationDescription: str | None = None
    LegislationTitle: str | None = None
    LDNumber: str | None = None
    Summary: str | None = None
    DraftTitle: str | None = None
    IsActive: bool = True
    IsHidden: bool | None = None
    CalendarCategoryTemplateID: int | None = None
    CommunicationID: int | None = None
    PageNumber: int | None = None
    EffectiveType: str | None = None
    CommitteeID: int | None = None
    CandidateDate: datetime | None = None
    DisplayType: bool | None = None
    Patrons: list[Patron] = []
    AgendaItems: list[AgendaItem] = []


class CalendarCategory(BaseModel):
    """A section within a calendar (e.g. "Order of Business", "Senate Bills").

    Example::

        {"CalendarCategoryID": 11445, "CategoryCode": "Order",
         "Description": "Order of Business",
         "IsLegislationCategory": false,
         "Agendas": [{"Description": "Call to Order"}, ...]}
    """

    CalendarCategoryID: int | None = None
    CalendarID: int | None = None
    CalendarCategoryTypeID: int | None = None
    CategoryCode: str | None = None  # e.g. "Order", "RCRES", "CSGEN"
    CategoryType: str | None = None  # e.g. "Order", "Regular Calendar"
    Description: str | None = None  # e.g. "Senate Bill in Committee"
    PluralDescription: str | None = None
    Sequence: int | None = None
    DisplayType: bool = False
    IsLegislationCategory: bool = False  # True if this section contains bills
    IsPrint: bool = False
    Agendas: list[Agenda] = []


class Staff(BaseModel):
    """A staff member assigned to a committee or calendar.

    Example::

        {"StaffID": 1964, "AffiliationID": 23,
         "FullName": "Lehman, Hobie", "Sequence": 1,
         "IsPublic": true}
    """

    StaffID: int
    AffiliationID: int | None = None  # CommitteeID
    StaffRoleTypeID: int | None = None
    IdentityID: int | None = None
    FullName: str | None = None
    Sequence: int | None = None
    IsPublic: bool = True
    EffectiveDate: datetime | None = None
    ModificationDate: datetime | None = None


class CalendarDetail(BaseModel):
    """Full calendar detail from ``/Calendar/api/getcalendarsbyidasync``.

    Includes the complete agenda with categories, legislation, votes, staff.
    Used for House floor calendars and both chambers' session calendars.

    Key: ``CalendarID``.

    Example (truncated)::

        {"CalendarID": 21146, "ReferenceNumber": "HC10314",
         "CalendarDate": "2026-03-14T10:00:00",
         "CalendarType": "Chamber", "ChamberCode": "H",
         "CalendarCategories": [
           {"CategoryCode": "Order", "Agendas": [...]},
           {"CategoryCode": "RCRES", "Agendas": [{bills with votes}]}
         ]}
    """

    CalendarID: int
    ReferenceNumber: str | None = None
    SessionCode: str | None = None
    CalendarDate: datetime | None = None
    MeetingTime: str | None = None
    CalendarNumber: int | None = None
    Description: str | None = None
    IsPublic: bool = True
    CalendarType: str | None = None
    CalendarTypeID: int | None = None
    ChamberCode: str | None = None
    SessionID: int | None = None
    VoteRoomID: int | None = None
    RoomDescription: str | None = None
    Comments: str | None = None
    PendingChange: bool = False
    IsProforma: bool = False
    DeletionDate: datetime | None = None
    staff: list[Staff] = Field(default=[], alias="Staff")
    CalendarCategories: list[CalendarCategory] = []
    CalendarComments: list[CalendarComment] = []
    CalendarFiles: list[CalendarFile] = []
