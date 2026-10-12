"""Minutes book models — the daily record of what a chamber or committee did.

A minutes book is one body's proceedings on one day.  It nests four levels
deep: the book holds categories, a category holds entries, an entry holds
activities, and an activity holds references.  A bill sits on the *entry*;
what happened to it sits on the *activity*.

The activity is the reason this service matters.  It carries ``VoteID``,
``VoteNumber``, ``VoteTally``, ``EventCode`` and ``IsBlock`` together, and
that ``VoteID`` is the roll call for the bill on the entry above it — see
:class:`MinutesActivity` for the measurement behind that claim.
"""

from datetime import datetime

from pydantic import Field

from va_lis_client.models.common import LISModel


class MinutesFile(LISModel):
    """A generated file attached to a minutes book.

    LIS publishes the pair together: ``TextFormatID`` 2 is HTML and 5 is
    JSON.  Across the 388 books of 20261 there were 186 of each and nothing
    else.  A book still in progress usually has neither.

    Note the type is an ``int`` here.  On
    :class:`~va_lis_client.models.vote.VoteFile` the same field is a string.

    Example::

        {"MinutesFileID": 1221746, "MinutesBookID": 3861,
         "FileURL": "https://lis.blob.core.windows.net/files/1221746.HTML",
         "TextFormatID": 2, "IsActive": true}
    """

    MinutesFileID: int | None = None
    MinutesBookID: int | None = None
    FileURL: str | None = None
    TextFormatID: int | None = None  # 2=HTML, 5=JSON
    IsActive: bool = True


class MinutesCalendar(LISModel):
    """The floor calendar a minutes book was taken against.

    Links to the Calendar service by ``CalendarID``, which is also the
    second way to address the book itself (see
    :meth:`~va_lis_client.client.LISClient.get_minutes_book`).

    Example::

        {"MinutesBookID": 3861, "CalendarID": 21149, "CalendarNumber": 1,
         "Description": "Calendar", "CalendarDate": "2026-04-22T00:00:00"}
    """

    MinutesBookID: int | None = None
    CalendarID: int | None = None  # FK to Calendar
    CalendarNumber: int | None = None
    Description: str | None = None
    MinutesStatusID: int | None = None
    CalendarDate: datetime | None = None
    Status: str | None = None
    StatusDate: datetime | None = None
    DeletionDate: datetime | None = None


class ActivityReference(LISModel):
    """A citation hanging off one activity.

    ``ActionReferenceTypeID`` names the kind, and
    :class:`ActionReferenceType` tells you which service resolves
    ``ReferenceID``.  Type 1 (``"Text"``) is free prose with a null
    ``ReferenceID`` and it dominates: 80 of the 89 references in Senate book
    3861 were text, against 6 communications, 2 members and 1 legislation
    event.

    Example::

        {"ReferenceText": "Called to Order at 12 m. by Lt. Governor ...",
         "ReferenceID": null, "MinutesActivityID": 36217,
         "ActionReferenceTypeID": 1, "ActionReferenceType": "Text",
         "Sequence": 0}
    """

    MinutesActivityID: int | None = None
    ActionReferenceTypeID: int | None = None
    ActionReferenceType: str | None = None  # e.g. "Text", "Member"
    ReferenceID: int | None = None  # null for free text
    ReferenceText: str | None = None
    Sequence: int | None = None


class MinutesActivity(LISModel):
    """One action taken on one entry — and the only reliable vote join in LIS.

    **The ``VoteID`` here really is the roll call for the bill on the parent
    entry.**  That is worth stating plainly, because
    :attr:`~va_lis_client.models.event.LegislationEvent.VoteID` is not: a
    bill event often points at a vote that disposed of some *other* bill (see
    "A VoteID on a bill event is often not that bill's roll call" in
    ``docs/votes.md``).  Sampled live on 2026-09-15 against Senate book 3861 — 3
    recorded roll calls, 2 block votes and 2 voice votes — every ``VoteID``
    resolved to a vote whose ``VoteLegislation`` listed the entry's own bill,
    and every ``VoteNumber`` matched the vote record exactly.

    **The tally string here is richer than the vote's.**  The same book gave
    activity ``"(18-Y 18-N 0-A, Chair votes Yea)"`` where the Vote service
    returned only ``"(18-Y 18-N 0-A)"``.  The presiding officer's tiebreak
    survives in the minutes and nowhere else.  Block votes are prefixed too:
    ``"block vote (21-Y 18-N 0-A)"`` against the vote's ``"(21-Y 18-N 0-A)"``.
    So read :attr:`VoteTally` for the human record and the Vote service for
    the counts.

    ``IsBlock`` marks the shared motion: one ``VoteID`` appears on every bill
    it disposed of.  Book 3861 had 14 block activities over 2 distinct votes,
    one of them covering 12 bills.  A block vote is not a member's verdict on
    any one of them.

    Example::

        {"MinutesActivityID": 35904, "MinutesEntryID": 31074,
         "Description": "Reported from Rules (13-Y 0-N)",
         "Sequence": 1, "CalendarActionID": 1817, "EventCode": "S1005",
         "ActionDescription": "Reported from Rules ", "VoteID": 301946,
         "VoteNumber": "S10V50", "IsBlock": false,
         "VoteTally": "(13-Y 0-N)", "ActivityReferences": []}
    """

    MinutesActivityID: int | None = None
    MinutesEntryID: int | None = None
    Description: str | None = None
    Sequence: int | None = None
    CalendarActionID: int | None = None
    ActionDescription: str | None = None  # LIS pads this; LISModel strips it
    EventCode: str | None = None  # joins the event vocabulary, e.g. "S1005"
    VoteID: int | None = None  # FK to Vote — trustworthy here
    VoteNumber: str | None = None  # e.g. "SV1526"
    VoteTally: str | None = None  # richer than the Vote service's
    VoteIsPublic: bool = False
    CommitteeComplete: bool = False
    IsBlock: bool = False
    IsPassed: bool = False
    IsPublic: bool = True
    InPreview: bool = False
    DeletionDate: datetime | None = None
    ActivityReferences: list[ActivityReference] = []

    @property
    def is_voice(self) -> bool:
        """Whether the tally reads as a voice vote, which names no members."""
        return "Voice Vote" in (self.VoteTally or "")


class MinutesSummary(LISModel):
    """An HTML narrative note on an entry.

    Carries prose the structured fields do not, e.g. ``"On motion of Senator
    Surovell, the Senate recessed at 2:40 p.m. to reconvene at 3:10 p.m."``

    The payload key is ``MinutesSummary``, the same word as this class, so
    the field is aliased to :attr:`summary`.

    Example::

        {"MinutesEntryID": 31341, "ReferenceID": null,
         "MinutesSummary": "<p>On motion of Senator Surovell, ...</p>"}
    """

    MinutesEntryID: int | None = None
    ReferenceID: int | None = None
    summary: str | None = Field(default=None, alias="MinutesSummary")


class MinutesEntry(LISModel):
    """One line of business — usually a bill, sometimes a procedural note.

    An entry names the bill; its :attr:`MinutesActivities` say what happened
    to it.  An entry with no ``LegislationNumber`` is procedural (a recess, a
    communication, an order of business).  Senate book 3861 held 195 entries,
    181 of them on a bill.

    **LIS omits keys rather than sending nulls.**  Those same 195 entries
    arrived in 4 different key sets: ``AgendaID`` is absent on procedural
    rows, and ``CommunicationChamberCode`` and
    ``CommunicationReferenceNumber`` appear only on the 2 rows that carry a
    communication.  Every field is therefore optional.

    Example::

        {"MinutesEntryID": 31270, "MinutesCategoryID": 3056, "Sequence": 1,
         "LegislationID": 98536, "LegislationNumber": "SB2",
         "LegislationDescription": "Paid family & med. leave insurance ...",
         "LegislationChamberCode": "S", "AgendaID": 299172,
         "MinutesActivities": [{...}], "MinutesSummaries": []}
    """

    MinutesEntryID: int | None = None
    MinutesCategoryID: int | None = None
    Sequence: int | None = None
    LegislationID: int | None = None  # FK to Legislation
    LegislationNumber: str | None = None  # e.g. "SB2"
    LegislationDescription: str | None = None
    LegislationChamberCode: str | None = None  # the bill's chamber, not the book's
    AgendaID: int | None = None
    EntryText: str | None = None
    CommunicationID: int | None = None
    CommunicationNumber: int | None = None
    CommunicationReferenceNumber: str | None = None
    CommunicationChamberCode: str | None = None
    CommunicationDate: datetime | None = None
    MinutesDate: datetime | None = None  # null in practice; read the book's
    IsPublic: bool = True
    IsOOB: bool | None = None  # out of block; LIS sends an explicit null
    ReleaseToPreview: bool | None = None  # null in practice
    MinutesActivities: list[MinutesActivity] = []
    MinutesSummaries: list[MinutesSummary] = []

    @property
    def is_bill(self) -> bool:
        """Whether this entry names a bill rather than procedural business."""
        return bool(self.LegislationNumber)


class MinutesCategory(LISModel):
    """A section of the day's business, e.g. "Senate Bills Vetoed by Governor".

    **``CategoryCode`` is null on every category observed**, unlike the
    docket and calendar categories, which do carry a code.  Read
    :attr:`CategoryType` (``"General"``, ``"Order"``, ``"Communication"``,
    ``"Miscellaneous"``) and :attr:`CategoryDescription` instead.

    :attr:`CategoryDescription` repeats within a book: Senate book 3861 had
    11 categories, 3 of them described as ``"Order of Business"``.  Order is
    :attr:`Sequence`, not the description.

    Example::

        {"MinutesCategoryID": 3056, "MinutesBookID": 3861, "Sequence": 4,
         "CategoryCode": null, "CategoryType": "General",
         "CategoryDescription": "Senate Bill with Governor's Recommendation",
         "CategoryText": "Senate Bills with Governor's Recommendations",
         "MinutesEntries": [{...}]}
    """

    MinutesCategoryID: int | None = None
    MinutesBookID: int | None = None
    Sequence: int | None = None
    CalendarCategoryTypeID: int | None = None
    CategoryTypeID: int | None = None
    CategoryType: str | None = None  # "General", "Order", "Communication"
    CategoryCode: str | None = None  # null in practice
    CategoryDescription: str | None = None
    CategoryText: str | None = None
    ChamberCode: str | None = None  # null in practice; read the book's
    DisplayType: bool | None = None
    ReleaseToPreview: bool | None = None
    DeletionDate: datetime | None = None
    MinutesEntries: list[MinutesEntry] = []


class MinutesBookListItem(LISModel):
    """A book from ``/MinutesBook/api/getpublishedminutesbooklistasync``.

    The list is shallow on purpose: it carries no categories, so it names no
    bills.  Call :meth:`~va_lis_client.client.LISClient.get_minutes_book` for
    those.

    **Two fields on this row are dead.**  ``IsCommittee`` is ``False`` on all
    388 rows of 20261, including the 342 that name a committee, so it does
    not mean what it says.  ``SessionCode`` is null on all 388, even though
    you filtered by it.  To tell a committee book from a floor book, test
    :attr:`CommitteeID`, which LIS omits entirely on a floor book — that is
    what :attr:`is_floor` does.

    ``Status`` and ``PendingChange`` were null on every row observed.

    Example::

        {"MinutesBookID": 3859, "SessionName": "2026 Regular Session",
         "SessionCode": null, "SessionID": 59, "CommitteeID": 18,
         "CommitteeName": "Privileges and Elections", "ChamberCode": "H",
         "MinutesDate": "2026-03-13T00:00:00", "MinutesNumber": 1,
         "MinutesStatusID": 2, "MinutesStatus": "In Progress",
         "IsPublic": true, "IsCommittee": false}
    """

    MinutesBookID: int  # surrogate PK
    MinutesDate: datetime | None = None
    MinutesNumber: int | None = None
    MinutesStatusID: int | None = None
    MinutesStatus: str | None = None  # "In Progress" or "Closed"
    ChamberCode: str | None = None  # the body meeting, "H" or "S"
    CommitteeID: int | None = None  # absent on a floor book
    CommitteeName: str | None = None
    SessionID: int | None = None
    SessionCode: str | None = None  # null in practice
    SessionName: str | None = None
    VoteRoomID: int | None = None
    Status: str | None = None  # null in practice
    PendingChange: bool | None = None  # null in practice
    IsPublic: bool = True
    IsCommittee: bool = False  # always False; do not trust it
    MinutesCalendars: list[MinutesCalendar] = []
    MinutesFiles: list[MinutesFile] = []

    @property
    def is_floor(self) -> bool:
        """Whether this is a floor book rather than a committee book.

        Floor books are **Senate only** in every session checked: 46 of 388
        in 20261, 33 of 599 in 20251, and all 45 rows of 20241.  The House
        publishes committee books and nothing else.
        """
        return self.CommitteeID is None

    @property
    def html_file(self) -> MinutesFile | None:
        """The generated HTML file, when the book has one."""
        return next((f for f in self.MinutesFiles if f.TextFormatID == 2), None)

    @property
    def json_file(self) -> MinutesFile | None:
        """The generated JSON file, when the book has one."""
        return next((f for f in self.MinutesFiles if f.TextFormatID == 5), None)


class MinutesBook(MinutesBookListItem):
    """A full book from ``/MinutesBook/api/getminutesbookasync``.

    The same row as :class:`MinutesBookListItem` plus
    :attr:`MinutesCategories`, which is where every bill and every vote
    lives.  The detail response drops ``Status`` and ``PendingChange`` and
    adds ``VoteRoomID`` on a committee book; both shapes validate here
    because every field is optional.

    Example (truncated)::

        {"MinutesBookID": 3861, "ChamberCode": "S",
         "MinutesDate": "2026-04-22T00:00:00", "MinutesStatus": "Closed",
         "MinutesCategories": [{"CategoryDescription": "Order of Business",
                                "MinutesEntries": [{...}]}]}
    """

    MinutesCategories: list[MinutesCategory] = []

    @property
    def entries(self) -> list[MinutesEntry]:
        """Every entry across the categories, in category then entry order."""
        return [e for c in self.MinutesCategories for e in c.MinutesEntries]

    @property
    def bill_entries(self) -> list[MinutesEntry]:
        """The entries that name a bill, dropping procedural business."""
        return [e for e in self.entries if e.is_bill]

    @property
    def activities(self) -> list[MinutesActivity]:
        """Every activity across every entry, in book order."""
        return [a for e in self.entries for a in e.MinutesActivities]


class ActionReferenceType(LISModel):
    """The vocabulary behind :attr:`ActivityReference.ActionReferenceTypeID`.

    15 rows, and :attr:`ObjectName` is the useful column: it names the entity
    that :attr:`ActivityReference.ReferenceID` points at, so it tells you
    which service resolves the citation.  A null ``ObjectName`` means the
    reference is self-contained prose.

    ``ActionReferenceTypeID`` 10 does not exist; the ids run 1-9 and 11-16.

    Example::

        {"ActionReferenceTypeID": 5, "ActionReferenceType": "Legislation",
         "ObjectName": "Legislation"}
    """

    ActionReferenceTypeID: int
    ActionReferenceType: str | None = None
    ObjectName: str | None = None  # null when the reference is free text
