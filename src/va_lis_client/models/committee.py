"""Committee models."""

from datetime import datetime

from va_lis_client.models.common import LISModel


class CommitteeFile(LISModel):
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


class Committee(LISModel):
    """A legislative committee from ``/Committee/api/getcommitteelistasync``.

    Key: ``CommitteeID`` (surrogate PK) or ``CommitteeNumber`` (e.g. ``"H14"``).

    ``CommitteeNumber`` is chamber-prefixed: ``"H01"`` through ``"H24"`` for
    House, ``"S01"`` through ``"S13"`` for Senate.  A subcommittee number is
    its parent's number plus a three-digit sequence: ``"H01001"`` is the
    first subcommittee of ``H01``, and ``ParentCommitteeID`` names the parent.

    **The list ignores the session.**  Its cache key reads ``{SESSIONID=0}``
    whatever session code is sent, and 20251 and 20261 return the same 14
    House committees.  Seats change by session; the committees do not.

    **Subcommittee names are padded inside**, e.g. ``"HAPP     Sub:
    Commerce Agriculture & Natural Resources"``.  Read :attr:`name` for the
    collapsed form.  The list nulls ``EffectiveBeginDate``, ``MeetingNote``,
    and ``IsPublic``; the by-id and by-number calls fill them.

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

    @property
    def name(self) -> str:
        """``Name`` with inner runs of whitespace collapsed.

        :class:`LISModel` strips only the ends, and subcommittee names carry
        padding in the middle.
        """
        return " ".join((self.Name or "").split())

    @property
    def is_subcommittee(self) -> bool:
        """True when ``ParentCommitteeID`` names a parent."""
        return self.ParentCommitteeID is not None


class CommitteeMember(LISModel):
    """A member's seat on a committee.

    **Two shapes arrive.**  The roster from
    ``/MembersByCommittee/api/getcommitteememberslistasync`` names the role
    in ``CommitteeRoleTitle`` and adds ``Seniority``, ``AssignDate``, and
    ``RemoveDate``; it carries no ``PartyCode`` or ``CommitteeNumber``.  The
    seat nested in docket detail names the role in ``Title`` and carries
    ``PartyCode``.  Read :attr:`role` for the title on either shape.

    ``CommitteeRoleID`` is chamber specific: a House chair is ``3`` and a
    Senate chair is ``1``.  See :class:`CommitteeRole`.

    Roster example::

        {"CommitteeMemberID": 33424, "CommitteeID": 8, "MemberID": 186,
         "MemberNumber": "H0219", "MemberDisplayName": "Patrick A. Hope",
         "PatronDisplayName": "Hope", "VotingSequence": 1, "Seniority": 0,
         "CommitteeRoleID": 3, "CommitteeRoleTitle": "Chair",
         "AssignDate": "2010-01-13T00:00:00"}

    Docket example::

        {"CommitteeMemberID": 33672, "CommitteeID": 23,
         "CommitteeNumber": "S02", "MemberID": 114,
         "MemberNumber": "S0062",
         "MemberDisplayName": "R. Creigh Deeds",
         "Title": "Chair", "PartyCode": "D"}
    """

    CommitteeMemberID: int
    CommitteeID: int
    CommitteeNumber: str | None = None  # e.g. "S02"; docket shape only
    SessionCode: str | None = None  # null even on a session-scoped roster query
    MemberID: int
    MemberNumber: str | None = None  # e.g. "S0062"
    MemberDisplayName: str | None = None
    PatronDisplayName: str | None = None
    PartyCode: str | None = None  # "D", "R"; docket shape only
    VotingSequence: int | None = None
    DisplaySequence: int | None = None
    Seniority: int | None = None  # roster shape only
    CommitteeRoleID: int | None = None  # chamber specific; see CommitteeRole
    CommitteeRoleTitle: str | None = None  # "Chair", "Vice-Chair", "Member", ...; roster shape
    Title: str | None = None  # the same title; docket shape
    IsPublic: bool = True
    EffectiveDate: datetime | None = None
    AssignDate: datetime | None = None  # roster shape only
    RemoveDate: datetime | None = None  # roster shape only

    @property
    def role(self) -> str:
        """The seat's title on either shape, e.g. ``"Chair"``; ``""`` when absent."""
        return self.CommitteeRoleTitle or self.Title or ""


class CommitteeRole(LISModel):
    """A row of ``/MembersByCommittee/api/getcommitteerolesasync``.

    Eight rows, and the IDs are chamber specific, so join on the title
    rather than the ID when comparing across chambers:

    ==  ==========  =======
    ID  Title       Chamber
    ==  ==========  =======
    1   Chair       S
    2   Co-Chair    S
    3   Chair       H
    4   Vice-Chair  H
    5   Member      S
    6   Member      H
    7   Ex-Officio  H
    8   Ex-Officio  S
    ==  ==========  =======
    """

    CommitteeRoleID: int
    Title: str
    ChamberCode: str | None = None


class CommitteeAction(LISModel):
    """Committee action reference from
    ``/CommitteeLegislationReferral/api/getcommitteeactionreferencesasync``.

    The actions a committee can take on referred legislation (e.g. Reported,
    Passed by indefinitely, Referred to subcommittee), 41 rows.  This is the
    only data operation the ``CommitteeLegislationReferral`` service exposes;
    it does not list the bills referred to a committee.

    Example::

        {"CommitteeActionID": 1, "Description": "Referred to Committee",
         "EventCode": "01", "IsComplete": false}
    """

    CommitteeActionID: int
    Description: str | None = None
    EventCode: str | None = None
    IsComplete: bool = False
