"""
Pydantic models for LIS API responses.

Built from live API responses — the OpenAPI specs are unreliable
(e.g. envelope keys differ from reality).

Modules:
    common       — Heartbeat, Partner
    session      — SessionEvent, Session
    legislation  — Patron, LegislationSession, LegislationSummaryItem,
                   Legislation, LegislationStatus, LegislationVersion
    text         — TextFile, LegislationTextItem, LegislationTextDetail,
                   LegislationSummary
    committee    — CommitteeFile, Committee, CommitteeMember, CommitteeAction
    schedule     — Schedule, ScheduleType, MeetingRoom
    calendar     — CalendarFile, CalendarComment, CalendarItem,
                   AgendaItem, Agenda, CalendarCategory, Staff, CalendarDetail
    vote         — VoteMember, VoteLegislation, VoteStatement, VoteFile,
                   VoteType, Vote
    docket       — DocketItem, DocketCategory, DocketListItem, CalendarDisplay,
                   DocketDetail
    event        — EventReference, LegislationEvent, LegislationEventType,
                   ActorType
    member       — Member, Party, District
    pagination   — Pagination, PagedList, page_request_header
"""

from va_lis_client.models.calendar import (
    Agenda,
    AgendaItem,
    CalendarCategory,
    CalendarComment,
    CalendarDetail,
    CalendarFile,
    CalendarItem,
    Staff,
)
from va_lis_client.models.committee import (
    Committee,
    CommitteeAction,
    CommitteeFile,
    CommitteeMember,
)
from va_lis_client.models.common import Heartbeat, Partner
from va_lis_client.models.docket import (
    CalendarDisplay,
    DocketCategory,
    DocketDetail,
    DocketItem,
    DocketListItem,
)
from va_lis_client.models.event import (
    ActorType,
    EventReference,
    LegislationEvent,
    LegislationEventType,
)
from va_lis_client.models.legislation import (
    Legislation,
    LegislationSession,
    LegislationStatus,
    LegislationSummaryItem,
    LegislationVersion,
    Patron,
)
from va_lis_client.models.member import District, Member, Party
from va_lis_client.models.pagination import (
    PagedList,
    Pagination,
    page_request_header,
)
from va_lis_client.models.schedule import MeetingRoom, Schedule, ScheduleType
from va_lis_client.models.session import Session, SessionEvent
from va_lis_client.models.text import (
    LegislationSummary,
    LegislationTextDetail,
    LegislationTextItem,
    TextFile,
)
from va_lis_client.models.vote import (
    Vote,
    VoteFile,
    VoteLegislation,
    VoteMember,
    VoteStatement,
    VoteType,
)

__all__ = [
    # common
    "Heartbeat",
    "Partner",
    # session
    "Session",
    "SessionEvent",
    # legislation
    "Legislation",
    "LegislationSession",
    "LegislationStatus",
    "LegislationSummaryItem",
    "LegislationVersion",
    "Patron",
    # text
    "LegislationSummary",
    "LegislationTextDetail",
    "LegislationTextItem",
    "TextFile",
    # committee
    "Committee",
    "CommitteeAction",
    "CommitteeFile",
    "CommitteeMember",
    # schedule
    "MeetingRoom",
    "Schedule",
    "ScheduleType",
    # calendar
    "Agenda",
    "AgendaItem",
    "CalendarCategory",
    "CalendarComment",
    "CalendarDetail",
    "CalendarFile",
    "CalendarItem",
    "Staff",
    # vote
    "Vote",
    "VoteFile",
    "VoteLegislation",
    "VoteMember",
    "VoteStatement",
    "VoteType",
    # docket
    "CalendarDisplay",
    "DocketCategory",
    "DocketDetail",
    "DocketItem",
    "DocketListItem",
    # pagination
    "PagedList",
    "Pagination",
    "page_request_header",
    # member
    "District",
    "Member",
    "Party",
    # event
    "ActorType",
    "EventReference",
    "LegislationEvent",
    "LegislationEventType",
]
