"""Legislation (bill) models — patrons, statuses, versions, and bill detail."""

from datetime import datetime

from pydantic import Field

from va_lis_client.models.common import LISModel

# The patron role vocabulary from ``/LegislationPatron/api/getpatronroletypelistasync``
# (five rows), plus the ``0`` that ``getmemberpatrontypelistasync`` returns for a
# member's budget amendment requests.  ``Patron.role`` reads this when a row
# arrives without its ``Name``.
PATRON_ROLES: dict[int, str] = {
    0: "Budget Amendment Requests",
    1: "Chief Patron",
    2: "Chief Co-Patron",
    3: "Incorporated Chief Co-Patron",
    4: "Co-Patron",
    5: "Offered",
}
CHIEF_PATRON = 1
CHIEF_CO_PATRON = 2
CO_PATRON = 4


class Patron(LISModel):
    """A bill patron (sponsor/co-sponsor).

    ``PatronTypeID`` determines the role; see :data:`PATRON_ROLES`:
    - 1 = Chief Patron (primary sponsor)
    - 2 = Chief Co-Patron
    - 4 = Co-Patron

    ``MemberNumber`` is the LIS member code: ``"H0173"`` (House member 173),
    ``"S0121"`` (Senate member 121).

    **Two shapes arrive.**  Bill detail, ``/LegislationPatron``, calendars,
    and dockets send every field.  The member-first bill list
    (``/LegislationByMember``) nests a thinner patron with no
    ``LegislationID``, ``ChamberCode``, ``MemberNumber``, or ``Name``, and its
    ``PatronDisplayName`` reads ``"Cole, J.G."`` rather than ``"Cole"``.  Read
    :attr:`role` for the role name; it works on both.

    Example::

        {"LegislationID": 98525, "ChamberCode": "H", "MemberID": 419,
         "MemberNumber": "H0173", "PatronTypeID": 1,
         "Name": "Chief Patron", "MemberDisplayName": "Jeion A. Ward",
         "PatronDisplayName": "Ward"}
    """

    LegislationID: int | None = None  # absent on the member-first list
    ChamberCode: str | None = None  # "H" = House, "S" = Senate; absent on the member-first list
    MemberID: int  # surrogate PK for the member
    MemberNumber: str | None = None  # e.g. "H0173", "S0121"; absent on the member-first list
    PatronTypeID: int  # 1=Chief Patron, 2=Chief Co-Patron, 4=Co-Patron
    Name: str | None = None  # role name, e.g. "Chief Patron"; absent on the member-first list
    DisplayName: str | None = None  # e.g. "(Chief Patron)"
    MemberDisplayName: str | None = None  # full name, e.g. "Jeion A. Ward"
    PatronDisplayName: str | None = None  # last name, e.g. "Ward"
    LegislationNumber: str | None = None
    Sequence: int | None = None  # display order
    IsIntroducing: bool | None = None
    ByRequest: bool | None = None  # True if introduced "by request"
    LegislationTextID: int | None = None

    @property
    def role(self) -> str:
        """The role name, e.g. ``"Chief Patron"``, on every patron shape.

        ``Name`` when LIS sent it, otherwise the vocabulary entry for
        ``PatronTypeID``.
        """
        return self.Name or PATRON_ROLES.get(self.PatronTypeID, f"PatronTypeID {self.PatronTypeID}")


class PatronRole(LISModel):
    """A row of ``/LegislationPatron/api/getpatronroletypelistasync``.

    Five rows: 1 Chief Patron, 2 Chief Co-Patron, 3 Incorporated Chief
    Co-Patron, 4 Co-Patron, 5 Offered.  ``DisplayName`` is null on Co-Patron.

    Example::

        {"PatronTypeID": 1, "Name": "Chief Patron", "DisplayName": "(Chief Patron)"}
    """

    PatronTypeID: int
    Name: str
    DisplayName: str | None = None


class LegislationSession(LISModel):
    """Session cross-reference on a bill (which sessions it appeared in).

    This list is the explicit carry-over lineage record: a bill continued
    from an even-year session into the odd-year session has one entry per
    session.  ``IsPrefile`` is ``True`` on the session the bill was
    prefiled for, not on the carry-over appearance.

    Example (HB9, LegislationID 98631, carried over)::

        [{"SessionID": 59, "SessionCode": "20261", "IsPrefile": true},
         {"SessionID": 61, "SessionCode": "20271", "IsPrefile": false}]
    """

    SessionID: int
    SessionCode: str
    IsPrefile: bool = False


class LegislationSummaryItem(LISModel):
    """Lightweight bill record from the per-session list endpoint.

    Returned by ``/Legislation/api/getlegislationsessionlistasync``.
    Use ``session_code`` (e.g. ``20261``) to query.

    Key: ``LegislationNumber`` is unique within a session (e.g. "HB1" in
    20261).  ``LegislationID`` names the *logical* bill and is reused
    across even→odd carry-over (HB9 keeps ID 98631 in both 20261 and
    20271), so it is only unique within ``(session, LegislationID)``.
    An ID never spans a two-year GA term and never carries twice.

    ``LegislationTypeCode``: ``"B"`` = Bill, ``"J"`` = Joint Resolution,
    ``"R"`` = Resolution.

    Example::

        {"LegislationID": 98525, "LegislationNumber": "HB1",
         "Description": "Minimum wage; increases incrementally...",
         "ChamberCode": "H", "LegislationTypeCode": "B",
         "LegislationStatus": "Awaiting Governor's Action",
         "Patrons": [{"MemberDisplayName": "Jeion A. Ward", ...}]}
    """

    LegislationID: int  # surrogate PK — reused across even→odd carry-over (see docstring)
    LegislationNumber: str  # e.g. "HB1", "SB234" (unpadded)
    Description: str  # short description
    LegislationTitle: str | None = None  # full formal title (often null here)
    ChamberCode: str  # "H" or "S"
    LegislationTypeCode: str  # "B"=Bill, "J"=Joint Resolution, "R"=Resolution
    LegislationKey: int | None = None  # numeric part of bill number (1 for HB1)
    LegislationStatus: str | None = None  # display name, e.g. "In Committee"
    Patrons: list[Patron] = []


class Legislation(LISModel):
    """Full bill detail from ``/Legislation/api/getlegislationbyidasync/{id}``.

    Query by ``LegislationID`` (the surrogate PK).  Returns one record.

    ``FullNumber`` is the zero-padded form (``"HB0001"``), while
    ``LegislationNumber`` is unpadded (``"HB1"``).

    Top-level ``SessionID``/``SessionCode`` are ``None`` in observed
    responses; ``Sessions`` holds the per-session cross-refs — two entries
    for a bill carried over from an even-year to the odd-year session,
    since carry-over reuses the ``LegislationID``.

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

    LegislationID: int  # surrogate PK — reused across even→odd carry-over (see docstring)
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
    SessionID: int | None = None  # ALWAYS NULL in observed responses — use Sessions[]
    SessionCode: str | None = None  # ALWAYS NULL in observed responses — use Sessions[]
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


class MemberLegislation(Legislation):
    """A bill as ``/LegislationByMember/api/getmemberlegislationlistasync`` lists it.

    The detail shape plus the summary text, for every bill a member patrons
    in a session.  Returned by :meth:`LISClient.get_member_legislation`.

    **One row per summary version, not one per bill.**  HB18 appears three
    times in Delegate Schmidt's 2026 list, once each as introduced, as passed
    chamber, and as passed, so his 236 rows describe 229 bills.
    :meth:`LISService.member_bills` collapses them to the newest version.

    **This service has its own summary vocabulary.**  It sends four labels,
    and its chamber passage label is ``SUMMARY AS PASSED CHAMBER``.  It never
    sends the ``SUMMARY AS PASSED HOUSE`` / ``SUMMARY AS PASSED SENATE`` pair
    that ``/AdvancedLegislationSearch`` sends, and the search never sends
    ``CHAMBER``.  Rank either spelling with
    :func:`~va_lis_client.models.search.summary_version_rank`.

    **Rows also arrive as exact duplicates**, the same defect the search
    shows: 141 of 198 multi-row groups across a 40-member sample of 20261
    held two byte identical rows (measured 2026-09-15).  Nothing in the row
    tells the copies apart, so de-duplicate on ``LegislationID``.

    **``Patrons`` is not the bill's patron list.**  It is empty on 177 of
    those 236 rows and holds exactly one entry on the rest, the chief patron
    in the thin shape; the member being listed never appears in it.  Call
    :meth:`LISClient.get_bill_patrons` for the real list.

    ``SessionID`` is set on every row; ``SessionCode``, ``IntroductionDate``,
    ``FullNumber``, and ``LegislationStatusID`` are null, and
    ``LegislationTextID`` is ``0``.  ``Sessions`` is always empty, so this
    row cannot show carry-over lineage.

    Example (fields beyond :class:`Legislation`)::

        {"LegislationSummary": "<p class=\\"sumtext\\"><b>Employee Child Care...",
         "SummaryVersion": "SUMMARY AS INTRODUCED",
         "SessionName": "Regular Session", "SessionID": 59,
         "LegislationTextID": 0, "VersionDate": null, "CandidateDate": null,
         "SearchText": []}
    """

    LegislationSummary: str | None = None  # HTML, one summary version per row
    SummaryVersion: str | None = None  # e.g. "SUMMARY AS INTRODUCED", "SUMMARY AS PASSED"
    SessionName: str | None = None  # e.g. "Regular Session", "Special Session I"
    LegislationTextID: int | None = None  # 0 on every observed row
    VersionDate: datetime | None = None
    CandidateDate: datetime | None = None
    SearchText: list = []  # empty on every observed row


class LegislationStatus(LISModel):
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


class LegislationVersion(LISModel):
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


class PatronBill(Legislation):
    """A bill under a member in the chamber-wide chief patron index.

    The :class:`Legislation` shape plus two fields that only this endpoint
    sends.  Unlike :class:`MemberLegislation` it carries no summary, so
    there is **one row per bill**, not one per summary version.

    Example (fields beyond :class:`Legislation`)::

        {"IsPrefile": true, "SessionName": "Regular Session"}
    """

    IsPrefile: bool | None = None
    SessionName: str | None = None  # e.g. "Regular Session"


class PatronLegislation(Patron):
    """One member's bills from ``getlegislationpatronlistasync``.

    The :class:`Patron` shape with the member's bills hung off it.  One row
    per member per patron type, and the whole chamber arrives in a single
    request: 102 rows and 2,342 bills for the 20261 House, 42 rows and 1,304
    bills for its Senate.

    **It is chief patron only.**  Every row of both chambers came back as
    ``Chief Patron`` (``PatronTypeID`` 1) on 2026-09-15, and the row count
    equals the distinct member count, so there is exactly one row per member.
    This endpoint is a cheap chamber-wide index, not a route to co-patronage.
    Call :meth:`~va_lis_client.client.LISClient.get_bill_patrons` for a
    bill's full patron list, or
    :meth:`~va_lis_client.service.LISService.member_bills` for one member's
    co-patronage.

    The payload key for the bills is ``Legislation``, which collides with the
    :class:`Legislation` model, so the field is aliased to :attr:`bills`.

    Example (truncated)::

        {"MemberID": 527, "MemberNumber": "H0386", "PatronTypeID": 1,
         "Name": "Chief Patron", "MemberDisplayName": " Jessica L. Anderson",
         "Legislation": [{"LegislationID": 98991, "LegislationNumber": "HB288",
                          "LegislationStatus": "Acts of Assembly Chapter"}]}
    """

    bills: list[PatronBill] = Field(default=[], alias="Legislation")


class Amendment(LISModel):
    """One amendment text on a bill, nested in :class:`AmendedLegislation`.

    Key: ``LegislationTextID``, which resolves through
    :meth:`~va_lis_client.client.LISClient.get_draft_text`.

    ``ChamberCode`` is the chamber that offered the amendment, not the bill's.
    ``DocumentCode`` encodes it: ``HB1005AH1`` is the first House amendment
    to HB1005, and a Senate amendment to it would carry ``AS``.

    ``TextDisposition`` says what became of it.  The House list for 20261
    held 268 ``Adopted``, 66 ``Engrossed``, 21 ``Not Adopted``, 13
    ``Removed`` and 11 ``Posted`` (measured 2026-10-12).  ``LDNumber`` was
    null on 71 of those 379.

    Example::

        {"LegislationID": 99829, "LegislationTextID": 261928,
         "LegislationVersionID": 8, "LegislationVersion": "Amendment",
         "TextDispositionID": 6, "TextDisposition": "Adopted",
         "DocumentCode": "HB1005AH1", "ChamberCode": "H",
         "Description": "General Laws Amendment", "LDNumber": "26106673A",
         "VersionDate": "2026-02-03T18:15:00"}
    """

    LegislationID: int | None = None
    LegislationTextID: int
    LegislationVersionID: int | None = None  # 8 on every observed row
    LegislationVersion: str | None = None  # "Amendment" on every observed row
    TextDispositionID: int | None = None  # see get_text_dispositions
    TextDisposition: str | None = None  # e.g. "Adopted", "Not Adopted"
    IsActive: bool = False
    IsPublic: bool = True
    DocumentCode: str | None = None  # e.g. "HB1005AH1"
    ChamberCode: str | None = None  # the amendment's chamber, not the bill's
    Description: str | None = None  # e.g. "General Laws Amendment"
    LDNumber: str | None = None
    VersionDate: datetime | None = None


class AmendedLegislation(LISModel):
    """A bill with its amendments, from ``getlegislationamendmentlistasync``.

    Key: ``(LegislationID, SummaryVersion)``.  **A bill repeats once per
    summary version**, each copy carrying the same amendments: HB18 arrived
    three times in the 20261 House list, as ``SUMMARY AS INTRODUCED``,
    ``SUMMARY AS PASSED CHAMBER`` and ``SUMMARY AS PASSED``.  That list held
    278 rows over 263 bills.  Collapse on ``LegislationID`` before you count.

    **The chamber filter picks the amendments, not the bills.**  The House
    list for 20261 held 207 House bills and 71 Senate bills, and every one of
    its 379 amendments was a House amendment.  ``ChamberCode`` on the row is
    the bill's chamber.

    ``LegislationSummary``, ``LegislationTitle``, ``IntroductionDate`` and
    ``FullNumber`` were null on all 278 rows.  ``Patrons`` holds the chief
    patron only.  ``CommitteeID`` is ``0`` rather than null when the bill
    sits in no committee.

    Example (truncated)::

        {"LegislationID": 99829, "LegislationNumber": "HB1005",
         "LegislationStatus": "Acts of Assembly Chapter",
         "SummaryVersion": "SUMMARY AS PASSED", "ChamberCode": "H",
         "SessionID": 59, "SessionCode": "20261",
         "Amendments": [{"DocumentCode": "HB1005AH1", ...}],
         "Patrons": [{"MemberID": 400, "Name": "Chief Patron", ...}]}
    """

    LegislationID: int
    LegislationNumber: str | None = None
    LegislationKey: int | None = None  # numeric part of the bill number
    Description: str | None = None
    LegislationTitle: str | None = None  # null on every observed row
    ChamberCode: str | None = None  # the bill's chamber
    LegislationTypeCode: str | None = None
    FullNumber: str | None = None  # null on every observed row
    LegislationStatusID: int | None = None
    LegislationStatus: str | None = None
    LegislationSummary: str | None = None  # null on every observed row
    SummaryVersion: str | None = None  # the row repeats once per version
    LegislationTextID: int | None = None  # 0 on every observed row
    EffectiveType: str | None = None
    EffectiveTypeID: int | None = None
    PendingChange: bool = False
    IntroductionDate: datetime | None = None  # null on every observed row
    SessionID: int | None = None
    SessionCode: str | None = None
    SessionName: str | None = None
    CommitteeID: int | None = None  # 0, not null, when there is no committee
    CommitteeName: str | None = None
    CommitteeNumber: str | None = None
    ParentCommitteeName: str | None = None
    Amendments: list[Amendment] = []
    Patrons: list[Patron] = []  # chief patron only


class ConferenceLegislation(LISModel):
    """A bill that went to a conference committee.

    From ``/Legislation/api/getconferencelegislationlistasync``.

    Key: ``(SessionID, LegislationID)``.  The list without a session spans
    every session, and 2,826 rows there held 2,689 unique ids.

    170 rows for 20261, 85 from each chamber, all of them bills (measured
    2026-10-12).  Their statuses were 141 ``Acts of Assembly Chapter``, 18
    ``Failed`` and 11 ``Governor's Veto``.

    The row is thin.  It carries no patrons, no summary, no committee and no
    conferees, so it says that a conference happened and nothing about it.
    The conferees are in the bill's events ("House Conferees: Seibold,
    Schmidt, Williams").

    Example::

        {"LegislationID": 98529, "LegislationNumber": "HB5",
         "FullNumber": "HB0005",
         "Description": "Employment; paid sick leave, civil penalties.",
         "ChamberCode": "H", "LegislationTypeCode": "B",
         "LegislationStatus": "Acts of Assembly Chapter",
         "SessionID": 59, "SessionName": "Regular Session", "IsPrefile": true}
    """

    LegislationID: int
    LegislationNumber: str | None = None
    FullNumber: str | None = None  # zero padded, e.g. "HB0005"
    Description: str | None = None
    IntroductionDate: datetime | None = None
    OfferedDate: datetime | None = None
    ChamberCode: str | None = None
    LegislationTypeCode: str | None = None
    LegislationClassID: int | None = None
    LegislationClass: str | None = None
    LegislationKey: int | None = None
    LegislationStatus: str | None = None
    EffectiveType: str | None = None
    SessionID: int | None = None  # there is no SessionCode on this row
    SessionName: str | None = None
    IsPrefile: bool | None = None


class LegislationIdEntry(LISModel):
    """A bill reduced to its ids, from ``/Legislation/api/getlegislationidslistasync``.

    The search result with everything but the identity removed: 228 rows and
    30 KB for member 544 in 20261, where the full rows are 440 KB.

    **The rows repeat as the search rows do.**  Those 228 rows held 203
    unique ``LegislationID`` values.  ``LegislationStatusID`` and
    ``SessionCode`` were null on all of them.

    A list of these is also the request body of
    :meth:`~va_lis_client.client.LISClient.get_legislation_by_ids`, where
    ``SessionID`` is mandatory on every entry.

    Example::

        {"LegislationID": 98525, "LegislationStatusID": null,
         "LegislationNumber": "HB1", "SessionID": 59, "SessionCode": null,
         "SearchText": []}
    """

    LegislationID: int
    LegislationNumber: str | None = None
    SessionID: int | None = None
    SessionCode: str | None = None  # null on every observed row
    LegislationStatusID: int | None = None  # null on every observed row
    SearchText: list = []


class Subject(LISModel):
    """A subject index heading, from ``/LegislationSubject/api/getsubjectreferencesasync``.

    Key: ``SubjectIndexID``.

    506 headings for 20261, session scoped.  **The vocabulary joins to
    nothing a public key can reach.**  No bill row carries a
    ``SubjectIndexID``, and passing one to the advanced search answers 204
    with either session parameter.  It is the list of headings and no more.

    Example::

        {"SubjectIndexID": 502, "Subject": "Abortion", "SubjectNumber": "3005"}
    """

    SubjectIndexID: int
    Subject: str | None = None
    SubjectNumber: str | None = None  # a string of digits, e.g. "3005"
