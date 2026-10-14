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
- The bulk version endpoint takes ids, not numbers, and returns every bill's
  rows interleaved by date.  ``bill_versions`` resolves the numbers through
  the session's number list and groups the rows back under them.
- Votes have no bill-first endpoint.  Reaching a bill's roll calls means
  walking its events for ``VoteID`` and fetching each one.
- A ballot names a member by ``MemberID`` and nothing else, so party and
  district take a second join against the session roster.
- The member-first vote endpoint returns one row per (vote, bill) pair and
  omits the block flag, so the service derives it by counting the rows that
  share a ``VoteID``.
- Advanced search returns a bill once per published summary version, and on
  top of that emits exact duplicate rows.  The two grains are two methods:
  ``search_bills`` collapses to one row per bill, ``search_bill_summaries``
  keeps every version.  Both drop the exact duplicates.
- The member-first bill endpoint repeats a bill the same two ways, and LIS
  does not reliably send the versions in progression order.  So
  ``member_bills`` and ``search_bills`` both rank the ``SummaryVersion``
  label rather than trusting arrival order.  The two services spell the
  chamber passage label differently, which is why the rank table carries
  both spellings.

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
from datetime import date, datetime
from html.parser import HTMLParser

from va_lis_client.client import LISClient
from va_lis_client.exceptions import (
    BillNotFoundError,
    CommitteeNotFoundError,
    InvalidBillNumberError,
    SessionNotFoundError,
    TextVersionNotFoundError,
)
from va_lis_client.models import (
    BillVersion,
    CalendarAction,
    Committee,
    CommitteeMember,
    Contact,
    DocketDetail,
    DocketItem,
    Legislation,
    LegislationEvent,
    LegislationEventType,
    LegislationSearchResult,
    LegislationStatus,
    LegislationSummaryItem,
    LegislationTextDetail,
    LegislationTextItem,
    Member,
    MemberLegislation,
    MemberVoteResult,
    MinutesActivity,
    MinutesBook,
    MinutesBookListItem,
    MinutesEntry,
    Patron,
    Personnel,
    ShallowVote,
    Vote,
    VoteMember,
    VoteStatement,
    summary_version_rank,
)

DEFAULT_BILL_LIST_TTL = 15 * 60
DEFAULT_ROSTER_TTL = 60 * 60

# A contact hangs off an owner type, and a member is type 3.  Its mailing
# address is contact type 9.  Both come from the Contact reference lists,
# which a Contact row cannot tell you itself: it never sends OwnerTypeID.
_MEMBER_OWNER_TYPE = 3
_MEMBER_MAILING = 9

_BILL_NUMBER_RE = re.compile(r"([A-Z]+)(\d+)")

# Ids per bulk version request.  102 went through in one request on
# 2026-09-17, so 100 stays inside what has been seen to work.
_VERSION_BATCH = 100


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


@dataclass(frozen=True)
class CommitteeSeat:
    """One member's seat on one committee, with the session roster joined on.

    :meth:`LISService.committee_members` and
    :meth:`LISService.member_committees` return these.  ``seat`` is the row
    from ``/MembersByCommittee``, which names the member and the role and
    nothing else; ``member`` is the session roster row that adds party and
    district, or ``None`` when the roster lacks the member.
    """

    committee: Committee
    seat: CommitteeMember
    member: Member | None

    @property
    def role(self) -> str:
        """``"Chair"``, ``"Vice-Chair"``, ``"Co-Chair"``, ``"Member"``, or ``"Ex-Officio"``."""
        return self.seat.role

    @property
    def is_chair(self) -> bool:
        """True for a chair or co-chair; a vice-chair is not a chair."""
        return self.role in ("Chair", "Co-Chair")

    @property
    def name(self) -> str:
        """The member's display name."""
        return self.seat.MemberDisplayName or (self.member.name if self.member else "")

    @property
    def party(self) -> str | None:
        """``"D"``, ``"I"``, or ``"R"``, from the roster."""
        return self.member.PartyCode if self.member else self.seat.PartyCode

    @property
    def district(self) -> str | None:
        """The district label, e.g. ``"71st"``, from the roster."""
        return self.member.DistrictName if self.member else None


@dataclass(frozen=True)
class DocketEntry:
    """One bill on one Senate committee docket, with the docket it sits on.

    :meth:`LISService.docket_entries` returns these.  ``docket`` is the full
    docket, carried on every entry because one docket lists many bills;
    ``item`` is the bill's row on it, with the summary and patrons.
    """

    docket: DocketDetail
    item: DocketItem

    @property
    def bill_number(self) -> str | None:
        """e.g. ``"HB128"``."""
        return self.item.LegislationNumber

    @property
    def date(self) -> datetime | None:
        """The docket date.  The linked schedule may disagree on the hour."""
        return self.docket.DocketDate

    @property
    def room(self) -> str | None:
        """The room from the linked schedule, when one is attached."""
        schedule = self.docket.schedule
        return schedule.RoomDescription if schedule else None


@dataclass(frozen=True)
class MinutesRecord:
    """One action in one minutes book, flattened to a single row.

    :meth:`LISService.minutes_records` returns these.  A minutes book nests
    book to category to entry to activity; this is the join of the three
    levels that carry meaning, one record per activity.  ``book`` repeats
    across every record from the same day.

    Unlike a bill event, :attr:`vote_id` here is the roll call for
    :attr:`bill_number` — see
    :class:`~va_lis_client.models.minutes.MinutesActivity`.  Pass it to
    :meth:`LISService.roll_call` to name the members.
    """

    book: MinutesBook
    entry: MinutesEntry
    activity: MinutesActivity

    @property
    def bill_number(self) -> str | None:
        """e.g. ``"SB2"``, or ``None`` for procedural business."""
        return self.entry.LegislationNumber

    @property
    def date(self) -> datetime | None:
        """The day the body met."""
        return self.book.MinutesDate

    @property
    def body(self) -> str | None:
        """The committee name, or the chamber code for a floor book."""
        return self.book.CommitteeName or self.book.ChamberCode

    @property
    def description(self) -> str | None:
        """What happened, e.g. ``"Reported from Rules (13-Y 0-N)"``."""
        return self.activity.Description

    @property
    def event_code(self) -> str | None:
        """The action's event code, e.g. ``"S1005"``."""
        return self.activity.EventCode

    @property
    def vote_id(self) -> int | None:
        """The roll call for this bill, or ``None`` when no vote was taken."""
        return self.activity.VoteID

    @property
    def tally(self) -> str | None:
        """The minutes' tally string, which keeps a chair's tiebreak."""
        return self.activity.VoteTally

    @property
    def is_block(self) -> bool:
        """Whether one motion disposed of this bill along with others."""
        return self.activity.IsBlock

    @property
    def is_voice(self) -> bool:
        """Whether the vote was a voice vote, which names no members."""
        return self.activity.is_voice


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
        self._committees: dict[tuple[str | None, bool], tuple[float, list[Committee]]] = {}
        self._committee_rosters: dict[tuple[int, int], tuple[float, list[CommitteeMember]]] = {}
        self._dockets: dict[int, tuple[float, DocketDetail | None]] = {}
        self._minutes: dict[int, tuple[float, MinutesBook | None]] = {}
        self._legislation_numbers: dict[int, tuple[float, dict[str, int]]] = {}
        self._calendar_actions: dict[int, list[CalendarAction]] | None = None
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
        self._committee_locks: dict[tuple[str | None, bool], threading.Lock] = {}
        self._committee_roster_locks: dict[tuple[int, int], threading.Lock] = {}
        self._docket_locks: dict[int, threading.Lock] = {}
        self._minutes_locks: dict[int, threading.Lock] = {}
        self._legislation_number_locks: dict[int, threading.Lock] = {}
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

    def bill_versions(
        self,
        bill_numbers: list[str],
        session_code: int,
        *,
        with_files: bool = False,
    ) -> dict[str, list[BillVersion]]:
        """Every published version of several bills, grouped by bill.

        The bulk endpoint takes ids and returns every bill's rows mixed
        together, sorted by date.  This resolves each number to its
        ``LegislationID`` through the session's number list (268 KB for
        20261, cached for ``roster_ttl``), fetches the versions 100 ids at a
        time, and groups the rows back under the numbers asked for.  Each
        bill keeps the order LIS sent, oldest ``DraftDate`` first.

        For one bill, ``client.get_bill_versions`` is a single small request
        and needs no list.

        **The bulk request returns no files.**  LIS fills the PDF, HTML and
        impact statement arrays only when a request names one id, so
        ``with_files=True`` spends one request per bill to get them.  Without
        it the arrays are empty, except for a batch that happens to hold a
        single bill.

        A carried bill's rows name the even year session, because LIS answers
        with those rows for the odd year too.

        Args:
            bill_numbers: Any case, padded or not.  Repeats collapse.
            session_code: e.g. ``20261``.
            with_files: Fetch one bill at a time, so the file arrays are filled.

        Returns:
            A dict keyed by normalized bill number, in the order asked.  A
            bill with no published version maps to ``[]``.

        Raises:
            InvalidBillNumberError: A string does not parse as a bill number.
            BillNotFoundError: A number is not in the session, even after the
                number list is fetched again.
        """
        numbers = list(dict.fromkeys(normalize_bill_number(n) for n in bill_numbers))
        if not numbers:
            return {}

        ids = self._legislation_ids(session_code)
        if any(n not in ids for n in numbers):
            # The bill may be newer than the cached list, so look once more.
            ids = self._legislation_ids(session_code, refresh=True)

        missing = [n for n in numbers if n not in ids]
        if missing:
            raise BillNotFoundError(
                f"{', '.join(missing)} not in session {session_code}. Check the bill "
                f"numbers and the session code (20261 = 2026 Regular Session)."
            )

        wanted = {ids[n]: n for n in numbers}
        grouped: dict[str, list[BillVersion]] = {n: [] for n in numbers}

        if with_files:
            for legislation_id, number in wanted.items():
                grouped[number] = self.client.get_bill_versions_by_id(legislation_id)

            return grouped

        batch = list(wanted)

        for start in range(0, len(batch), _VERSION_BATCH):
            rows = self.client.get_bill_versions_by_id(
                legislation_ids=batch[start : start + _VERSION_BATCH]
            )
            for row in rows:
                number = wanted.get(row.LegislationID)
                if number is not None:
                    grouped[number].append(row)

        return grouped

    def bill_votes(
        self,
        bill_number: str,
        session_code: int,
        *,
        roll_calls_only: bool = False,
    ) -> list[BillVote]:
        """Every recorded vote on a bill, in both chambers, with the members.

        This walks the bill's events, takes each ``VoteID``, and fetches the
        roll call behind it.  The members cost one request per vote whatever
        the route, because only the single vote record carries them.  When
        the tallies are enough, :meth:`bill_shallow_votes` lists the same
        votes in two requests.

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

    def bill_shallow_votes(self, bill_number: str, session_code: int) -> list[ShallowVote]:
        """A bill's votes in both chambers without the members, in two requests.

        The cheap sibling of :meth:`bill_votes`.  Each row has the tally, the
        vote type and the committee, and no roll call, so a bill costs one
        request per chamber however many times it was voted on.  HB1 in
        session 20261 returns 7: three House and four Senate.

        **Voice votes are mostly absent.**  :meth:`bill_votes` returns 8 for
        that bill, and the eighth is a voice vote this list leaves out.  A
        bill decided by voice alone returns nothing here.

        **Read** :attr:`~va_lis_client.models.vote.ShallowVote.is_block`,
        **not** ``IsBlock``.  The flag is false on every row; a block vote
        names itself in ``LegislationNumber``.

        Args:
            bill_number: Any case, padded or not.  ``hb0001`` resolves ``HB1``.
            session_code: e.g. ``20261``.

        Returns:
            :class:`~va_lis_client.models.vote.ShallowVote` rows from both
            chambers, oldest first.

        Raises:
            InvalidBillNumberError: The string does not parse as a bill number.
            BillNotFoundError: The bill is not in that session.
        """
        legislation_id = self.resolve_bill_id(bill_number, session_code)

        votes = []
        for chamber_code in ("H", "S"):
            votes.extend(
                self.client.get_shallow_votes(
                    chamber_code,
                    session_code=session_code,
                    legislation_id=legislation_id,
                )
            )

        return sorted(votes, key=lambda v: (v.VoteDate is None, v.VoteDate, v.VoteID))

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
        with three summaries arrives three times.  This keeps the row whose
        ``SummaryVersion`` ranks furthest along, and preserves the order of
        first appearance.  Delegate Schmidt's 236 rows for 20261 collapse to
        229 bills.

        **The rank decides, not the arrival order.**  LIS usually sends the
        versions in progression order and sometimes does not: across a
        40-member sample of 20261 (measured 2026-09-15), 57 groups held more
        than one distinct version and HB1503 arrived with ``SUMMARY AS
        PASSED`` ahead of ``SUMMARY AS PASSED CHAMBER``.  Taking the last row
        kept the older label there.  35 of those 57 groups carry genuinely
        different summary text, so the pick matters.

        **This service spells the chamber summary its own way.**  It sends
        ``SUMMARY AS PASSED CHAMBER`` and never the per-chamber spelling that
        ``/AdvancedLegislationSearch`` sends; see
        :data:`~va_lis_client.models.search.SUMMARY_VERSION_RANK`.

        Rows also arrive as exact duplicates, the same defect the search
        shows.  141 of 198 multi-row groups in that sample held two byte
        identical rows.  Collapsing on ``LegislationID`` absorbs them.

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
            current = collapsed.get(row.LegislationID)
            rank = summary_version_rank(row.SummaryVersion)
            if current is None or rank > summary_version_rank(current.SummaryVersion):
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

    def search_bills(
        self,
        *,
        dedupe: bool = True,
        **criteria,
    ) -> list[LegislationSearchResult]:
        """Search bills, one row per bill.

        This is the bill-grain half of the search.  LIS returns one row per
        published summary version, so a bill that advanced arrives two or
        three times; this keeps the row carrying the furthest-along summary.
        Call :meth:`search_bill_summaries` when you want every version.

        Ranking picks the winner, not arrival order.  LIS usually sends the
        versions in progression order and sometimes does not: HB1503 and
        SB605 in 20261 both put ``SUMMARY AS PASSED`` ahead of the chamber
        passage row.  See :data:`SUMMARY_VERSION_RANK`.

        Collapsing by ``LegislationID`` also absorbs the exact duplicate rows
        LIS emits, which carry no distinguishing field at all.  Session 20261
        with no filter returns 3,007 rows and collapses to 2,827 bills.

        Rows keep the order in which each bill first appeared.

        **Warning:** the endpoint has no paging, so an unfiltered search
        fetches the whole session.  20261 measured 5.7 MB.  Send a filter.

        **Warning:** a keyword needs ``keyword_location``.  Without it LIS
        answers nothing and this returns ``[]``; no default is added here.
        ``keyword="firearm"`` in 20261 found 0 bills alone, 50 with
        ``"Summary"`` and 129 with ``"Bill Text"`` (measured 2026-10-13).

        **Warning:** a criterion beside the keyword drops failed and
        incorporated bills.  Those 50 bills fell to 37 once a chamber, a
        category or a summary length went with the keyword, and the 13
        missing were the 10 ``Failed`` and the 3 ``Incorporated``.
        ``exclude_failed=False`` does not bring them back.  ``member_id``
        drops them too, so call :meth:`member_bills` for everything a member
        patrons.  ``committee_id`` does not select the bills a committee
        holds.  :meth:`LISClient.search_legislation` carries the
        measurements.

        Args:
            dedupe: Collapse to one row per bill.  Set ``False`` to get the
                rows exactly as LIS sent them, duplicates and all, which is
                the same thing :meth:`LISClient.search_legislation` returns.
            **criteria: Search criteria, forwarded unchanged to
                :meth:`LISClient.search_legislation`.  That method's
                docstring carries the full list and its traps.

        Example::

            hits = service.search_bills(
                session_code=20261, keyword="firearm", keyword_location="Summary"
            )
        """
        rows = self.client.search_legislation(**criteria)
        if not dedupe:
            return rows

        best: dict[int, LegislationSearchResult] = {}
        for row in rows:
            current = best.get(row.LegislationID)
            if current is None or row.summary_rank > current.summary_rank:
                best[row.LegislationID] = row

        return list(best.values())

    def search_bill_summaries(
        self,
        *,
        dedupe: bool = True,
        **criteria,
    ) -> list[LegislationSearchResult]:
        """Search bills, one row per published summary version.

        This is the summary-grain half of the search, and the right call when
        you want to read how a bill's summary changed as it advanced.  A bill
        with three summaries stays three rows.  Call :meth:`search_bills` for
        one row per bill.

        Deduplication here removes only the exact duplicate rows LIS emits,
        by keying on ``(LegislationID, SummaryVersion)``.  It never collapses
        two genuine versions.  Session 20261 with no filter returns 3,007
        rows and settles to 2,884.

        Note that 17 of the 52 multi-version bills in 20261 carry *identical*
        summary text under two different version labels, so a version change
        does not promise a text change.

        Rows keep the order LIS sent them.

        **Warning:** the endpoint has no paging, so an unfiltered search
        fetches the whole session.  20261 measured 5.7 MB.  Send a filter.

        **Warning:** a keyword needs ``keyword_location``.  Without it LIS
        answers nothing and this returns ``[]``; no default is added here.
        ``keyword="firearm"`` with ``keyword_location="Summary"`` returned
        53 rows for 20261, and they settle to 51 here: 50 bills, one of them
        with two summary versions (measured 2026-10-13).

        **Warning:** a criterion beside the keyword drops failed and
        incorporated bills, ``exclude_failed`` changes nothing, and
        ``committee_id`` does not select the bills a committee holds.  See
        :meth:`search_bills` and :meth:`LISClient.search_legislation`.

        Args:
            dedupe: Drop exact duplicate rows.  Set ``False`` to get the rows
                exactly as LIS sent them, which is the same thing
                :meth:`LISClient.search_legislation` returns.
            **criteria: Search criteria, forwarded unchanged to
                :meth:`LISClient.search_legislation`.  That method's
                docstring carries the full list and its traps.
        """
        rows = self.client.search_legislation(**criteria)
        if not dedupe:
            return rows

        seen: set[tuple[int, str | None]] = set()
        kept: list[LegislationSearchResult] = []
        for row in rows:
            key = (row.LegislationID, row.SummaryVersion)
            if key in seen:
                continue

            seen.add(key)
            kept.append(row)

        return kept

    def committees(
        self,
        chamber_code: str | None = None,
        *,
        include_subcommittees: bool = False,
        refresh: bool = False,
    ) -> list[Committee]:
        """The standing committees, cached for ``roster_ttl``.

        The list is not session scoped (LIS ignores the session code it is
        sent), so the cache is keyed on chamber and whether subcommittees are
        included.

        Args:
            chamber_code: ``"H"`` or ``"S"``.  Omit for both.
            include_subcommittees: Add every subcommittee; 67 House rows
                instead of 14.
            refresh: Fetch again even when a fresh copy is cached.
        """
        key = (chamber_code, include_subcommittees)

        return self._memo(
            self._committees,
            self._committee_locks,
            key,
            lambda: self.client.get_committees(
                chamber_code=chamber_code,
                include_subcommittees=include_subcommittees,
            ),
            refresh=refresh,
        )

    def resolve_committee(self, query: str, chamber_code: str | None = None) -> Committee:
        """Find one committee by number or name, subcommittees included.

        ``"H08"`` matches the number.  ``"Courts of Justice"`` matches the name
        exactly, and ``"courts"`` matches as a substring, both ignoring case
        and inner whitespace.  Both chambers have a Courts of Justice, so
        pass ``chamber_code`` when a name could exist in either.

        A substring that matches one standing committee and several of its
        subcommittees resolves to the standing committee.

        Args:
            query: A committee number or any part of a name.
            chamber_code: ``"H"`` or ``"S"`` to search one chamber.

        Raises:
            CommitteeNotFoundError: Nothing matches, or more than one does.
        """
        wanted = " ".join(query.split())
        if not wanted:
            raise CommitteeNotFoundError("A committee number or name is required.")

        rows = self.committees(chamber_code, include_subcommittees=True)

        by_number = [c for c in rows if c.CommitteeNumber.upper() == wanted.upper()]
        if by_number:
            return by_number[0]

        lowered = wanted.lower()
        matches = [c for c in rows if c.name.lower() == lowered]
        if not matches:
            matches = [c for c in rows if lowered in c.name.lower()]
            standing = [c for c in matches if not c.is_subcommittee]
            if len(standing) == 1:
                matches = standing

        if len(matches) == 1:
            return matches[0]

        if not matches:
            raise CommitteeNotFoundError(f"No committee matches {query!r}.")

        names = ", ".join(f"{c.CommitteeNumber} {c.name}" for c in matches)
        raise CommitteeNotFoundError(
            f"{query!r} matches {len(matches)} committees: {names}. "
            "Pass the committee number, or a chamber code."
        )

    def committee_members(
        self,
        committee_id: int,
        session_code: int,
        *,
        refresh: bool = False,
    ) -> list[CommitteeSeat]:
        """Who sits on a committee in a session, with party and district.

        The seat list from ``/MembersByCommittee`` names the member and the
        role and nothing else, so this joins the session roster on, the way
        :meth:`roll_call` does for ballots.  Both lists are cached for
        ``roster_ttl``.  Seats come back in LIS order, which puts the chair
        first.

        Args:
            committee_id: ``CommitteeID`` from :meth:`committees` or
                :meth:`resolve_committee`.  Subcommittees work too.
            session_code: e.g. ``20261``.
            refresh: Fetch the seat list again even when a fresh copy is
                cached.

        Raises:
            CommitteeNotFoundError: No committee has that ID.
        """
        committee = self._committee_by_id(committee_id, session_code)
        roster = self.members_by_id(session_code)

        seats = self._memo(
            self._committee_rosters,
            self._committee_roster_locks,
            (committee_id, session_code),
            lambda: self.client.get_committee_members(committee_id, session_code),
            refresh=refresh,
        )

        return [CommitteeSeat(committee, seat, roster.get(seat.MemberID)) for seat in seats]

    def member_committees(
        self,
        member_id: int,
        session_code: int,
        chamber_code: str | None = None,
        *,
        include_subcommittees: bool = False,
        refresh: bool = False,
    ) -> list[CommitteeSeat]:
        """Every committee seat one member holds in a session.

        LIS has no member-first committee endpoint, so this walks every
        committee in the member's chamber and reads each seat list: 14
        requests for a House member, 67 with subcommittees, all cached for
        ``roster_ttl``.  The chamber comes from the session roster when it
        is not given; a member absent from the roster searches both chambers.

        Args:
            member_id: ``MemberID`` from the roster or :meth:`find_members`.
            session_code: e.g. ``20261``.
            chamber_code: ``"H"`` or ``"S"``; omit to read it off the roster.
            include_subcommittees: Walk the subcommittees too.
            refresh: Fetch every seat list again.
        """
        if chamber_code is None:
            member = self.members_by_id(session_code).get(member_id)
            chamber_code = member.ChamberCode if member else None

        seats = []
        for committee in self.committees(chamber_code, include_subcommittees=include_subcommittees):
            for seat in self.committee_members(
                committee.CommitteeID, session_code, refresh=refresh
            ):
                if seat.seat.MemberID == member_id:
                    seats.append(seat)

        return seats

    def docket_entries(
        self,
        committee: int | str,
        session_code: int,
        *,
        refresh: bool = False,
    ) -> list[DocketEntry]:
        """Every bill on every docket of a Senate committee in a session.

        This is the Senate half of "when is a bill heard".  The docket list
        names no bills, so this fetches each docket's detail, one request per
        docket, cached for ``roster_ttl``: 17 requests for Senate Courts of
        Justice in 20261, which docketed 16 meetings.  Entries come back in
        docket order, newest docket first as LIS lists them.

        The House keeps no dockets; its committee agendas are not in the API.
        Read a House bill's committee from its events instead.

        Args:
            committee: A ``CommitteeID``, or a number or name for
                :meth:`resolve_committee` (``"S13"``, ``"Courts of Justice"``).
            session_code: e.g. ``20261``.
            refresh: Fetch every docket again even when cached.

        Raises:
            CommitteeNotFoundError: A name or number matches nothing, or more
                than one committee.
        """
        if isinstance(committee, str):
            committee_id = self.resolve_committee(committee, "S").CommitteeID
        else:
            committee_id = committee

        entries = []
        for docket in self.client.get_dockets(committee_id, session_code):
            detail = self._memo(
                self._dockets,
                self._docket_locks,
                docket.DocketID,
                lambda docket_id=docket.DocketID: self.client.get_docket(docket_id),
                refresh=refresh,
            )
            if detail is None:
                continue

            entries.extend(DocketEntry(detail, item) for item in detail.bills)

        return entries

    def minutes_books(
        self,
        session_code: int,
        *,
        committee: int | str | None = None,
        chamber_code: str | None = None,
        start_date: str | date | datetime | None = None,
        end_date: str | date | datetime | None = None,
    ) -> list[MinutesBookListItem]:
        """The published minutes books for a session, shallow and cheap.

        One request.  The rows name no bills; pass a ``MinutesBookID`` to
        :meth:`minutes_records` for those.

        Both chambers publish committee books, but **only the Senate
        publishes floor books**: 46 of 388 rows in 20261, 33 of 599 in 20251,
        and all 45 rows of 20241.  Test
        :attr:`~va_lis_client.models.minutes.MinutesBookListItem.is_floor` to
        split them; the ``IsCommittee`` flag LIS sends is ``False`` on every
        row and means nothing.

        Args:
            session_code: e.g. ``20261``.  20271 was empty on 2026-09-15.
            committee: A ``CommitteeID``, or a number or name for
                :meth:`resolve_committee` (``"S13"``, ``"Courts of Justice"``).
                Filtering by one excludes every floor book.
            chamber_code: ``"H"`` or ``"S"``.  Omit for both.
            start_date: Inclusive lower bound on the meeting date.
            end_date: Inclusive upper bound on the meeting date.

        Raises:
            CommitteeNotFoundError: A name or number matches nothing, or more
                than one committee.
        """
        committee_id = None
        if isinstance(committee, str):
            committee_id = self.resolve_committee(committee, chamber_code).CommitteeID
        elif committee is not None:
            committee_id = committee

        return self.client.get_minutes_books(
            session_code=session_code,
            committee_id=committee_id,
            chamber_code=chamber_code,
            start_date=start_date,
            end_date=end_date,
        )

    def minutes_book(
        self,
        minutes_book_id: int,
        *,
        refresh: bool = False,
    ) -> MinutesBook | None:
        """One full book, cached for ``roster_ttl``.

        A book is large — Senate book 3861 runs to 195 entries — so it is
        worth keeping between calls.

        Args:
            minutes_book_id: ``MinutesBookID`` from :meth:`minutes_books`.
            refresh: Fetch the book again even when cached.
        """
        return self._memo(
            self._minutes,
            self._minutes_locks,
            minutes_book_id,
            lambda: self.client.get_minutes_book(minutes_book_id),
            refresh=refresh,
        )

    def minutes_records(
        self,
        minutes_book_id: int,
        *,
        bills_only: bool = True,
        refresh: bool = False,
    ) -> list[MinutesRecord]:
        """Every action in one book, flattened, in book order.

        This is the layer that answers "what did this body do, and how did
        each member vote on it".  Each record carries the book, the entry
        that names the bill, and the activity that names the vote.  Feed
        :attr:`MinutesRecord.vote_id` to :meth:`roll_call`; unlike a bill
        event's vote id, this one belongs to the bill on the record.

        Senate book 3861 holds 205 activities and yields 190 bill records,
        169 of them naming a vote and 14 of those cast in a block.

        Args:
            minutes_book_id: ``MinutesBookID`` from :meth:`minutes_books`.
            bills_only: Drop procedural entries — recesses, prayers, orders
                of business — that name no bill.  Pass ``False`` for the
                whole day's proceedings.
            refresh: Fetch the book again even when cached.
        """
        book = self.minutes_book(minutes_book_id, refresh=refresh)
        if book is None:
            return []

        entries = book.bill_entries if bills_only else book.entries

        return [
            MinutesRecord(book, entry, activity)
            for entry in entries
            for activity in entry.MinutesActivities
        ]

    def committee_staff(
        self,
        committee: int | str,
        *,
        chamber_code: str | None = None,
        staff_role_type_id: int | None = None,
    ) -> list[Personnel]:
        """Who clerks or staffs a committee.

        This is the only place LIS says it. ``AffiliationID`` on a personnel
        row is a ``CommitteeID``, and all 34 distinct values resolve, ten of
        them to subcommittees the committee list does not return.

        The data is not session scoped, so there is no session argument. Rows
        carry an ``EffectiveBeginDate`` if you need to age them.

        Args:
            committee: A ``CommitteeID``, or a number or name for
                :meth:`resolve_committee` (``"H08"``, ``"Courts of Justice"``).
            chamber_code: Disambiguates a name both chambers use.
            staff_role_type_id: ``1`` clerks, ``2`` staff. Omit for both.

        Raises:
            CommitteeNotFoundError: A name or number matches nothing, or more
                than one committee.
        """
        if isinstance(committee, str):
            committee_id = self.resolve_committee(committee, chamber_code).CommitteeID
        else:
            committee_id = committee

        return self.client.get_personnel(
            affiliation_id=committee_id,
            staff_role_type_id=staff_role_type_id,
        )

    def member_office(self, member_id: int) -> Contact | None:
        """A member's district office address and phone, or ``None``.

        The district office is the part of a member's contact detail that
        lives nowhere else in LIS. A :class:`~va_lis_client.models.member.Member`
        already carries ``GABEmailAddress`` and ``GABPhoneNumber``, and the
        email agrees on 115 of 124 rows, so the address and the district
        phone are the reason to call this.

        **Many members have no row.** 390 member contacts exist against the
        148 members sitting in 20261, so the set is part historical and part
        incomplete. Member 503 answers and member 527 does not.

        Args:
            member_id: ``MemberID`` from :meth:`find_members` or a roll call.
        """
        contacts = self.client.get_contacts(member_id, _MEMBER_OWNER_TYPE)

        return next((c for c in contacts if c.ContactTypeID == _MEMBER_MAILING), None)

    def calendar_actions_by_id(self) -> dict[int, list[CalendarAction]]:
        """The action vocabulary indexed by ``CalendarActionID``, fetched once.

        This is what turns a minutes activity's ``CalendarActionID`` into a
        label. The fetch is 4,952 rows and roughly 2 MB with no filter
        available, so it is kept for the life of the instance.

        **The value is a list, not a single row.** Those 4,952 rows hold only
        490 distinct ids, because LIS emits one row per calendar category the
        action can appear under. Id 1016 arrives 84 times, identical except
        for the category, all carrying ``EventCode`` ``"H4000"``. This is the
        same one-to-many shape as :meth:`event_types_by_code`.
        """
        with self._reference_lock:
            if self._calendar_actions is None:
                index: dict[int, list[CalendarAction]] = {}
                for action in self.client.get_calendar_actions():
                    index.setdefault(action.CalendarActionID, []).append(action)

                self._calendar_actions = index

            return self._calendar_actions

    def action_for(self, activity: MinutesActivity) -> CalendarAction | None:
        """The vocabulary row describing one minutes activity.

        Picks the row whose ``EventCode`` matches the activity's, which is
        what disambiguates the repeated ids. Against Senate book 3861, 204 of
        205 activities resolved and the event code agreed on every one.

        Returns ``None`` when the activity names no action, or when the id is
        absent from the vocabulary.
        """
        if activity.CalendarActionID is None:
            return None

        rows = self.calendar_actions_by_id().get(activity.CalendarActionID)
        if not rows:
            return None

        if activity.EventCode:
            match = next((r for r in rows if r.EventCode == activity.EventCode), None)
            if match is not None:
                return match

        return rows[0]

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
            self._committees.clear()
            self._committee_rosters.clear()
            self._dockets.clear()
            self._minutes.clear()
            self._legislation_numbers.clear()

        with self._reference_lock:
            self._session_ids = None
            self._calendar_actions = None
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

    def _memo(self, store, locks, key, fetch, *, refresh: bool = False):
        """``store[key]`` while it is younger than ``roster_ttl``, else ``fetch()``.

        One lock per key, so a cold fetch for one key does not block another.
        """
        if not refresh:
            cached = store.get(key)
            if cached is not None and time.monotonic() - cached[0] < self.roster_ttl:
                return cached[1]

        with self._guard:
            lock = locks.setdefault(key, threading.Lock())

        with lock:
            # Another thread may have filled the slot while this one waited.
            if not refresh:
                cached = store.get(key)
                if cached is not None and time.monotonic() - cached[0] < self.roster_ttl:
                    return cached[1]

            value = fetch()
            store[key] = (time.monotonic(), value)
            return value

    def _committee_by_id(self, committee_id: int, session_code: int) -> Committee:
        """The committee row for an ID, from the cached list or the by-id call."""
        for committee in self.committees(include_subcommittees=True):
            if committee.CommitteeID == committee_id:
                return committee

        committee = self.client.get_committee(committee_id, self.session_id(session_code))
        if committee is None:
            raise CommitteeNotFoundError(f"No committee has CommitteeID {committee_id}.")

        return committee

    def _legislation_ids(self, session_code: int, *, refresh: bool = False) -> dict[str, int]:
        """Bill number to ``LegislationID`` for a session, from the cheap number list."""
        return self._memo(
            self._legislation_numbers,
            self._legislation_number_locks,
            session_code,
            lambda: {
                row.LegislationNumber.upper(): row.LegislationID
                for row in self.client.get_legislative_numbers(session_code=session_code)
            },
            refresh=refresh,
        )

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
