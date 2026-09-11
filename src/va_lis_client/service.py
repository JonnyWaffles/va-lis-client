"""Identifier resolution, reference joins, and text extraction over the client.

:class:`LISClient` is a transport layer: one method per LIS endpoint and no
interpretation.  :class:`LISService` is the layer above it.  It answers the
questions the raw endpoints leave to every caller:

- LIS keys bills by ``LegislationID``, but people say ``HB1``.  Numbers must
  also be unpadded, so ``hb0001`` has to become ``HB1`` before it is sent.
- Most bill endpoints key on ``LegislationID``, but the text list endpoint
  takes a number and hands the ID back, so resolving one costs about 1.3 KB.
  The session bill list is the fallback, and the only route for search.  It
  runs to roughly 3 MB, so it needs caching.
- Events come back with ``LegislationEventTypeID`` and ``LegislationStatusID``
  null, so the reference vocabularies join on ``EventCode`` and status
  ``Name`` instead.  ``EventCode`` alone is not unique, so resolving one event
  to one vocabulary row takes the bill's chamber and ``IsPassed`` as well.
- Bill text spans two endpoints whose rows match on ``LegislationTextID``,
  and the body arrives as HTML.
- Votes have no bill-first endpoint.  Reaching a bill's roll calls means
  walking its events for ``VoteID`` and fetching each one.
- A ballot names a member by ``MemberID`` and nothing else, so party and
  district take a second join against the session roster.
- The member-first vote endpoint returns one row per (vote, bill) pair and
  omits the block flag, so the service derives it by counting the rows that
  share a ``VoteID``.

Usage::

    from va_lis_client import LISClient, LISService, strip_html

    service = LISService(LISClient())

    bill = service.get_bill("hb0001", 20261)         # normalizes, then resolves
    item, detail = service.bill_text("HB1", 20261)   # newest version
    print(strip_html(detail.DraftText))

A service owns its caches, so build one and keep it.  Instances are safe to
share between threads.
"""

from __future__ import annotations

import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser

from va_lis_client.client import LISClient
from va_lis_client.exceptions import (
    BillNotFoundError,
    InvalidBillNumberError,
    SessionNotFoundError,
    TextVersionNotFoundError,
)
from va_lis_client.models import (
    Legislation,
    LegislationEvent,
    LegislationEventType,
    LegislationStatus,
    LegislationSummaryItem,
    LegislationTextDetail,
    LegislationTextItem,
    Member,
    MemberLegislation,
    MemberVoteResult,
    Patron,
    Vote,
    VoteMember,
    VoteStatement,
)

DEFAULT_BILL_LIST_TTL = 15 * 60
DEFAULT_ROSTER_TTL = 60 * 60

_BILL_NUMBER_RE = re.compile(r"([A-Z]+)(\d+)")


@dataclass(frozen=True)
class BillVote:
    """One bill action paired with the vote record it produced.

    :meth:`LISService.bill_votes` returns these.  ``event`` is the bill's own
    action; ``vote`` is the roll call that ``event.VoteID`` resolves to.

    Check :attr:`is_roll_call` before you attribute the member rows to this
    bill.  A voice vote records nobody, and a block vote records a chamber
    disposing of many bills at once.
    """

    event: LegislationEvent
    vote: Vote

    @property
    def is_roll_call(self) -> bool:
        """True when the members voted on this bill and this bill alone.

        False for a voice vote (no members recorded) and for a block vote
        (the members voted on a bundle, so crediting them on this bill
        misstates the record).
        """
        return not self.vote.IsVoice and not self.vote.IsBlock and bool(self.vote.vote_members)

    @property
    def is_committee(self) -> bool:
        """True for a committee or subcommittee vote, false on the floor."""
        return self.vote.CommitteeID is not None

    @property
    def chamber(self) -> str | None:
        """``"H"`` or ``"S"`` — the chamber that voted, not the bill's."""
        return self.vote.ChamberCode or self.event.ChamberCode

    @property
    def bill_count(self) -> int:
        """How many bills this one vote disposed of.  Above 1 means a block."""
        return len(self.vote.vote_legislation)

    def responses(self) -> dict[str, list[VoteMember]]:
        """Members grouped by ``ResponseCode``.

        The codes are ``"Y"`` yea, ``"N"`` nay, ``"A"`` abstain, and ``"X"``
        not voting.  ``"X"`` is absent from ``Vote.VoteTally``, so these
        groups do not sum to the tally string.  Members with no response code
        are omitted.

        Groups arrive in the order LIS lists the members, which is
        alphabetical on floor votes and by seniority on committee votes.
        """
        grouped: dict[str, list[VoteMember]] = {}
        for member in self.vote.vote_members:
            if member.ResponseCode:
                grouped.setdefault(member.ResponseCode, []).append(member)

        return grouped

    def statements_by_member(self) -> dict[int, list[VoteStatement]]:
        """Record corrections, keyed by the ``MemberID`` they concern.

        A member recorded wrongly files a statement, and **the roll call is
        never amended**.  Vote 294006 records Delegate Knight as ``"X"`` and
        carries a statement reading "Delegate Knight was recorded as not
        voting. Intended to vote nay."  Both are true; only the first counts.

        ``VoteStatement.VoteMemberID`` is misnamed and holds a ``MemberID``,
        so this keys on :attr:`VoteMember.MemberID`.  Statements with no ID
        are omitted.

        Returns:
            ``MemberID`` to that member's statements on this vote.
        """
        keyed: dict[int, list[VoteStatement]] = {}
        for statement in self.vote.VoteStatements:
            # Do NOT match this against VoteMember.VoteMemberID.  Despite the
            # name, the value is a MemberID; that join silently matches
            # nothing.  See VoteStatement for the numbers.
            if statement.VoteMemberID is not None:
                keyed.setdefault(statement.VoteMemberID, []).append(statement)

        return keyed


@dataclass(frozen=True)
class RollCallEntry:
    """One member's vote, with the roster record that names and places them.

    :meth:`LISService.roll_call` returns these.  A ballot from LIS carries a
    ``MemberID`` and a display name and nothing else, so this pairs it with
    the member's roster row for party and district.

    ``vote`` is carried on every entry, because one bill has many votes.
    Group on ``entry.vote.VoteID`` when you want them separated.
    """

    vote: Vote
    member: Member
    ballot: VoteMember

    @property
    def response(self) -> str | None:
        """``"Y"`` yea, ``"N"`` nay, ``"A"`` abstain, or ``"X"`` not voting."""
        return self.ballot.ResponseCode

    @property
    def name(self) -> str:
        """The member's display name."""
        return self.member.name

    @property
    def party(self) -> str | None:
        """``"D"``, ``"I"``, or ``"R"``."""
        return self.member.PartyCode

    @property
    def district(self) -> str | None:
        """The district label, e.g. ``"71st"``."""
        return self.member.DistrictName


@dataclass(frozen=True)
class MemberVote:
    """One member's vote on one bill, with the block fan-out worked out.

    :meth:`LISService.member_votes` returns these.  ``result`` is the raw row;
    ``bills_in_vote`` counts how many bills that single ``VoteID`` disposed of.

    ``/MemberVoteSearch`` carries no ``IsBlock`` flag, unlike
    :class:`~va_lis_client.models.vote.Vote`, so the service counts the rows
    sharing a ``VoteID`` to recover it.
    """

    result: MemberVoteResult
    bills_in_vote: int

    @property
    def is_block(self) -> bool:
        """True when this vote disposed of more than one bill at once.

        A block vote is a real vote the member cast, but it is not a verdict
        on this bill in particular, so do not read it as one.
        """
        return self.bills_in_vote > 1

    @property
    def is_committee(self) -> bool:
        """True for a committee or subcommittee vote, false on the floor."""
        return self.result.CommitteeID is not None

    @property
    def response(self) -> str | None:
        """``"Y"`` yea, ``"N"`` nay, ``"A"`` abstain, or ``"X"`` not voting."""
        return self.result.ResponseCode

    @property
    def bill_number(self) -> str | None:
        """e.g. ``"HB1"``.  Never ``None`` on a legislation row."""
        return self.result.LegislationNumber


class LISService:
    """Resolution and reference joins over a :class:`LISClient`.

    Args:
        client: The transport client to call.  Omit it to build a
            :class:`LISClient` from the ``LIS_API_KEY`` environment variable.
        bill_list_ttl: Seconds to keep a session's bill list.  Bills change
            through a session, so this list expires.  The event type and
            status vocabularies are static, so the service keeps them for its
            whole life.
        roster_ttl: Seconds to keep a session's member roster.  A roster
            changes a handful of times a session, when a member resigns or
            arrives, so it expires far more slowly than the bill list.

    Caches live on the instance, not on the module, so two services built with
    different API keys never share data.
    """

    def __init__(
        self,
        client: LISClient | None = None,
        *,
        bill_list_ttl: float = DEFAULT_BILL_LIST_TTL,
        roster_ttl: float = DEFAULT_ROSTER_TTL,
    ):
        self.client = client if client is not None else LISClient()
        self.bill_list_ttl = bill_list_ttl
        self.roster_ttl = roster_ttl

        self._bill_lists: dict[int, tuple[float, list[LegislationSummaryItem]]] = {}
        self._rosters: dict[int, tuple[float, dict[int, Member]]] = {}
        self._member_votes: dict[tuple[int, int], tuple[float, list[MemberVoteResult]]] = {}
        self._member_bills: dict[
            tuple[int, int, int | None], tuple[float, list[MemberLegislation]]
        ] = {}
        self._session_ids: dict[int, int] | None = None
        self._event_types: dict[str, list[LegislationEventType]] | None = None
        self._statuses_by_name: dict[str, LegislationStatus] | None = None
        self._statuses_by_id: dict[int, LegislationStatus] | None = None

        # One lock per session code, so a cold fetch for one session does not
        # block a fetch for another.  ``_guard`` protects the lock table
        # itself; ``_reference_lock`` covers the static vocabularies.
        self._bill_list_locks: dict[int, threading.Lock] = {}
        self._roster_locks: dict[int, threading.Lock] = {}
        self._member_vote_locks: dict[tuple[int, int], threading.Lock] = {}
        self._member_bill_locks: dict[tuple[int, int, int | None], threading.Lock] = {}
        self._guard = threading.Lock()
        self._reference_lock = threading.Lock()

    def session_bills(
        self,
        session_code: int,
        *,
        refresh: bool = False,
    ) -> list[LegislationSummaryItem]:
        """Every bill in a session, cached for ``bill_list_ttl`` seconds.

        This is the list that :meth:`resolve_bill` searches.  A regular
        session runs to roughly 3 MB, so keep one service instance rather
        than building one per request.

        This always fetches the whole list, because resolving a bill number
        means scanning every row.  Call :meth:`LISClient.get_session_bills`
        with ``page_size`` directly when you only want one window.

        Args:
            session_code: e.g. ``20261`` for the 2026 Regular Session.
            refresh: Fetch again even when a fresh copy is cached.
        """
        if not refresh:
            cached = self._read_bill_list(session_code)
            if cached is not None:
                return cached

        with self._bill_list_lock(session_code):
            # Another thread may have filled the slot while this one waited.
            if not refresh:
                cached = self._read_bill_list(session_code)
                if cached is not None:
                    return cached

            bills = self.client.get_session_bills(session_code=session_code)
            self._bill_lists[session_code] = (time.monotonic(), bills)
            return bills

    def resolve_bill(self, bill_number: str, session_code: int) -> LegislationSummaryItem:
        """Find a bill's session record, and with it the ``LegislationID``.

        A bill's identity is ``(session_code, bill_number)``.  The numeric ID
        alone is ambiguous, because carry-over reuses it across the even-year
        and odd-year sessions of a term.

        Args:
            bill_number: Any case, padded or not.  ``hb0001`` resolves ``HB1``.
            session_code: e.g. ``20261``.

        Raises:
            InvalidBillNumberError: The string does not parse as a bill number.
            BillNotFoundError: The session's bill list holds no such number.
        """
        number = normalize_bill_number(bill_number)

        for item in self.session_bills(session_code):
            if item.LegislationNumber.upper() == number:
                return item

        raise BillNotFoundError(
            f"Bill {number} is not in session {session_code}. Check the bill number "
            f"and the session code (20261 = 2026 Regular Session)."
        )

    def resolve_bill_id(self, bill_number: str, session_code: int) -> int:
        """The ``LegislationID`` for a bill, usually without the session list.

        The text list endpoint accepts a bill number and returns rows carrying
        the ID, which makes it a cheap lookup: roughly 1.3 KB against the 3 MB
        a session list costs.  It is session scoped, so a bill absent from the
        session returns nothing rather than a neighbouring session's record
        (verified 2026-08-28: HB1 has three text versions in 20261 and none in
        20271).

        A bill with no published text falls back to :meth:`resolve_bill` and
        the full list.

        Use :meth:`resolve_bill` instead when you want the session row itself,
        for its status, description, or chief patron.

        Raises:
            InvalidBillNumberError: The string does not parse as a bill number.
            BillNotFoundError: Neither the text list nor the session's bill
                list holds that number.
        """
        number = normalize_bill_number(bill_number)

        texts = self.client.get_bill_texts(
            legislation_number=number,
            session_code=session_code,
        )
        if texts:
            return texts[0].LegislationID

        return self.resolve_bill(number, session_code).LegislationID

    def get_bill(self, bill_number: str, session_code: int) -> Legislation:
        """Full bill detail, addressed the way people address a bill.

        This resolves the ID with :meth:`resolve_bill_id`, then fetches the
        detail record.  A bill with published text therefore costs about
        1.3 KB to resolve rather than a 3 MB session list.

        Raises:
            InvalidBillNumberError: The string does not parse as a bill number.
            BillNotFoundError: The bill is not in the session, or LIS holds no
                detail record for it.
        """
        number = normalize_bill_number(bill_number)
        legislation_id = self.resolve_bill_id(number, session_code)
        bill = self.client.get_bill(legislation_id)

        if bill is None:
            raise BillNotFoundError(
                f"Bill {number} (LegislationID {legislation_id}) has no detail record in LIS."
            )

        return bill

    def bill_text_versions(
        self,
        bill_number: str,
        session_code: int,
    ) -> list[LegislationTextItem]:
        """Every published text version of a bill.

        Each item carries a ``DocumentCode``: ``HB1`` is the introduced text,
        ``HB1ER`` the enrolled text, ``HB1S1`` the first substitute.  Pass one
        of those codes to :meth:`bill_text`.
        """
        number = normalize_bill_number(bill_number)

        return self.client.get_bill_texts(
            legislation_number=number,
            session_code=session_code,
        )

    def bill_text(
        self,
        bill_number: str,
        session_code: int,
        document_code: str | None = None,
        *,
        versions: list[LegislationTextItem] | None = None,
    ) -> tuple[LegislationTextItem, LegislationTextDetail]:
        """One text version of a bill, with its body.

        The body is HTML in ``detail.DraftText``.  Pass it through
        :func:`strip_html` for plain text.

        Args:
            bill_number: Any case, padded or not.
            session_code: e.g. ``20261``.
            document_code: A version to select, e.g. ``"HB1ER"``.  The default
                is the most recent version.
            versions: A version list from :meth:`bill_text_versions`, to reuse
                instead of fetching it again.

        Returns:
            The selected version item and its detail record.

        Raises:
            TextVersionNotFoundError: The bill has no text at all, no version
                with that document code, or no body on the selected version.
        """
        number = normalize_bill_number(bill_number)
        texts = versions if versions is not None else self.bill_text_versions(number, session_code)

        if not texts:
            raise TextVersionNotFoundError(
                f"No text versions exist for {number} in session {session_code}."
            )

        selected = pick_text_version(texts, document_code)

        # The detail endpoint returns every version at once, so match the row
        # back to the selected version on its text id.
        details = self.client.get_bill_text_detail(
            legislation_id=selected.LegislationID,
            session_code=session_code,
        )
        detail = next(
            (d for d in details if d.LegislationTextID == selected.LegislationTextID),
            None,
        )

        if detail is None or not detail.DraftText:
            raise TextVersionNotFoundError(
                f"LIS returned no text body for {selected.DocumentCode} in session {session_code}."
            )

        return selected, detail

    def bill_votes(
        self,
        bill_number: str,
        session_code: int,
        *,
        roll_calls_only: bool = False,
    ) -> list[BillVote]:
        """Every recorded vote on a bill, in both chambers, with the members.

        This walks the bill's events, takes each ``VoteID``, and fetches the
        roll call behind it.  Events are the only bill-first route to votes,
        because no vote endpoint accepts a ``legislationID``.

        Committee, subcommittee, and floor votes all come back, from whichever
        chamber cast them.  HB1 in session 20261 returns 8: two House
        committee votes, one House floor vote, two Senate committee votes, and
        three Senate floor votes.

        **Not every result is a roll call for this bill.**  A voice vote
        records no members, and a block vote records one roster disposing of
        many bills.  Read :attr:`BillVote.is_roll_call`, or pass
        ``roll_calls_only=True`` to drop them here.

        This costs one request per vote, so a heavily amended bill costs a
        dozen.  Results are not cached, because votes accrue through a session.

        Args:
            bill_number: Any case, padded or not.  ``hb0001`` resolves ``HB1``.
            session_code: e.g. ``20261``.
            roll_calls_only: Return only votes that record members for this
                bill alone.

        Returns:
            :class:`BillVote` records in event order, so chronological.

        Raises:
            InvalidBillNumberError: The string does not parse as a bill number.
            BillNotFoundError: The bill is not in that session.
        """
        legislation_id = self.resolve_bill_id(bill_number, session_code)

        results = []
        for event in self.client.get_bill_events(legislation_id=legislation_id):
            if not event.VoteID:
                continue

            vote = self.client.get_vote(event.VoteID)
            if vote is None:
                continue

            record = BillVote(event=event, vote=vote)
            if roll_calls_only and not record.is_roll_call:
                continue

            results.append(record)

        return results

    def roll_call(
        self,
        bill_number: str,
        session_code: int,
        *,
        vote_id: int | None = None,
    ) -> list[RollCallEntry]:
        """Who voted which way on a bill, with party and district attached.

        This is :meth:`bill_votes` with the roster joined on.  It returns one
        entry per member per vote, in the order LIS lists them, and each entry
        carries its own ``vote`` because a bill has many.

        **Only attributable roll calls are included.**  Voice votes record no
        members, and a block vote records a chamber disposing of many bills at
        once, so naming a member on either misstates the record.  Use
        :meth:`bill_votes` when you want every vote including those.

        The roster costs one extra request per session, cached for
        ``roster_ttl``.  Members who left mid-session stay in it, so a vote
        cast in January still resolves after a February resignation.

        Args:
            bill_number: Any case, padded or not.  ``hb0001`` resolves ``HB1``.
            session_code: e.g. ``20261``.
            vote_id: Narrow to a single vote.  Omit for every roll call.

        Returns:
            :class:`RollCallEntry` records.  Empty when a ballot names a
            member absent from the roster, which does not happen in practice:
            all 100 ballots on House vote 294006 resolve.
        """
        roster = self.members_by_id(session_code)

        entries = []
        for record in self.bill_votes(bill_number, session_code, roll_calls_only=True):
            if vote_id is not None and record.vote.VoteID != vote_id:
                continue

            for ballot in record.vote.vote_members:
                member = roster.get(ballot.MemberID)
                if member is None:
                    continue

                entries.append(RollCallEntry(vote=record.vote, member=member, ballot=ballot))

        return entries

    def member_votes(
        self,
        member_id: int,
        session_code: int,
        *,
        legislation_only: bool = True,
        refresh: bool = False,
    ) -> list[MemberVote]:
        """Every vote one member cast in a session, in LIS order.

        This is the member-first axis, where :meth:`roll_call` is the
        bill-first one.  The two agree on the same ``VoteID`` values.

        Each entry knows how many bills its vote covered, because the endpoint
        omits the ``IsBlock`` flag that :class:`~va_lis_client.models.vote.Vote`
        carries.  Read :attr:`MemberVote.is_block` before treating a row as the
        member's verdict on that one bill: member 503's 2026 history includes a
        single House vote that passed 105 bills at once.

        **A row is a (vote, bill) pair.**  ``len(...)`` counts bill positions,
        not votes.  Count distinct ``result.VoteID`` for votes cast.

        The response runs to roughly 1.9 MB, so it is cached for ``roster_ttl``
        per member and session.

        Args:
            member_id: ``MemberID`` from the roster or a ballot.
            session_code: e.g. ``20261``.
            legislation_only: Drop the attendance roll calls, which carry no
                bill.  This filters on ``LegislationNumber``, because
                ``ClassificationName`` is null on every committee vote.
            refresh: Fetch again even when a fresh copy is cached.
        """
        rows = self._member_vote_rows(member_id, session_code, refresh=refresh)

        fan_out = Counter(r.VoteID for r in rows)

        votes = []
        for row in rows:
            if legislation_only and not row.LegislationNumber:
                continue

            votes.append(MemberVote(result=row, bills_in_vote=fan_out[row.VoteID]))

        return votes

    def member_votes_on(
        self,
        member_id: int,
        bill_number: str,
        session_code: int,
    ) -> list[MemberVote]:
        """How one member voted on one bill, across every vote it saw.

        The intersection of the two axes.  A member sees only the votes their
        own chamber and committees cast, so a delegate returns nothing for a
        Senate-only vote.

        Args:
            member_id: ``MemberID`` from the roster or a ballot.
            bill_number: Any case, padded or not.  ``hb0001`` resolves ``HB1``.
            session_code: e.g. ``20261``.

        Raises:
            InvalidBillNumberError: The string does not parse as a bill number.
        """
        number = normalize_bill_number(bill_number)

        return [
            v
            for v in self.member_votes(member_id, session_code)
            if (v.result.LegislationNumber or "").upper() == number
        ]

    def members_by_id(
        self,
        session_code: int,
        *,
        refresh: bool = False,
    ) -> dict[int, Member]:
        """The session's roster, keyed by ``MemberID``, cached for ``roster_ttl``.

        ``MemberID`` is the join key from a ballot: every
        :attr:`VoteMember.MemberID` on a roll call resolves here.

        **Do not filter this on ``MemberStatus``.**  The roster deliberately
        holds members who left or arrived mid-session, and they cast the votes
        you are attributing.  Session 20261 carries 148 rows for 140 seats.

        This reads the roster list rather than calling
        :meth:`LISClient.get_member` per member, which answers 204 for 9 of
        those 148.

        Args:
            session_code: e.g. ``20261``.
            refresh: Fetch again even when a fresh copy is cached.
        """
        if not refresh:
            cached = self._read_roster(session_code)
            if cached is not None:
                return cached

        with self._roster_lock(session_code):
            # Another thread may have filled the slot while this one waited.
            if not refresh:
                cached = self._read_roster(session_code)
                if cached is not None:
                    return cached

            members = self.client.get_members(session_code=session_code)
            roster = {m.MemberID: m for m in members}
            self._rosters[session_code] = (time.monotonic(), roster)
            return roster

    def find_members(self, name: str, session_code: int) -> list[Member]:
        """Roster rows whose name contains ``name``, ignoring case.

        The search runs over the display name, the list name (``"Schmidt,
        Charlie"``), and the surname, so ``"schmidt"``, ``"Charlie Schmidt"``,
        and ``"Schmidt, C"`` all find Delegate Schmidt.  Runs of whitespace
        collapse before matching.  Results come back in list-name order.

        This reads the cached roster; it costs nothing after the first call
        for a session.  It includes members who left mid-session, like the
        roster itself.

        Args:
            name: Any part of the name.  Blank returns nothing rather than
                everyone.
            session_code: e.g. ``20261``.
        """
        needle = " ".join(name.split()).lower()
        if not needle:
            return []

        hits = []
        for member in self.members_by_id(session_code).values():
            names = (member.MemberDisplayName, member.ListDisplayName, member.PatronDisplayName)
            haystack = " | ".join(n for n in names if n).lower()
            if needle in haystack:
                hits.append(member)

        return sorted(hits, key=lambda m: (m.ListDisplayName or m.name).lower())

    def member_bills(
        self,
        member_id: int,
        session_code: int,
        *,
        role: int | None = None,
        refresh: bool = False,
    ) -> list[MemberLegislation]:
        """Every bill a member patrons in a session, one entry per bill.

        This is the member-first bill axis, the counterpart of
        :meth:`member_votes`.  The bill-first list names only the chief
        patron, so co-patronage is reachable only this way.

        The endpoint returns one row per published summary version, so a bill
        with three summaries arrives three times.  This keeps the last row
        LIS sends for each ``LegislationID``, which carries the newest
        summary, and preserves the order of first appearance.  Delegate
        Schmidt's 236 rows for 20261 collapse to 229 bills.

        The session code is resolved to a ``SessionID`` first, because the
        endpoint mis-caches session codes; see
        :meth:`LISClient.get_member_legislation`.  The response is cached for
        ``roster_ttl`` per member, session, and role.

        Args:
            member_id: ``MemberID`` from the roster or :meth:`find_members`.
            session_code: e.g. ``20261``.
            role: Restrict to one ``PatronTypeID``: ``CHIEF_PATRON`` (1),
                ``CHIEF_CO_PATRON`` (2), or ``CO_PATRON`` (4).  Omit for
                every role.
            refresh: Fetch again even when a fresh copy is cached.

        Raises:
            SessionNotFoundError: The code is not in the session reference.
        """
        rows = self._member_bill_rows(member_id, session_code, role, refresh=refresh)

        collapsed: dict[int, MemberLegislation] = {}
        for row in rows:
            collapsed[row.LegislationID] = row

        return list(collapsed.values())

    def bill_patrons(self, bill_number: str, session_code: int) -> list[Patron]:
        """Every patron of a bill, in every role, by bill number.

        Args:
            bill_number: Any case, padded or not.  ``hb0001`` resolves ``HB1``.
            session_code: e.g. ``20261``.

        Raises:
            InvalidBillNumberError: The string does not parse as a bill number.
            BillNotFoundError: The number is not in that session.
        """
        return self.client.get_bill_patrons(self.resolve_bill_id(bill_number, session_code))

    def session_id(self, session_code: int) -> int:
        """The ``SessionID`` behind a session code, e.g. ``59`` for ``20261``.

        Most endpoints accept either form, but ``/LegislationByMember`` caches
        by ``sessionID`` alone, so callers there must send the ID.  The
        session reference is fetched once and kept; an unknown code triggers
        one refetch, in case a session was added since.

        Raises:
            SessionNotFoundError: The code is absent even after a refetch.
        """
        code = int(session_code)

        ids = self._session_ids
        if ids is None or code not in ids:
            with self._reference_lock:
                if self._session_ids is None or code not in self._session_ids:
                    self._session_ids = {
                        int(s.SessionCode): s.SessionID for s in self.client.get_sessions()
                    }
                ids = self._session_ids

        if code not in ids:
            raise SessionNotFoundError(
                f"Session code {code} is not in the LIS session reference. "
                f"Known codes run from {min(ids)} to {max(ids)}."
                if ids
                else f"Session code {code} is not in the LIS session reference."
            )

        return ids[code]

    def event_types_by_code(self) -> dict[str, list[LegislationEventType]]:
        """The event type reference, grouped by ``EventCode``.

        The endpoint returns 3,912 rows with ``LegislationEventTypeID`` null,
        so ``EventCode`` is the only join key onto a bill's events.  The
        vocabulary is static, so the service fetches it once.

        **``EventCode`` is not unique.**  3,912 rows carry only 1,502 distinct
        codes, and 1,326 codes repeat.  The repeats are not harmless copies:
        ``H1405`` returns four rows, two reading "Reported from Labor and
        Commerce" and two reading "Failed to report (defeated) in Labor and
        Commerce".  A ``dict[str, LegislationEventType]`` keyed on the code
        keeps whichever row arrives last, so it can report a bill as defeated
        when it passed.  That is why this returns every row per code.

        Use :meth:`event_type_for` to resolve one event to one row.

        That response is 2.19 MB and the endpoint ignores ``X-Pagination``
        (measured 2026-08-28), so caching is the only way to avoid paying
        for it twice.
        """
        if self._event_types is None:
            with self._reference_lock:
                if self._event_types is None:
                    grouped: dict[str, list[LegislationEventType]] = {}
                    for t in self.client.get_event_types():
                        if t.EventCode:
                            grouped.setdefault(t.EventCode, []).append(t)
                    self._event_types = grouped

        return self._event_types

    def event_type_for(self, event: LegislationEvent) -> LegislationEventType | None:
        """The single event type row that describes ``event``.

        ``(EventCode, LegislationChamberCode, IsPassed)`` is the real primary
        key of the vocabulary: it yields 3,912 distinct keys for 3,912 rows,
        with no collisions (measured 2026-09-08).

        ``LegislationChamberCode`` is the **bill's** chamber, not the actor's.
        A Senate bill reported from a House committee carries the House actor
        code ``H1405`` against a row whose chamber is ``S``.  The event's own
        ``ChamberCode`` mirrors the code prefix, so it names the actor and
        cannot serve here; the bill's chamber comes from
        ``event.LegislationNumber`` instead.

        ``IsPassed`` separates the pass and fail twins that share a code, so
        the method keeps it even when the chamber does not match.

        Returns:
            The matching :class:`LegislationEventType`, or ``None`` when the
            code is absent from the vocabulary.
        """
        rows = self.event_types_by_code().get(event.EventCode or "")
        if not rows:
            return None

        number = event.LegislationNumber or event.ChamberCode or ""
        chamber = number[:1].upper() or None

        exact = [
            r for r in rows if r.LegislationChamberCode == chamber and r.IsPassed == event.IsPassed
        ]
        if exact:
            return exact[0]

        by_passed = [r for r in rows if r.IsPassed == event.IsPassed]
        if by_passed:
            return by_passed[0]

        return rows[0]

    def statuses_by_name(self) -> dict[str, LegislationStatus]:
        """The 52 status rows, keyed by internal ``Name``.

        An event carries a status ``Name`` and a null ``LegislationStatusID``,
        so the name is the join key for a per-event status.
        """
        self._load_statuses()

        return self._statuses_by_name

    def statuses_by_id(self) -> dict[int, LegislationStatus]:
        """The 52 status rows, keyed by ``LegislationStatusID``.

        Bill detail carries the ID rather than the name, so this is the join
        key for a bill's own status.
        """
        self._load_statuses()

        return self._statuses_by_id

    def bill_status_label(
        self,
        bill: Legislation,
        item: LegislationSummaryItem | None = None,
    ) -> str | None:
        """The best status label available for a bill.

        ``Legislation.LegislationStatus`` is often null on the detail
        endpoint, so fall back to the status reference, then to the session
        list row.

        Args:
            bill: The detail record.
            item: The session list row from :meth:`resolve_bill`.  It is the
                last fallback.
        """
        if bill.LegislationStatus:
            return bill.LegislationStatus

        if bill.LegislationStatusID is not None:
            status = self.statuses_by_id().get(bill.LegislationStatusID)
            if status is not None:
                return status.DisplayName

        return item.LegislationStatus if item is not None else None

    def clear_cache(self) -> None:
        """Drop every cached bill list, roster, vote history, and vocabulary."""
        with self._guard:
            self._bill_lists.clear()
            self._rosters.clear()
            self._member_votes.clear()
            self._member_bills.clear()

        with self._reference_lock:
            self._session_ids = None
            self._event_types = None
            self._statuses_by_name = None
            self._statuses_by_id = None

    def _read_bill_list(self, session_code: int) -> list[LegislationSummaryItem] | None:
        """The cached bill list for a session, or None when it is absent or stale."""
        cached = self._bill_lists.get(session_code)

        if cached is None or time.monotonic() - cached[0] >= self.bill_list_ttl:
            return None

        return cached[1]

    def _bill_list_lock(self, session_code: int) -> threading.Lock:
        with self._guard:
            return self._bill_list_locks.setdefault(session_code, threading.Lock())

    def _read_roster(self, session_code: int) -> dict[int, Member] | None:
        """The cached roster for a session, or None when absent or stale."""
        cached = self._rosters.get(session_code)

        if cached is None or time.monotonic() - cached[0] >= self.roster_ttl:
            return None

        return cached[1]

    def _roster_lock(self, session_code: int) -> threading.Lock:
        with self._guard:
            return self._roster_locks.setdefault(session_code, threading.Lock())

    def _member_vote_rows(
        self,
        member_id: int,
        session_code: int,
        *,
        refresh: bool = False,
    ) -> list[MemberVoteResult]:
        """The raw ~1.9 MB vote history for a member, cached per session."""
        key = (member_id, session_code)

        if not refresh:
            cached = self._read_member_votes(key)
            if cached is not None:
                return cached

        with self._member_vote_lock(key):
            # Another thread may have filled the slot while this one waited.
            if not refresh:
                cached = self._read_member_votes(key)
                if cached is not None:
                    return cached

            rows = self.client.get_member_votes(member_id=member_id, session_code=session_code)
            self._member_votes[key] = (time.monotonic(), rows)
            return rows

    def _read_member_votes(self, key: tuple[int, int]) -> list[MemberVoteResult] | None:
        cached = self._member_votes.get(key)

        if cached is None or time.monotonic() - cached[0] >= self.roster_ttl:
            return None

        return cached[1]

    def _member_vote_lock(self, key: tuple[int, int]) -> threading.Lock:
        with self._guard:
            return self._member_vote_locks.setdefault(key, threading.Lock())

    def _member_bill_rows(
        self,
        member_id: int,
        session_code: int,
        role: int | None,
        *,
        refresh: bool = False,
    ) -> list[MemberLegislation]:
        """The raw member bill rows, one per summary version, cached per role."""
        key = (member_id, session_code, role)

        if not refresh:
            cached = self._read_member_bills(key)
            if cached is not None:
                return cached

        with self._member_bill_lock(key):
            # Another thread may have filled the slot while this one waited.
            if not refresh:
                cached = self._read_member_bills(key)
                if cached is not None:
                    return cached

            rows = self.client.get_member_legislation(
                member_id=member_id,
                session_id=self.session_id(session_code),
                patron_type_id=role,
            )
            self._member_bills[key] = (time.monotonic(), rows)
            return rows

    def _read_member_bills(
        self, key: tuple[int, int, int | None]
    ) -> list[MemberLegislation] | None:
        cached = self._member_bills.get(key)

        if cached is None or time.monotonic() - cached[0] >= self.roster_ttl:
            return None

        return cached[1]

    def _member_bill_lock(self, key: tuple[int, int, int | None]) -> threading.Lock:
        with self._guard:
            return self._member_bill_locks.setdefault(key, threading.Lock())

    def _load_statuses(self) -> None:
        if self._statuses_by_name is not None:
            return

        with self._reference_lock:
            if self._statuses_by_name is not None:
                return

            statuses = self.client.get_legislation_statuses()
            self._statuses_by_id = {s.LegislationStatusID: s for s in statuses}
            self._statuses_by_name = {s.Name: s for s in statuses}


def normalize_bill_number(bill_number: str) -> str:
    """Uppercase and unpad a bill number: ``hb0001`` becomes ``HB1``.

    LIS returns 204 for a padded number, so every bill number must pass
    through this before it reaches the API.

    Raises:
        InvalidBillNumberError: The string is not a chamber prefix plus digits.
    """
    candidate = bill_number.strip().upper().replace(" ", "")

    match = _BILL_NUMBER_RE.fullmatch(candidate)
    if match is None:
        raise InvalidBillNumberError(
            f"{bill_number!r} is not a bill number. Expected a chamber prefix and "
            f"digits, for example HB1, SB234, or HJ5."
        )

    prefix, digits = match.groups()
    return f"{prefix}{int(digits)}"


def pick_text_version(
    texts: list[LegislationTextItem],
    document_code: str | None = None,
) -> LegislationTextItem:
    """Select one text version, defaulting to the most recent.

    Args:
        texts: A version list from :meth:`LISService.bill_text_versions`.
        document_code: A version to select, e.g. ``"HB1ER"``.  Case is ignored.

    Raises:
        TextVersionNotFoundError: No version carries that document code.
    """
    if document_code is None:
        # LegislationTextID is a rising surrogate key, so the highest one is
        # the newest version.  DraftDate is not always set.
        return max(texts, key=lambda t: t.LegislationTextID)

    wanted = document_code.strip().upper()
    for text in texts:
        if (text.DocumentCode or "").upper() == wanted:
            return text

    available = ", ".join(t.DocumentCode or "?" for t in texts)
    raise TextVersionNotFoundError(
        f"No text version {wanted!r} for this bill. Available versions: {available}."
    )


def strip_html(markup: str) -> str:
    """Flatten LIS HTML to readable plain text.

    Bill text and summaries arrive as HTML.  Amendment markup flattens too:
    additions carry ``<em class="new">`` and deletions carry ``<s>``, so the
    marks are lost and only the words remain.

    This uses the standard library parser, so the package needs no HTML
    dependency.
    """
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()

    text = extractor.text()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class _TextExtractor(HTMLParser):
    """Collects text content, dropping tags and ``<script>``/``<style>`` bodies."""

    _SKIP_CONTENT = {"script", "style"}
    _BLOCK = {"p", "div", "br", "li", "tr", "table", "section", "h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_CONTENT:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_CONTENT and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)
