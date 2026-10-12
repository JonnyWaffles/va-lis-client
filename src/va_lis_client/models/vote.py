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

What a block vote is
--------------------
A chamber does not vote on every bill separately.  It bundles the
uncontroversial ones and disposes of them in one motion, so members vote once
and that vote passes the whole bundle.  Vote 297750 recorded 99 members and
passed **105 bills** at 97-Y 0-N.

That lopsidedness is the signature: anything contested gets pulled out and
voted on alone, so a single session day carries both a 27-bill block at
98-Y 0-N and separate bills at 63-Y 35-N.

Every bill still gets its own ``LegislationEventID`` off the shared vote, so a
bill's history reads like an ordinary passage while its ``VoteID`` is shared
with a hundred others.  **The description text will not tell you.**  Only 9 of
vote 297750's 105 bill events say "Block Vote"; the other 96 read exactly like
an individual passage.  Test ``IsBlock``.

The practical point: crediting a member with a position on one bill out of a
105-bill unanimous bundle misstates the record.  That is why
:attr:`~va_lis_client.service.BillVote.is_roll_call` rejects them.
"""

from datetime import datetime

from pydantic import Field

from va_lis_client.models.common import LISModel


class VoteMember(LISModel):
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
    votes.  LIS pads ``MemberDisplayName`` on some rows (2 of 100 on vote
    294006); :class:`~va_lis_client.models.common.LISModel` strips it, so
    :attr:`name` is shorthand rather than a repair.

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
    MemberDisplayName: str | None = None  # LIS pads this; LISModel strips it
    PatronDisplayName: str | None = None  # surname only
    ResponseCode: str | None = None  # "Y", "N", "A", "X"
    ProxyMemberID: int | None = None
    VotingSequence: int | None = None  # committee votes only

    @property
    def name(self) -> str:
        """``MemberDisplayName``, never ``None``."""
        return self.MemberDisplayName or ""


class VoteLegislation(LISModel):
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


class VoteStatement(LISModel):
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


class VoteFile(LISModel):
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


class VoteType(LISModel):
    """Vote type reference from ``/Vote/api/getvotetypereferencesasync``.

    Three types::

        1 = Committee
        2 = Subcommittee
        3 = Floor

    Envelope key: ``VoteTypes``.
    """

    VoteTypeID: int
    Name: str | None = None


class Vote(LISModel):
    """A vote record from ``/Vote/api/getvotebyidasync``.

    Key: ``VoteID``.  Reach one from ``LegislationEvent.VoteID``, or from a
    :class:`ShallowVote`, which is the list of a bill's votes without their
    members.  This endpoint takes a ``voteID`` and nothing else.

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


class ShallowVote(LISModel):
    """A vote without its members, from ``/Vote/api/getshallowvoteasync``.

    Key: ``VoteID``, which resolves to the full :class:`Vote`.

    This is the list form of the vote service: one row per vote with its
    tally, type and committee, and no roll call.  It is the one vote endpoint
    that filters by bill, by member, and by date.

    **``IsBlock`` is false on every row, block votes included.**  Vote 300173
    is a 50 bill block: its full record says ``IsBlock: true``, and its
    shallow row says ``IsBlock: false``.  The shallow row marks a block in
    ``LegislationNumber`` instead, with the literal string ``"BLOCK"``.  Read
    :attr:`is_block`, which tests both.

    **Voice votes are mostly missing.**  Across five bills in 20261 the
    events named nine voice votes and this list returned one of them.  A bill
    whose only votes were voice votes answers 204: HB9 has two on its events
    and none here (measured 2026-10-12).  Every roll call and block vote on
    those five bills' events was here, and nothing was here that the events
    lacked.

    ``LegislationNumber`` is null on a vote about no bill, such as a quorum
    call, whose ``ClassificationName`` is ``"Attendance"``.

    ``PassFail``, ``CommitteeNumber``, ``Sequence``, ``VoteRoomID``,
    ``RoomDescription``, ``BatchNumber``, ``ReferenceID``,
    ``ReferenceNumber``, ``CalendarCategoryTypeID`` and ``VoteComment`` were
    null on all 1,911 rows of one member's session.  The outcome is in
    ``VoteTally`` and ``Description``.  ``ReferToCommitteeName`` is a key
    only on a vote that refers the bill onward.

    Example (truncated)::

        {"VoteID": 291609, "ChamberCode": "H", "CommitteeID": 14,
         "CommitteeName": "Labor and Commerce", "LegislationNumber": "HB1",
         "VoteNumber": "H14V2610034", "IsVoice": false, "IsBlock": false,
         "SessionID": 59, "SessionCode": "20261",
         "VoteDate": "2026-01-20T12:30:00", "VoteTypeID": 1,
         "VoteType": "Committee", "VoteTally": "(15-Y 7-N)",
         "EventCode": "H1405", "ClassificationName": "Legislation",
         "ReferToCommitteeID": 2, "ReferToCommitteeNumber": "H02"}
    """

    VoteID: int
    VoteNumber: str | None = None  # e.g. "H14V2610034", "SV899", "2619916"
    SessionID: int | None = None
    SessionCode: str | None = None
    VoteDate: datetime | None = None
    ChamberCode: str | None = None  # the chamber that voted
    VoteTypeID: int | None = None  # 1=Committee, 2=Subcommittee, 3=Floor
    VoteType: str | None = None
    CommitteeID: int | None = None  # null on a floor vote
    CommitteeName: str | None = None
    CommitteeNumber: str | None = None  # null on every observed row
    LegislationNumber: str | None = None  # a bill number, "BLOCK", or null
    Description: str | None = None
    VoteTally: str | None = None  # e.g. "(15-Y 7-N)"
    PassFail: str | None = None  # null on every observed row
    IsVoice: bool = False
    IsBlock: bool = False  # false even on a block vote; read is_block
    IsPublic: bool = True
    EventCode: str | None = None
    VoteActionID: int | None = None
    VoteActionDescription: str | None = None
    VoteClassificationID: int | None = None
    ClassificationName: str | None = None  # "Legislation", "Attendance", or null
    ReferToCommitteeID: int | None = None
    ReferToCommitteeNumber: str | None = None
    ReferToCommitteeName: str | None = None  # omitted unless the vote refers
    CalendarCategoryTypeID: int | None = None
    ReferenceID: str | None = None
    ReferenceNumber: str | None = None
    VoteRoomID: int | None = None
    RoomDescription: str | None = None
    BatchNumber: str | None = None
    Sequence: int | None = None
    VoteComment: str | None = None

    @property
    def is_block(self) -> bool:
        """True when this vote disposed of many bills at once.

        ``IsBlock`` alone never says so on this endpoint, so this also reads
        the ``"BLOCK"`` that LIS writes where a bill number belongs.
        """
        return self.IsBlock or (self.LegislationNumber or "").upper() == "BLOCK"


class VoteLink(LISModel):
    """The vote behind one bill event, from ``/Vote/api/getvotelinksasync``.

    Key: ``VoteID``.  Asked by ``LegislationEventID``.

    The cheap answer to "which vote is this event's": 290 bytes, where the
    full vote is a roster.  It adds two things the event lacks, the
    ``VoteNumber`` and the ``VoteFileID`` of the vote sheet.  An event with
    no vote answers 204.

    **``ReferenceID`` is not one thing.**  On a floor vote it equalled the
    ``VoteID`` (294006); on a committee vote it was ``2``, that vote's
    ``ReferToCommitteeID``.  Do not join on it.

    ``CommitteeID`` is null on a floor vote.  The spec lists a ``FileURL``
    that LIS does not send.

    Example::

        {"VoteID": 291609, "VoteNumber": "H14V2610034", "ChamberCode": "H",
         "CommitteeID": 14, "ParentCommitteeID": null, "VoteFileID": 1092426,
         "ReferenceType": "Vote", "ReferenceID": 2, "SessionID": 59,
         "SessionCode": "20261", "IsPublic": true}
    """

    VoteID: int
    VoteNumber: str | None = None
    ChamberCode: str | None = None
    CommitteeID: int | None = None  # null on a floor vote
    ParentCommitteeID: int | None = None
    VoteFileID: int | None = None
    ReferenceType: str | None = None  # "Vote"
    ReferenceID: int | None = None  # the VoteID on a floor vote, not on a committee one
    SessionID: int | None = None
    SessionCode: str | None = None
    IsPublic: bool = True
    FileURL: str | None = None  # in the spec, never sent
