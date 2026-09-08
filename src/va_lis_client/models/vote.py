"""Vote models — per-member roll calls for floor and committee votes.

The ``/Vote`` service is absent from the LIS developer portal's service list,
but it is the only route to a per-member roll call.  A bill's events carry a
``VoteID``; that ID resolves here.

Two traps dominate this service:

- **A VoteID on a bill event is often not a roll call for that bill.**  Block
  votes cover many bills in one record, and voice votes record no members at
  all.  See :class:`Vote` for the fields that tell them apart.
- **``VoteStatement.VoteMemberID`` holds a ``MemberID``, not a
  ``VoteMemberID``.**  The join implied by the field name silently matches
  nothing.  See :class:`VoteStatement`.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class VoteMember(BaseModel):
    """One member's response on a vote.

    ``ResponseCode`` values::

        Y  yea
        N  nay
        A  abstain
        X  not voting

    ``"X"`` means the member did not vote, confirmed against a vote statement
    that reads "Delegate Knight was recorded as not voting" for an ``X`` row.
    **The tally string omits ``X``**, so summing these rows does not reproduce
    ``Vote.VoteTally``: House vote 294006 returns 100 members (64 Y, 34 N,
    2 X) against a tally of ``"(64-Y 34-N 0-A)"``.

    ``VotingSequence`` appears on committee votes and is absent on floor
    votes.  ``MemberDisplayName`` carries leading-space dirt from LIS (2 of
    100 rows on vote 294006), so prefer :attr:`name`.

    Example::

        {"VoteMemberID": 11186599, "MemberID": 509,
         "MemberDisplayName": "Lashrecse D. Aird",
         "PatronDisplayName": "Aird",
         "MemberNumber": "S0115", "ResponseCode": "Y"}
    """

    # Two distinct IDs, and they are not interchangeable.  ``VoteMemberID``
    # names this ballot and is new on every vote.  ``MemberID`` names the
    # person and holds across every vote and session.  ``VoteStatement``
    # has a field called ``VoteMemberID`` that actually carries ``MemberID``.
    VoteMemberID: int | None = None  # the ballot — e.g. 10988439
    MemberID: int | None = None  # the person — e.g. 217, stable
    MemberNumber: str | None = None  # e.g. "S0115", "H0353"
    MemberDisplayName: str | None = None  # may carry a leading space
    PatronDisplayName: str | None = None  # surname only
    ResponseCode: str | None = None  # "Y", "N", "A", "X"
    ProxyMemberID: int | None = None
    VotingSequence: int | None = None  # committee votes only

    @property
    def name(self) -> str:
        """``MemberDisplayName`` with the LIS leading-space dirt removed."""
        return (self.MemberDisplayName or "").strip()


class VoteLegislation(BaseModel):
    """A bill covered by a vote, and the event that vote produced.

    ``LegislationEventID`` is the back-link to the event whose ``VoteID``
    led here.  A block vote lists many of these — vote 300173 covers 50
    bills with one 40-member roll call.

    Example::

        {"LegislationEventID": 1582810, "VoteLegislationID": 331154,
         "LegislationID": 98525, "LegislationNumber": "HB1",
         "LegislationActionDescription":
             "Read third time and passed House (64-Y 34-N 0-A)"}
    """

    VoteLegislationID: int | None = None
    LegislationID: int | None = None
    LegislationNumber: str | None = None
    LegislationEventID: int | None = None
    LegislationActionDescription: str | None = None
    Description: str | None = None  # the bill's title
    VoteNumber: str | None = None
    MinutesEntryID: int | None = None
    VoteItems: list[dict] = []


class VoteStatement(BaseModel):
    """A correction to the recorded roll call.

    A member who was recorded wrongly files one of these.  The roll call
    itself is **not** amended, so a caller that reports voting records must
    surface these alongside the member rows.

    **``VoteMemberID`` on this model does not hold a ``VoteMemberID``.  It
    holds a ``MemberID``.**  Joining it to :attr:`VoteMember.VoteMemberID`
    matches nothing, returns an empty result, and raises no error.

    A :class:`VoteMember` row carries two distinct IDs: ``MemberID`` names the
    *person* and is stable across every vote, while ``VoteMemberID`` names the
    *ballot* and is new on every vote.  This field points at the person.

    Vote 294006 makes it concrete::

        statement rows        VoteMemberID = 17, 217
        member rows           VoteMemberID = 10988428 .. 10988537
                              MemberID     = 17, 217, ...

    The two ranges never overlap, so the wrong join fails silently::

        statement.VoteMemberID == member.VoteMemberID   # WRONG
        statement.VoteMemberID == member.MemberID       # right

    :meth:`~va_lis_client.service.BillVote.statements_by_member` applies the
    correct join.

    Example::

        {"VoteStatementID": 126, "VoteID": 294006, "VoteMemberID": 17,
         "VoteStatement":
             "Delegate Austin was recorded as yea. Intended to vote nay."}
    """

    VoteStatementID: int | None = None
    VoteID: int | None = None
    VoteMemberID: int | None = None
    VoteStatement: str | None = None
    ModificationDate: datetime | None = None


class VoteFile(BaseModel):
    """A published file for a vote (PDF or JSON).

    Note ``TextFormatID`` arrives as a **string** here (``"5"``), unlike
    :class:`~va_lis_client.models.calendar.CalendarFile`, where it is an int.

    Example::

        {"VoteFileID": 1114666, "VoteID": 294006,
         "FileURL": "https://lis.blob.core.windows.net/files/1114666.JSON",
         "TextFormatID": "5", "FileTypeID": 3, "FileType": "Vote"}
    """

    VoteFileID: int | None = None
    VoteID: int | None = None
    FileURL: str | None = None
    TextFormatID: str | None = None  # "1"=PDF, "5"=JSON — a string, not an int
    FileTypeID: int | None = None
    FileType: str | None = None
    IsGenerated: bool = False
    IsActive: bool = True


class VoteType(BaseModel):
    """Vote type reference from ``/Vote/api/getvotetypereferencesasync``.

    Three types::

        1 = Committee
        2 = Subcommittee
        3 = Floor

    Envelope key: ``VoteTypes``.
    """

    VoteTypeID: int
    Name: str | None = None


class Vote(BaseModel):
    """A vote record from ``/Vote/api/getvotebyidasync``.

    Key: ``VoteID``.  Reach one from ``LegislationEvent.VoteID``; no
    endpoint accepts a ``legislationID``, so events are the only bill-first
    way in.

    **Three traps decide whether this is a usable roll call:**

    - ``IsVoice`` — a voice vote records **no members**.  ``VoteMember`` is
      empty and ``VoteTally`` reads ``"(Voice Vote)"``.
    - ``IsBlock`` — one roll call disposing of many bills at once.  Vote
      300173 has 40 members and **50** entries in ``VoteLegislation``.  The
      members voted on the block, not on any one bill.
    - ``IsPublic`` — false on some block and voice votes.

    ``EventCode`` here can disagree with the originating event's own
    ``EventCode``: vote 294006 reports ``H9999`` while its event reports
    ``H5000``.  Join on ``VoteID`` alone.

    ``CommitteeID`` and ``CommitteeName`` are populated on committee votes
    and null on floor votes, which is the reliable floor/committee test
    alongside ``VoteTypeID``.

    Envelope key: ``Votes`` — a list holding exactly one vote.

    Example (truncated)::

        {"VoteID": 300418, "VoteNumber": "SV899", "ChamberCode": "S",
         "VoteType": "Floor", "VoteTypeID": 3,
         "VoteDate": "2026-03-04T13:44:15",
         "Description": "Passage  R", "VoteTally": "(21-Y 19-N 0-A)",
         "PassFail": "P", "IsVoice": false, "IsBlock": false,
         "VoteMember": [{"MemberNumber": "S0115", "ResponseCode": "Y"}],
         "VoteLegislation": [{"LegislationNumber": "HB1"}]}
    """

    VoteID: int
    VoteNumber: str | None = None  # e.g. "SV899", "H14V2610034"
    SessionID: int | None = None
    SessionCode: str | None = None
    VoteDate: datetime | None = None
    ChamberCode: str | None = None  # "H" or "S"
    VoteTypeID: int | None = None  # 1=Committee, 2=Subcommittee, 3=Floor
    VoteType: str | None = None
    # Committee fields — populated on committee votes, null on floor votes.
    CommitteeID: int | None = None
    CommitteeName: str | None = None
    CommitteeNumber: str | None = None
    ParentCommitteeID: int | None = None
    ParentCommitteeName: str | None = None
    ReferToCommitteeID: int | None = None
    ReferToCommitteeName: str | None = None
    ReferToCommitteeNumber: str | None = None
    Description: str | None = None
    VoteTally: str | None = None  # e.g. "(21-Y 19-N 0-A)", "(Voice Vote)"
    PassFail: str | None = None  # "P" or "F"
    IsUnanimous: bool = False
    # A voice vote records no members; a block vote covers many bills.
    IsVoice: bool = False
    IsBlock: bool = False
    IsPublic: bool = True
    # Disagrees with the originating event's EventCode — join on VoteID.
    EventCode: str | None = None
    VoteActionID: int | None = None
    VoteActionDescription: str | None = None
    VoteClassificationID: int | None = None
    ClassificationName: str | None = None
    CalendarCategoryTypeID: int | None = None
    LegislationActionDescription: str | None = None
    LegislationNumber: str | None = None  # null in practice
    ReferenceID: str | None = None
    ReferenceNumber: str | None = None
    VoteRoomID: int | None = None
    RoomDescription: str | None = None
    BatchNumber: str | None = None
    Sequence: int | None = None
    VoteComment: str | None = None
    # Aliased because the payload key ``VoteLegislation`` collides with the
    # model class of the same name, and a field named for its own class
    # shadows it inside the class body.
    vote_members: list[VoteMember] = Field(default=[], alias="VoteMember")
    vote_legislation: list[VoteLegislation] = Field(default=[], alias="VoteLegislation")
    VoteStatements: list[VoteStatement] = []
    VoteFiles: list[VoteFile] = []
