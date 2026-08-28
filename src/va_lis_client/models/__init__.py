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
    calendar     — CalendarFile, CalendarComment, CalendarItem, VoteMember,
                   AgendaItem, Agenda, CalendarCategory, Staff, CalendarDetail
    docket       — DocketItem, DocketCategory, DocketListItem, CalendarDisplay,
                   DocketDetail
    event        — EventReference, LegislationEvent, LegislationEventType,
                   ActorType
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
    VoteMember,
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
    "VoteMember",
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
    # event
    "ActorType",
    "EventReference",
    "LegislationEvent",
    "LegislationEventType",
]
