"""Member models — the roster that gives a roll call names, parties, districts.

A :class:`~va_lis_client.models.vote.VoteMember` row carries a ``MemberID`` and
a display name, and nothing else.  Party and district live here.

**Fetch the roster, not the member.**  The two useful endpoints are incomplete
in different dimensions:

- ``getmembersasync`` is complete in **rows** and incomplete in **fields**.  It
  returns all 148 members every time, with nine fields null on every row.
- ``getmemberbyidasync`` is complete in **fields** and incomplete in **rows**.
  It fills four of those nine and adds ``MemberDetailID``, but answers 204 for
  9 of the 148, including sitting members.

Build on the roster, because a missing row is unrecoverable and a null field is
not.  A 204 leaves a member with no name, party, or district and raises no
error, so their votes become unattributable.  The fields by-id adds are thin:
``ChamberName`` restates ``ChamberCode``, ``SessionID`` is what you passed in,
and ``SeatNumber`` and ``VotingSequence`` are seating-chart trivia.

See :class:`Member` for the field-level detail and the one field where the
roster is wrong and by-id is right.
"""

from datetime import datetime

from va_lis_client.models.common import LISModel


class Party(LISModel):
    """Party reference from ``/Member/api/getpartyreferencesasync``.

    Three parties::

        D = Democrat
        I = Independent
        R = Republican

    Join to :attr:`Member.PartyCode`.  Envelope key: ``Parties``.
    """

    PartyCode: str
    Name: str | None = None


class District(LISModel):
    """District reference from ``/Member/api/getdistrictreferencesasync``.

    140 rows, one per seat: 100 House and 40 Senate.

    Note the name of the label differs from the member record's.  A district
    row calls it ``Title``; a :class:`Member` calls the same value
    ``DistrictName``.  Both read ``"71st"``.

    Envelope key: ``Districts``.

    Example::

        {"ChamberCode": "H", "DistrictID": 1, "Title": "1st"}
    """

    DistrictID: int
    ChamberCode: str | None = None  # "H" or "S"
    Title: str | None = None  # e.g. "1st"


class Member(LISModel):
    """A legislator's roster record from ``/Member/api/getmembersasync``.

    Key: ``MemberID``, which is the person and is stable across every vote and
    session.  It joins directly to
    :attr:`~va_lis_client.models.vote.VoteMember.MemberID`; all 100 ballots on
    House vote 294006 resolve against the 2026 roster.

    **The roster is session scoped and holds more than the sitting
    membership.**  Session 20261 returns 148 rows for 140 seats: 106 House
    against 100, and 42 Senate against 40.  The extra rows are members who
    left or arrived mid-session, and one member who moved from the House to
    the Senate and so appears under two member numbers.  Keep them.  A member
    who resigned in February still cast the January votes you are attributing.

    ``ServiceEndDate`` and ``ServiceEndReason`` mark a departure, with reasons
    like ``"Resigned Feb. 18, 2026"`` or ``"Deceased"``.

    **``MemberStatusID`` is a real code, but the roster's copy is stale.**  The
    vocabulary is sound — every by-id response agrees on it::

        1 = Active      2 = Inactive      3 = Outgoing

    The roster list breaks that mapping two ways.  It omits the ID on 62 of
    148 rows.  And on a member who has left, it keeps the status they held
    *before* leaving while refreshing the name: all six rows whose list ID
    contradicts their own name carry ``MemberStatusID`` ``1`` ("Active"), a
    ``ServiceEndDate``, and a name of ``"Inactive"`` or ``"Outgoing"``.  The
    by-id call returns ``2`` or ``3`` for those same members.

    So ``MemberStatusID`` from :meth:`~va_lis_client.LISClient.get_members` is
    the *previous* status, not the current one.  **Read ``MemberStatus``**, the
    same way :class:`~va_lis_client.models.event.LegislationEvent` joins its
    status on the name rather than the ID.

    **The list nulls fields that ``getmemberbyidasync`` fills.**  Across all
    148 rows the list returns ``SessionCode``, ``SessionID``, ``ChamberName``,
    ``SeatNumber``, ``Seniority``, ``VotingSequence``, ``Salutation``,
    ``LastElectionDate``, and ``StatusReason`` as null.  The by-id call
    populates most of them and adds ``MemberDetailID`` — but it answers 204 for
    9 of the 148, so treat it as an enrichment, never as the source of truth.

    LIS pads strings on this record more than on any other: on the 2026
    roster ``GABEmailAddress`` carries trailing spaces on 50 of 148 rows,
    ``ListDisplayName`` on 4, and ``MemberDisplayName`` on 3, and 20241
    pads ``RoomNumber`` as well.  :class:`~va_lis_client.models.common.LISModel`
    strips every string, so the fields arrive clean; :attr:`name`,
    :attr:`list_name`, and :attr:`email` are shorthand, not a repair.

    Envelope key: ``Members``.

    Example (truncated)::

        {"MemberID": 527, "MemberNumber": "H0386",
         "MemberDisplayName": " Jessica L. Anderson",
         "PatronDisplayName": "Anderson",
         "ChamberCode": "H", "DistrictID": 71, "DistrictName": "71st",
         "PartyCode": "D", "MemberStatus": "Active",
         "GABEmailAddress": "deljanderson@house.virginia.gov"}
    """

    MemberID: int  # the person — stable across votes and sessions
    IdentityID: int | None = None
    MemberDetailID: int | None = None  # by-id responses only
    MemberNumber: str | None = None  # e.g. "H0386", chamber prefix + number
    ListDisplayName: str | None = None  # "Anderson, Jessica L."
    MemberDisplayName: str | None = None  # "Jessica L. Anderson"
    PatronDisplayName: str | None = None  # surname only
    Salutation: str | None = None
    ChamberCode: str | None = None  # "H" or "S"
    ChamberName: str | None = None  # null on list responses
    DistrictID: int | None = None
    DistrictName: str | None = None  # e.g. "71st" — District calls it Title
    PartyCode: str | None = None  # "D", "I", "R"
    # Read MemberStatus.  MemberStatusID is a real code (1=Active,
    # 2=Inactive, 3=Outgoing) but the roster's copy is the member's
    # PREVIOUS status, and 62 of 148 rows omit it.  See the class docstring.
    MemberStatus: str | None = None  # "Active", "Outgoing", "Inactive"
    MemberStatusID: int | None = None  # stale on the roster list
    StatusReason: str | None = None
    ServiceBeginDate: datetime | None = None
    ServiceEndDate: datetime | None = None  # set when a member leaves
    ServiceEndReason: str | None = None  # e.g. "Resigned January 17, 2026"
    LastElectionDate: datetime | None = None
    RoomNumber: str | None = None
    GABPhoneNumber: str | None = None
    GABEmailAddress: str | None = None  # LIS pads this; LISModel strips it
    SeatNumber: int | None = None  # null on list responses
    Seniority: int | None = None
    VotingSequence: int | None = None  # null on list responses
    VotingTitle: str | None = None  # "Mr. Speaker"; the active list sends it for one member
    IsPublic: bool = True
    SessionCode: str | None = None  # null even on a session-scoped query
    SessionID: int | None = None  # null on list responses

    @property
    def name(self) -> str:
        """``MemberDisplayName``, never ``None``."""
        return self.MemberDisplayName or ""

    @property
    def list_name(self) -> str:
        """``ListDisplayName`` ("Surname, Given"), never ``None``."""
        return self.ListDisplayName or ""

    @property
    def email(self) -> str:
        """``GABEmailAddress``, never ``None``."""
        return self.GABEmailAddress or ""

    @property
    def is_serving(self) -> bool:
        """False once the member has a ``ServiceEndDate``.

        Do not filter a roll call on this.  A member who left in February
        still cast the January votes.
        """
        return self.ServiceEndDate is None


class MemberStatusType(LISModel):
    """A member status, from ``/Member/api/getmemberstatustypereferencesasync``.

    Key: ``MemberStatusID``.  Four rows: 1 ``Active``, 2 ``Inactive``, 3
    ``Outgoing``, 4 ``Elect``.

    Join it to a row's ``MemberStatus`` name, not to its ``MemberStatusID``.
    The roster's id is the member's previous status, and the contact list
    sends ``0``, which is not in this vocabulary, beside ``"Active"``.
    """

    MemberStatusID: int
    Name: str | None = None


class ChamberType(LISModel):
    """A chamber, from ``/Member/api/getchambertypereferencesasync``.

    Two rows: ``H`` is ``House`` and ``S`` is ``Senate``.
    """

    ChamberCode: str
    Name: str | None = None


class MemberContactAddress(LISModel):
    """One address on a :class:`MemberContact` row.

    Two kinds arrive.  Every member has a ``"GA"`` address, the General
    Assembly Building, with no phone and no email.  83 of 106 House members
    in 20261 also had a ``"Mailing"`` address, their district office, and 80
    of those carry the district phone (measured 2026-10-12).

    **The ``"GA"`` row is a placeholder, not a Contact record.**  All 106 of
    them carried ``OwnerID`` 0 and ``ContactTypeID`` 0, omitted
    ``ContactInformationID``, and said false for both ``IsActive`` and
    ``IsPublic``, which says nothing about the member.  A ``"Mailing"`` row
    is the real thing: ``OwnerID`` is the ``MemberID``, ``ContactTypeID`` is
    9, and the shape is the :class:`~va_lis_client.models.directory.Contact`
    that ``get_contacts`` returns.

    Example::

        {"ContactInformationID": 373, "OwnerID": 503, "ContactTypeID": 9,
         "ContactType": "Mailing", "Address1": "P.O. Box 1853",
         "City": "Norfolk", "StateCode": "VA", "ZipCode": "23501",
         "PhoneNumber": "(757) 204-5492", "IsActive": true, "IsPublic": true}
    """

    ContactInformationID: int | None = None  # "Mailing" rows only
    OwnerID: int | None = None  # the MemberID on "Mailing"; 0 on "GA"
    ContactTypeID: int | None = None  # 9 on "Mailing"; 0 on "GA"
    ContactType: str | None = None  # "GA" or "Mailing"
    Address1: str | None = None
    Address2: str | None = None
    Address3: str | None = None
    City: str | None = None
    StateCode: str | None = None
    ZipCode: str | None = None
    PhoneNumber: str | None = None  # the district phone, on "Mailing" rows
    EmailAddress: str | None = None
    IsActive: bool = False
    IsPublic: bool = False


class MemberContact(LISModel):
    """A member with their addresses in one row.

    From ``/Member/api/getmemberscontactinformationlistasync``.

    Key: ``MemberID``.

    This is the one member endpoint that splits the name into ``FirstName``,
    ``MiddleName`` and ``LastName``, and the one that returns the Capitol and
    district addresses together.  106 rows for the 20261 House.

    **The three bill counts are not the member's unless you asked for one
    member.**  ``PrefiledCount``, ``NotPrefiledCount`` and
    ``LegislationTotal`` were 2,306, 1,600 and 3,906 on all 106 House rows,
    and the same on the 19 Senate Republicans.  A request that names a
    ``member_number`` returns that member's own counts: 0, 11 and 11 for
    H0402 (measured 2026-10-12).

    **``MemberStatusID`` disagrees with ``MemberStatus``.**  50 of the 106
    rows said ``0`` beside ``"Active"`` and 49 said ``1``; three said ``1``
    beside ``"Outgoing"``.  Read the name.

    ``ServiceEndDate`` is a key only on a member who left: 7 of 106 rows.

    Example (truncated)::

        {"MemberID": 544, "MemberNumber": "H0402", "FullName": "Charlie Schmidt",
         "FirstName": "Charlie", "MiddleName": null, "LastName": "Schmidt",
         "RoomNumber": "916", "GABPhoneNumber": "(804) 698-1077",
         "ChamberCode": "H", "PartyCode": "D", "PartyName": "Democrat",
         "DistrictID": 77, "DistrictName": "77th", "MemberStatus": "Active",
         "PrefiledCount": 0, "NotPrefiledCount": 11, "LegislationTotal": 11,
         "ContactInformation": [{"ContactType": "GA", ...}]}
    """

    MemberID: int
    IdentityID: int | None = None
    MemberNumber: str | None = None  # e.g. "H0402"
    FullName: str | None = None  # first and last, no middle
    FirstName: str | None = None
    MiddleName: str | None = None
    LastName: str | None = None
    MemberDisplayName: str | None = None  # LIS pads this; LISModel strips it
    RoomNumber: str | None = None
    IsPublic: bool = True
    GABPhoneNumber: str | None = None  # two formats in one list
    GABEmailAddress: str | None = None
    ChamberCode: str | None = None
    Chamber: str | None = None  # "House" or "Senate"
    PartyCode: str | None = None
    PartyName: str | None = None  # "Democrat", "Republican"
    DistrictID: int | None = None
    DistrictName: str | None = None
    MemberStatusID: int | None = None  # unreliable; read MemberStatus
    MemberStatus: str | None = None
    ServiceBeginDate: datetime | None = None
    ServiceEndDate: datetime | None = None  # omitted unless the member left
    ServiceEndReason: str | None = None
    PrefiledCount: int | None = None  # a session total unless one member is named
    NotPrefiledCount: int | None = None  # the same
    LegislationTotal: int | None = None  # the same
    ContactInformation: list[MemberContactAddress] = []
