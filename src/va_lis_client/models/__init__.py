"""
Pydantic models for LIS API responses.

Built from live API responses — the OpenAPI specs are unreliable
(e.g. envelope keys differ from reality).

Modules:
    common       — Heartbeat, Partner
    session      — SessionEvent, Session
    legislation  — Patron, PatronRole, PatronBill, PatronLegislation,
                   LegislationSession, LegislationSummaryItem, Legislation,
                   MemberLegislation, LegislationStatus, LegislationVersion,
                   PATRON_ROLES
    text         — TextFile, LegislationTextItem, LegislationTextDetail,
                   LegislationSummary
    bill_version — BillVersion (one bill's versions; not LegislationVersion,
                   which is the version type vocabulary)
    statistics   — ChamberStatistic, CommitteeStatistic
    committee    — CommitteeFile, Committee, CommitteeMember, CommitteeRole,
                   CommitteeAction
    communication — CommunicationFile, CommunicationLegislation,
                   CommunicationCategory, CommunicationListItem,
                   Communication, CommunicationCategoryType
    schedule     — Schedule, ScheduleFile, ScheduleType, MeetingRoom
    search       — LegislationSearchResult, SearchTextMatch, LegislationCategory,
                   LegislationNumberEntry, IntroductionDate,
                   SUMMARY_VERSION_RANK, summary_version_rank
    calendar     — CalendarFile, CalendarComment, CalendarItem,
                   AgendaItem, Agenda, CalendarCategory, Staff, CalendarDetail,
                   CalendarType, CalendarCategoryType, CategoryType,
                   CalendarAction
    vote         — VoteMember, VoteLegislation, VoteStatement, VoteFile,
                   VoteType, Vote
    docket       — DocketItem, DocketCategory, DocketListItem, CalendarDisplay,
                   DocketDetail
    minutes      — MinutesFile, MinutesCalendar, ActivityReference,
                   MinutesActivity, MinutesSummary, MinutesEntry,
                   MinutesCategory, MinutesBookListItem, MinutesBook,
                   ActionReferenceType
    event        — EventReference, LegislationEvent, LegislationEventType,
                   ActorType
    directory    — Personnel, StaffRoleType, Contact, ContactType,
                   OwnerType, Agency
    member       — Member, Party, District
    member_vote  — MemberVoteResult
    pagination   — Pagination, PagedList, page_request_header
"""

from va_lis_client.models.bill_version import BillVersion
from va_lis_client.models.calendar import (
    Agenda,
    AgendaItem,
    CalendarAction,
    CalendarCategory,
    CalendarCategoryType,
    CalendarComment,
    CalendarDetail,
    CalendarFile,
    CalendarItem,
    CalendarType,
    CategoryType,
    Staff,
)
from va_lis_client.models.committee import (
    Committee,
    CommitteeAction,
    CommitteeFile,
    CommitteeMember,
    CommitteeRole,
)
from va_lis_client.models.common import Heartbeat, Partner
from va_lis_client.models.communication import (
    Communication,
    CommunicationCategory,
    CommunicationCategoryType,
    CommunicationFile,
    CommunicationLegislation,
    CommunicationListItem,
)
from va_lis_client.models.directory import (
    Agency,
    Contact,
    ContactType,
    OwnerType,
    Personnel,
    StaffRoleType,
)
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
    CHIEF_CO_PATRON,
    CHIEF_PATRON,
    CO_PATRON,
    PATRON_ROLES,
    Legislation,
    LegislationSession,
    LegislationStatus,
    LegislationSummaryItem,
    LegislationVersion,
    MemberLegislation,
    Patron,
    PatronBill,
    PatronLegislation,
    PatronRole,
)
from va_lis_client.models.member import District, Member, Party
from va_lis_client.models.member_vote import MemberVoteResult
from va_lis_client.models.minutes import (
    ActionReferenceType,
    ActivityReference,
    MinutesActivity,
    MinutesBook,
    MinutesBookListItem,
    MinutesCalendar,
    MinutesCategory,
    MinutesEntry,
    MinutesFile,
    MinutesSummary,
)
from va_lis_client.models.pagination import (
    PagedList,
    Pagination,
    page_request_header,
)
from va_lis_client.models.schedule import MeetingRoom, Schedule, ScheduleFile, ScheduleType
from va_lis_client.models.search import (
    SUMMARY_VERSION_RANK,
    UNKNOWN_SUMMARY_RANK,
    IntroductionDate,
    LegislationCategory,
    LegislationNumberEntry,
    LegislationSearchResult,
    SearchTextMatch,
    summary_version_rank,
)
from va_lis_client.models.session import Session, SessionEvent
from va_lis_client.models.statistics import ChamberStatistic, CommitteeStatistic
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
    "CHIEF_CO_PATRON",
    "CHIEF_PATRON",
    "CO_PATRON",
    "PATRON_ROLES",
    "Legislation",
    "LegislationSession",
    "LegislationStatus",
    "LegislationSummaryItem",
    "LegislationVersion",
    "MemberLegislation",
    "Patron",
    "PatronBill",
    "PatronLegislation",
    "PatronRole",
    # text
    "LegislationSummary",
    "LegislationTextDetail",
    "LegislationTextItem",
    "TextFile",
    # bill_version
    "BillVersion",
    # statistics
    "ChamberStatistic",
    "CommitteeStatistic",
    # search
    "SUMMARY_VERSION_RANK",
    "UNKNOWN_SUMMARY_RANK",
    "IntroductionDate",
    "LegislationCategory",
    "LegislationNumberEntry",
    "LegislationSearchResult",
    "SearchTextMatch",
    "summary_version_rank",
    # committee
    "Committee",
    "CommitteeAction",
    "CommitteeFile",
    "CommitteeMember",
    "CommitteeRole",
    # communication
    "Communication",
    "CommunicationCategory",
    "CommunicationCategoryType",
    "CommunicationFile",
    "CommunicationLegislation",
    "CommunicationListItem",
    # schedule
    "MeetingRoom",
    "Schedule",
    "ScheduleFile",
    "ScheduleType",
    # calendar
    "Agenda",
    "AgendaItem",
    "CalendarAction",
    "CalendarCategory",
    "CalendarCategoryType",
    "CalendarComment",
    "CalendarDetail",
    "CalendarFile",
    "CalendarItem",
    "CalendarType",
    "CategoryType",
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
    # minutes
    "ActionReferenceType",
    "ActivityReference",
    "MinutesActivity",
    "MinutesBook",
    "MinutesBookListItem",
    "MinutesCalendar",
    "MinutesCategory",
    "MinutesEntry",
    "MinutesFile",
    "MinutesSummary",
    # directory
    "Agency",
    "Contact",
    "ContactType",
    "OwnerType",
    "Personnel",
    "StaffRoleType",
    # pagination
    "PagedList",
    "Pagination",
    "page_request_header",
    # member
    "District",
    "Member",
    "MemberVoteResult",
    "Party",
    # event
    "ActorType",
    "EventReference",
    "LegislationEvent",
    "LegislationEventType",
]
