"""The member-first view of voting, from ``/MemberVoteSearch/api/``.

:class:`~va_lis_client.models.vote.Vote` answers "who voted on this bill".
This module answers the other direction: "how did this member vote on
everything".  The two axes agree — member 503's HB1 rows carry the same
``VoteID`` values that a bill-first lookup returns.

**A row is one (vote, bill) pair, not one vote.**  Member 503's 2026 history
holds 2,910 rows over only 2,293 distinct ``VoteID`` values, because a block
vote repeats once per bill it disposed of.  See :class:`MemberVoteResult`.
"""

from datetime import datetime

from pydantic import Field

from va_lis_client.models.common import LISModel
from va_lis_client.models.vote import VoteStatement


class MemberVoteResult(LISModel):
    """One member's response, on one vote, against one bill.

    From ``/MemberVoteSearch/api/getmembervotelistasync``, nested under
    ``MemberVoteList[0].VoteResult``.

    **A row is a (vote, bill) pair.**  A block vote repeats, once per bill:
    ``VoteID`` 297750 appears 105 times in member 503's 2026 history, one row
    for each bill that single House vote passed.  Counting rows overstates how
    many times a member voted; count distinct ``VoteID`` for that.  This
    endpoint carries no ``IsBlock`` flag, so
    :class:`~va_lis_client.service.MemberVote` derives one by counting the
    rows that share a ``VoteID``.

    **``ClassificationName`` is not the legislation filter.**  It takes three
    values in member 503's 2026 history, and only one of them means "not a
    bill"::

        Legislation   2179 rows   floor votes on bills
        None           688 rows   committee and subcommittee votes on bills
        Attendance      43 rows   quorum roll calls, no bill

    All 688 null rows carry a ``LegislationNumber`` and all 43 attendance rows
    do not, so **filter on ``LegislationNumber``**.  Filtering on
    ``ClassificationName == "Legislation"`` silently drops every committee
    vote the member cast.

    ``VoteStatements`` holds this member's corrections on that vote, and the
    ``VoteMemberID`` inside carries a ``MemberID`` — the same misnaming
    documented on :class:`~va_lis_client.models.vote.VoteStatement`.

    Example (truncated)::

        {"VoteID": 294006, "VoteDate": "2026-02-03T00:00:00",
         "ChamberCode": "H", "VoteType": "Floor", "VoteTypeID": 3,
         "LegislationID": 98525, "LegislationNumber": "HB1",
         "ResponseCode": "Y", "PassFail": "P",
         "ClassificationName": "Legislation",
         "VoteDescription": "Read third time and passed House (64-Y 34-N 0-A)"}
    """

    VoteID: int
    VoteNumber: str | None = None
    VoteDate: datetime | None = None
    SessionID: int | None = None
    ChamberCode: str | None = None  # "H" or "S"
    VoteTypeID: int | None = None  # 1=Committee, 2=Subcommittee, 3=Floor
    VoteType: str | None = None
    # Populated on committee and subcommittee votes, null on the floor.
    CommitteeID: int | None = None
    CommitteeName: str | None = None
    # The bill this row is about.  Null only on attendance roll calls, which
    # is the reliable test — see the class docstring.
    LegislationID: int | None = None
    LegislationNumber: str | None = None  # e.g. "HB1"
    LegislationDescription: str | None = None
    VoteLegislationID: int | None = None
    ResponseCode: str | None = None  # "Y", "N", "A", "X"
    PassFail: str | None = None  # the vote's outcome, not the member's
    VoteDescription: str | None = None
    ActionDescription: str | None = None
    VoteClassificationID: int | None = None
    # "Legislation", "Attendance", or null.  Null still means a bill.
    ClassificationName: str | None = None
    VoteActionID: int | None = None
    BatchNumber: str | None = None
    Sequence: int | None = None
    # Aliased because the payload key ``VoteStatement`` collides with the
    # model class of the same name.  A field named for its own class shadows
    # it inside the class body, which silently turns the annotation below
    # into ``list[str | None]`` — and makes linters read the import as unused.
    vote_statement: str | None = Field(default=None, alias="VoteStatement")
    VoteStatements: list[VoteStatement] = []
