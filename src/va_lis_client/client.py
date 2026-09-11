"""
LIS API client.

Docs:  https://lis.virginia.gov/developers
Auth:  ``WebAPIKey`` header on every request.
Data:  Session reference list reaches back to 1994.  Bill data observed
       working for the 2024–2027 sessions (as of Aug 2026); earlier
       guidance said 2025–2026 only.  Older sessions via legacy CSV.

The client wraps a requests session with a system-cert SSL adapter (required
on Windows for government CA roots) and returns parsed Pydantic models.

Identifier quick-reference
--------------------------
Most query parameters accept **either** a surrogate PK **or** a
human-readable code.  Common patterns:

======================  ====================  ========================
Parameter               Example               Notes
======================  ====================  ========================
``sessionCode``         ``20261``             Year + sequence.  Str or int.
``sessionID``           ``59``                Surrogate PK.
``legislationNumber``   ``"HB1"``            **Unpadded** — not ``HB0001``.
``legislationID``       ``98525``             Surrogate PK for the *logical*
                                              bill — reused across carry-over
                                              (see gotchas below).
``legislationTextID``   ``257719``            PK for a specific text version.
======================  ====================  ========================

Gotchas discovered by hitting the live API:

- Bill numbers must be **unpadded**: ``HB1`` works, ``HB0001`` returns 204.
- Text/summary endpoints require ``sessionCode`` or ``sessionID``.  Passing
  ``legislationID`` alone returns 400 on the text endpoint.
- Response envelope keys differ from the OpenAPI spec.  E.g. the docs say
  ``ListItems`` everywhere, but the real keys are ``Sessions``,
  ``Legislations``, ``LegislationTextList``, ``TextsList``,
  ``LegislationSummaries``, ``References``, ``LegislationVersionList``,
  ``PartnerList``.
- **Carry-over reuses** ``LegislationID`` (confirmed live 2026-08-12): a
  bill continued from an even-year session keeps its ID in the following
  odd-year session, so the same ID appears in *both* sessions' bill lists
  (e.g. HB9, ID ``98631``, in ``20261`` and ``20271``).  Carry-over is
  even→odd only, never twice, and never across a two-year GA term — so an
  ID is unique within a term but is NOT one-session-one-record.
  Session-scoped identity is ``(SessionCode, LegislationNumber)`` or
  ``(SessionCode, LegislationID)``.
- ``get_bill`` returns top-level ``SessionID``/``SessionCode`` as ``None``.
  Session membership — including carry-over lineage — lives in the
  ``Sessions`` cross-reference list (two entries for a carried-over bill).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime

import requests as _requests

from va_lis_client.exceptions import LISClientError
from va_lis_client.http import requests_session
from va_lis_client.models import (
    ActorType,
    CalendarCategoryType,
    CalendarDetail,
    CalendarItem,
    CalendarType,
    Committee,
    CommitteeAction,
    CommitteeMember,
    CommitteeRole,
    District,
    DocketDetail,
    DocketListItem,
    Heartbeat,
    Legislation,
    LegislationEvent,
    LegislationEventType,
    LegislationStatus,
    LegislationSummary,
    LegislationSummaryItem,
    LegislationTextDetail,
    LegislationTextItem,
    LegislationVersion,
    MeetingRoom,
    Member,
    MemberLegislation,
    MemberVoteResult,
    PagedList,
    Pagination,
    Partner,
    Party,
    Patron,
    PatronRole,
    Schedule,
    ScheduleType,
    Session,
    Vote,
    VoteType,
    page_request_header,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://lis.virginia.gov"

# Retry configuration
_MAX_RETRIES = 8
_INITIAL_DELAY = 2  # seconds
_BACKOFF_FACTOR = 2
_MAX_DELAY = 300  # 5 minutes
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class LISClient:
    """Low-level HTTP client for the LIS REST API.

    Usage::

        from va_lis_client import LISClient

        # Pass the API key explicitly, or set the LIS_API_KEY env var
        client = LISClient(api_key="your-key-here")
        # or:
        # export LIS_API_KEY=your-key-here
        client = LISClient()

        # Check key validity (heartbeats don't validate!)
        partner = client.check_api_key()

        # Get 2026 sessions
        sessions = client.get_sessions(year=2026)

        # List all bills in 2026 Regular Session
        bills = client.get_session_bills(session_code=20261)

        # Full detail + patron list for HB1
        bill = client.get_bill(legislation_id=98525)

        # Bill text (HTML) for all versions of HB1
        texts = client.get_bill_text_detail(
            legislation_id=98525, session_code=20261,
        )

        # Bill summaries (introduced, passed) for HB1
        summaries = client.get_bill_summaries(
            legislation_number="HB1", session_code=20261,
        )
    """

    def __init__(self, api_key: str | None = None, max_retries: int = _MAX_RETRIES):
        self.session = requests_session
        self.api_key = api_key if api_key is not None else os.environ.get("LIS_API_KEY")
        self._max_retries = max_retries
        if not self.api_key:
            raise LISClientError(
                "LIS API key is not configured. "
                "Pass it as api_key= or set the LIS_API_KEY environment variable."
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"WebAPIKey": self.api_key}

    def _request(
        self,
        method: str,
        path: str,
        *,
        page_header: dict[str, str] | None = None,
        capture: dict | None = None,
        **kwargs,
    ):
        """Send an HTTP request with exponential backoff on transient failures.

        Retries on connection errors, timeouts, and HTTP 429/5xx responses.
        Respects ``Retry-After`` header when present.  Logs all response
        headers on 429 to help discover LIS rate-limit SLAs.

        Args:
            page_header: An ``X-Pagination`` request header from
                :func:`page_request_header`, merged over the auth header.
            capture: A dict to fill with the response headers, so a caller can
                read ``X-Pagination`` back off a successful response.
        """
        url = f"{BASE_URL}{path}"
        headers = self._headers()
        if page_header:
            headers.update(page_header)
        kwargs.setdefault("headers", headers)
        delay = _INITIAL_DELAY

        for attempt in range(self._max_retries + 1):
            try:
                resp = self.session.request(method, url, **kwargs)
            except (_requests.ConnectionError, _requests.Timeout) as exc:
                if attempt == self._max_retries:
                    raise
                logger.warning(
                    "LIS request %s %s failed (attempt %d/%d): %s — retrying in %ds",
                    method,
                    path,
                    attempt + 1,
                    self._max_retries,
                    exc,
                    delay,
                )
                time.sleep(delay)
                delay = min(delay * _BACKOFF_FACTOR, _MAX_DELAY)
                continue

            if resp.status_code == 204:
                return None

            if resp.status_code not in _RETRYABLE_STATUS_CODES:
                resp.raise_for_status()
                if capture is not None:
                    capture.update(resp.headers)
                return resp.json()

            # Retryable HTTP error
            if attempt == self._max_retries:
                resp.raise_for_status()

            # Log rate-limit details on 429 so we can discover SLAs
            if resp.status_code == 429:
                logger.warning(
                    "LIS 429 rate-limited on %s %s — response headers: %s",
                    method,
                    path,
                    dict(resp.headers),
                )

            # Respect Retry-After header if present
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = int(retry_after)
                except ValueError:
                    wait = delay
                wait = min(wait, _MAX_DELAY)
            else:
                wait = delay

            logger.warning(
                "LIS %d on %s %s (attempt %d/%d) — retrying in %ds",
                resp.status_code,
                method,
                path,
                attempt + 1,
                self._max_retries,
                wait,
            )
            time.sleep(wait)
            delay = min(delay * _BACKOFF_FACTOR, _MAX_DELAY)

        # Should not reach here, but just in case
        raise LISClientError(
            f"LIS request {method} {path} failed after {self._max_retries} retries"
        )

    def _get(
        self,
        path: str,
        params: dict | None = None,
        *,
        page_header: dict[str, str] | None = None,
        capture: dict | None = None,
    ):
        return self._request("GET", path, params=params, page_header=page_header, capture=capture)

    def _post(self, path: str, json: dict | None = None):
        return self._request("POST", path, json=json)

    # ------------------------------------------------------------------
    # Health / auth verification
    # ------------------------------------------------------------------

    def check_api_key(self) -> Partner | None:
        """Validate the API key via PartnerAuthentication.

        Returns the :class:`Partner` on success, or ``None`` if the key is
        not registered (HTTP 204).

        **Important:** Heartbeat endpoints accept *any* key — even garbage.
        This is the only endpoint that actually validates the key.
        """
        url = f"{BASE_URL}/PartnerAuthentication/api/checkpartnerkeyasync/{self.api_key}"
        resp = self.session.get(url)
        resp.raise_for_status()
        if resp.status_code == 204:
            return None
        data = resp.json()
        partners = data.get("PartnerList", [])
        if not partners:
            return None
        return Partner.model_validate(partners[0])

    def heartbeat(self, service: str = "Legislation") -> Heartbeat:
        """Hit a service heartbeat endpoint.

        Args:
            service: Service name, e.g. ``"Legislation"``, ``"Session"``,
                ``"Authentication"``.  The Authentication service uses a
                different path (``/heartbeat/heartbeatsync``).

        Note: heartbeats do NOT validate the API key.
        """
        if service == "Authentication":
            path = f"/{service}/api/heartbeat/heartbeatsync"
        else:
            path = f"/{service}/api/heartbeatasync"
        data = self._get(path)
        return Heartbeat.model_validate(data)

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def get_sessions(self, year: int | str = "") -> list[Session]:
        """Return legislative sessions, optionally filtered by year.

        Args:
            year: Calendar year (e.g. ``2026``).  Omit to get all available.

        Returns:
            List of :class:`Session` models.  For 2026 this returns
            two entries: Regular Session (``20261``) and Special Session I
            (``20262``).

        Envelope key: ``Sessions``.
        """
        params = {"year": str(year)} if year else None
        data = self._get("/Session/api/getsessionlistasync", params=params)
        if data is None:
            return []
        return [Session.model_validate(s) for s in data.get("Sessions", [])]

    def get_default_session(self) -> Session | None:
        """Return the current/default legislative session.

        LIS marks exactly one session as ``IsDefault=True`` at any time.

        The default tracks the General Assembly's *working* session, not
        the most recently convened one.  Once a session wraps up (sine
        die, then the April reconvened session), the default advances to
        the upcoming session during the interim — observed pointing at
        ``20271`` by August 2026, months before that session convenes.
        That is where continued bills, interim committee action, and
        (from mid-November) new prefiles accumulate.  For retrospective
        work on a just-ended session (enactments, final statuses), pass
        its explicit ``session_code`` instead of using the default.

        Envelope key: ``Sessions`` (single-element list).
        """
        data = self._get("/Session/api/getdefaultsessionasync")
        if data is None:
            return None
        sessions = data.get("Sessions", [])
        return Session.model_validate(sessions[0]) if sessions else None

    # ------------------------------------------------------------------
    # Legislation
    # ------------------------------------------------------------------

    def get_session_bills(
        self,
        session_code: int | None = None,
        session_id: int | None = None,
        *,
        page_size: int | None = None,
        skip: int | None = None,
    ) -> PagedList[LegislationSummaryItem]:
        """Lightweight bill list for a session.

        Provide **one of**:

        - ``session_code``: e.g. ``20261`` (2026 Regular Session)
        - ``session_id``: e.g. ``59``

        Returns every bill in the session with basic metadata and the
        chief patron.  For full detail (all patrons, dates, status ID),
        call :meth:`get_bill` with the ``LegislationID``.

        **Size:** the unpaged response is large.  Session ``20271`` measured
        363,344 bytes for 443 rows (about 820 bytes per row), which scales to
        roughly 3 MB for a full regular session.  Pass ``page_size`` to trim
        it.

        **Paging** rides an ``X-Pagination`` request header, not the query
        string; see :mod:`va_lis_client.models.pagination`.  The returned
        :class:`PagedList` is a plain list that also carries ``.pagination``
        with ``TotalCount`` and ``HasNext``.

        Args:
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.
            page_size: Maximum rows to return.  Omit for every row.
            skip: Records to skip before the window starts.  This is a raw
                record offset, not a page index.

        **Carry-over:** an odd-year session's list starts as pure
        carry-over — every bill continued from the preceding even-year
        session appears with its ``LegislationID`` unchanged (as of Aug
        2026, ``20271`` returned 443 bills, all sharing IDs with
        ``20261``'s 3,645).  See the module docstring for the identity
        rules.

        Envelope key: ``Legislations``.
        """
        params = {}
        if session_code is not None:
            params["sessionCode"] = session_code
        if session_id is not None:
            params["sessionID"] = session_id

        headers: dict = {}
        data = self._get(
            "/Legislation/api/getlegislationsessionlistasync",
            params=params,
            page_header=page_request_header(page_size, skip),
            capture=headers,
        )
        if data is None:
            return PagedList()

        rows = [
            LegislationSummaryItem.model_validate(item) for item in data.get("Legislations", [])
        ]
        return PagedList(rows, Pagination.from_header(headers.get("X-Pagination")))

    def get_session_bill_count(
        self,
        session_code: int | None = None,
        session_id: int | None = None,
    ) -> int | None:
        """Number of bills in a session, without downloading the list.

        Asks for a single row and reads ``TotalCount`` off the response's
        ``X-Pagination`` header, so this costs about a kilobyte rather than
        the megabytes a full list costs.

        Returns:
            The row count, or ``None`` when the server sent no usable header.
        """
        page = self.get_session_bills(
            session_code=session_code,
            session_id=session_id,
            page_size=1,
        )

        return page.pagination.TotalCount if page.pagination else None

    def get_bill(self, legislation_id: int) -> Legislation | None:
        """Full bill detail by numeric LIS ID.

        Args:
            legislation_id: Surrogate PK, e.g. ``98525`` for HB1 in 20261.
                Get this from :meth:`get_session_bills` or from an existing
                :class:`LegislationSummaryItem`.

        Returns:
            :class:`Legislation` with all patrons (65 for HB1!), session
            cross-refs, and status info.  ``None`` if not found.

        Top-level ``SessionID``/``SessionCode`` are ``None`` in observed
        responses.  Session membership comes from the ``Sessions`` list,
        which holds one entry per session the bill appears in — two for a
        bill carried over from an even-year session (e.g. ``98631`` lists
        ``20261`` and ``20271``).

        Envelope key: ``Legislations``.
        """
        data = self._get(f"/Legislation/api/getlegislationbyidasync/{legislation_id}")
        if data is None:
            return None
        items = data.get("Legislations", [])
        return Legislation.model_validate(items[0]) if items else None

    def get_legislation_statuses(self) -> list[LegislationStatus]:
        """Reference list of all 52 legislation statuses.

        No parameters needed.  Returns statuses like Introduced (1),
        In Committee (2), Passed Both (6), Approved (8), Failed (9), etc.

        Envelope key: ``References``.
        """
        data = self._get("/Legislation/api/getlegislationstatuslistasync")
        if data is None:
            return []
        return [LegislationStatus.model_validate(r) for r in data.get("References", [])]

    # ------------------------------------------------------------------
    # Legislation text
    # ------------------------------------------------------------------

    def get_legislation_versions(self) -> list[LegislationVersion]:
        """Reference list of the 13 legislation version types.

        No parameters needed.  Returns version types like Introduced (1,
        suffix=None), Engrossed (2, suffix="E"), Enrolled (3, suffix="ER"),
        Substitute (5, suffix="S"), etc.

        The ``Suffix`` is appended to the bill number to form a
        ``DocumentCode``: e.g. ``HB1`` + ``"ER"`` → ``"HB1ER"``.

        Envelope key: ``LegislationVersionList``.
        """
        data = self._get("/LegislationText/api/getlegislationversionlistasync")
        if data is None:
            return []
        return [
            LegislationVersion.model_validate(v) for v in data.get("LegislationVersionList", [])
        ]

    def get_bill_texts(
        self,
        *,
        legislation_number: str | None = None,
        legislation_id: int | None = None,
        session_code: int | None = None,
        session_id: int | None = None,
    ) -> list[LegislationTextItem]:
        """List text versions for a bill (introduced, enrolled, etc.).

        Provide a session identifier (``session_code`` or ``session_id``)
        **plus** a bill identifier (``legislation_number`` or ``legislation_id``).

        Args:
            legislation_number: Unpadded bill number, e.g. ``"HB1"``.
                **Not** ``"HB0001"`` — padded numbers return 204.
            legislation_id: Surrogate PK, e.g. ``98525``.
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.

        Returns:
            One :class:`LegislationTextItem` per version.  HB1 in 20261
            returns two: Introduced (``DocumentCode="HB1"``) and
            Enrolled (``DocumentCode="HB1ER"``).

        Envelope key: ``LegislationTextList``.
        """
        params: dict = {}
        if legislation_number is not None:
            params["legislationNumber"] = legislation_number
        if legislation_id is not None:
            params["legislationID"] = legislation_id
        if session_code is not None:
            params["sessionCode"] = session_code
        if session_id is not None:
            params["sessionID"] = session_id
        data = self._get("/LegislationText/api/getlegislationtextlistasync", params=params)
        if data is None:
            return []
        return [LegislationTextItem.model_validate(t) for t in data.get("LegislationTextList", [])]

    def get_bill_text_detail(
        self,
        *,
        legislation_id: int,
        session_code: int,
    ) -> list[LegislationTextDetail]:
        """Full text detail including the bill body HTML.

        Args:
            legislation_id: Surrogate PK, e.g. ``98525``.
            session_code: e.g. ``20261``.

        Returns:
            One :class:`LegislationTextDetail` per version.  The
            ``DraftText`` field contains the full bill text as HTML with
            additions (``<em class="new">``) and deletions (``<s>``).

        Envelope key: ``TextsList``.
        """
        data = self._get(
            "/LegislationText/api/getlegislationtextbyidasync",
            params={
                "legislationID": legislation_id,
                "sessionCode": session_code,
            },
        )
        if data is None:
            return []
        return [LegislationTextDetail.model_validate(t) for t in data.get("TextsList", [])]

    # ------------------------------------------------------------------
    # Legislation summary
    # ------------------------------------------------------------------

    def get_bill_summaries(
        self,
        *,
        legislation_number: str | None = None,
        legislation_id: int | None = None,
        session_code: int | None = None,
        session_id: int | None = None,
    ) -> list[LegislationSummary]:
        """Bill summaries (introduced, passed, etc.).

        Provide a session identifier **plus** a bill identifier.

        Args:
            legislation_number: Unpadded, e.g. ``"HB1"``.
            legislation_id: Surrogate PK, e.g. ``98525``.
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.

        Returns:
            One :class:`LegislationSummary` per summary version.  HB1 in
            20261 returns two: ``"SUMMARY AS INTRODUCED"`` (``IsActive=False``)
            and ``"SUMMARY AS PASSED"`` (``IsActive=True``).  The active
            summary is the current/latest one.

            ``Summary`` is HTML (``<p class="sumtext">...</p>``).

        Envelope key: ``LegislationSummaries``.
        """
        params: dict = {}
        if legislation_number is not None:
            params["legislationNumber"] = legislation_number
        if legislation_id is not None:
            params["legislationID"] = legislation_id
        if session_code is not None:
            params["sessionCode"] = session_code
        if session_id is not None:
            params["sessionID"] = session_id
        data = self._get(
            "/LegislationSummary/api/getlegislationsummarylistasync",
            params=params,
        )
        if data is None:
            return []
        return [LegislationSummary.model_validate(s) for s in data.get("LegislationSummaries", [])]

    # ------------------------------------------------------------------
    # Legislation events (bill action history)
    # ------------------------------------------------------------------

    def get_bill_events(self, legislation_id: int) -> list[LegislationEvent]:
        """Chronological action history for a bill.

        Returns every event from prefiling through governor action.
        This is the **primary source for passage dates** — the
        ``HousePassageDate`` / ``SenatePassageDate`` fields on
        ``Legislation`` are always null.

        Look for events where ``Status`` is ``"Passed House"`` or
        ``"Passed Senate"`` and read ``EventDate`` for the passage date.
        ``VoteTally`` contains the vote count (e.g. ``"(64-Y 34-N 0-A)"``).

        ``Status`` is the internal status *name* from the 52-value
        LegislationStatus vocabulary — a closed vocabulary, not free
        text.  Match on it rather than ``LegislationStatusID``, which
        this endpoint returns null (verified 2026-08-13).  ``EventDate``
        is the true action date (a committee continuance is dated on the
        committee vote, not on crossover); whether LIS publishes events
        promptly mid-session or batches them at crossover is not
        verifiable from post-session data.

        Args:
            legislation_id: Surrogate PK, e.g. ``98525`` for HB1.

        Returns:
            List of :class:`LegislationEvent` in chronological order.

        Envelope key: ``LegislationEvents``.
        """
        data = self._get(
            "/LegislationEvent/api/getlegislationeventbylegislationidasync",
            params={"legislationID": legislation_id},
        )
        if data is None:
            return []
        return [LegislationEvent.model_validate(e) for e in data.get("LegislationEvents", [])]

    def get_event_types(self) -> list[LegislationEventType]:
        """Reference list of 3,912 legislation event types.

        Key fields for filtering:

        - ``IsPassage``: True for passage/defeat events (H5000/S5000 family)
        - ``EventCode``: Prefix = actor (H=House, S=Senate, G=Governor)
        - ``LegislationDescription``: Human-readable action name

        The continuance (carry-forward) family is 120 types with
        committee-numbered codes ending 40, 41, and 42; see
        :class:`LegislationEventType` for the breakdown.  Rows come back
        with ``LegislationEventTypeID`` null, so join to events on
        ``EventCode``.

        Envelope key: ``EventTypes``.
        """
        data = self._get(
            "/LegislationEvent/api/getlegislationeventtypereferencesasync",
        )
        if data is None:
            return []
        return [LegislationEventType.model_validate(t) for t in data.get("EventTypes", [])]

    def get_actor_types(self) -> list[ActorType]:
        """Reference list of 5 actor types (House, Senate, Committee, etc.).

        Envelope key: ``ActorTypes``.
        """
        data = self._get(
            "/LegislationEvent/api/getactortypereferencesasync",
        )
        if data is None:
            return []
        return [ActorType.model_validate(a) for a in data.get("ActorTypes", [])]

    # ------------------------------------------------------------------
    # Votes (per-member roll calls)
    # ------------------------------------------------------------------

    def get_vote(self, vote_id: int) -> Vote | None:
        """A single vote record, with its per-member roll call.

        This is the only route to per-member votes, and it covers both
        chambers for floor votes and committee votes alike.  The ``/Vote``
        service does not appear in the LIS developer portal service list.

        **No endpoint accepts a ``legislationID``.**  Reach a vote through
        ``LegislationEvent.VoteID``: call
        :meth:`get_bill_events`, then this method for each event that
        carries one.

        **A VoteID on a bill event is often not a roll call for that bill.**
        Check the returned record before you attribute it:

        - ``IsVoice`` — no members are recorded at all.
        - ``IsBlock`` — one roll call disposing of many bills; read
          ``vote_legislation`` to see how many.
        - ``ResponseCode`` ``"X"`` is excluded from ``VoteTally``, so the
          member rows do not sum to the tally string.

        **Votes reach back to 1994**, three decades before the bill data.
        ``voteID=1`` is a 1994 committee vote, so do not assume a vote ID
        belongs to a recent session.

        Args:
            vote_id: Surrogate PK from an event, e.g. ``300418``.

        Returns:
            The :class:`Vote`, or ``None`` when LIS holds no such ID (it
            answers 204 No Content).

        Raises:
            requests.HTTPError: ``vote_id=0`` returns HTTP 400
                ``"Failed, Database Error (51000)"`` rather than an empty
                result.

        Envelope key: ``Votes`` — a list holding exactly one vote.
        """
        data = self._get("/Vote/api/getvotebyidasync", params={"voteID": vote_id})
        if data is None:
            return None

        votes = data.get("Votes", [])
        if not votes:
            return None

        return Vote.model_validate(votes[0])

    def get_vote_types(self) -> list[VoteType]:
        """Reference list of 3 vote types (Committee, Subcommittee, Floor).

        Envelope key: ``VoteTypes``.
        """
        data = self._get("/Vote/api/getvotetypereferencesasync")
        if data is None:
            return []
        return [VoteType.model_validate(t) for t in data.get("VoteTypes", [])]

    # ------------------------------------------------------------------
    # Members (the roster behind a roll call)
    # ------------------------------------------------------------------

    def get_members(
        self,
        session_code: int,
        chamber_code: str | None = None,
    ) -> list[Member]:
        """The roster for a session, with party and district.

        This is the authoritative member source and the join target for
        ``VoteMember.MemberID``.  Prefer it over :meth:`get_member`, which
        answers 204 for some sitting members.

        **The roster holds more than the sitting membership, on purpose.**
        Session 20261 returns 148 rows for 140 seats, because members who
        left or arrived mid-session are still there, and one member who moved
        from the House to the Senate appears under two member numbers.  A
        member who resigned in February still cast the January votes, so keep
        the extra rows rather than filtering on ``MemberStatus``.

        The undocumented ``chamberCode`` parameter works: it returns 106 House
        or 42 Senate rows against 148 unfiltered (measured 2026-09-08).

        ``/Member/api/getmemberlistasync`` returns the same rows under
        ``ShallowMembers`` with a strict subset of the fields — it drops
        ``DistrictID`` and ``DistrictName`` — so this client does not wire it.

        Args:
            session_code: Required.  Omitting it returns HTTP 400.
            chamber_code: ``"H"`` or ``"S"``.  Omit for both.

        Envelope key: ``Members``.
        """
        params: dict[str, str | int] = {"sessionCode": session_code}
        if chamber_code:
            params["chamberCode"] = chamber_code

        data = self._get("/Member/api/getmembersasync", params=params)
        if data is None:
            return []
        return [Member.model_validate(m) for m in data.get("Members", [])]

    def get_member(self, member_id: int, session_code: int) -> Member | None:
        """One member, with the fields the roster list leaves null.

        **This endpoint is not reliable enough to build on.**  It answered 204
        for 9 of the 148 members on the 2026 roster, including sitting ones
        (measured 2026-09-08).  Use :meth:`get_members` as the source of truth
        and treat this as an enrichment.

        What it adds over a list row: ``MemberDetailID``, plus values for
        ``ChamberName``, ``SeatNumber``, ``VotingSequence``, and ``SessionID``,
        which the list returns null for every member.

        Returns:
            The :class:`Member`, or ``None`` when LIS answers 204.

        Envelope key: ``Members`` — a list holding one member.
        """
        data = self._get(
            "/Member/api/getmemberbyidasync",
            params={"memberID": member_id, "sessionCode": session_code},
        )
        if data is None:
            return None

        members = data.get("Members", [])
        if not members:
            return None

        return Member.model_validate(members[0])

    def get_parties(self) -> list[Party]:
        """Reference list of 3 parties (D Democrat, I Independent, R Republican).

        Join to ``Member.PartyCode``.  Envelope key: ``Parties``.
        """
        data = self._get("/Member/api/getpartyreferencesasync")
        if data is None:
            return []
        return [Party.model_validate(p) for p in data.get("Parties", [])]

    def get_member_votes(self, member_id: int, session_code: int) -> list[MemberVoteResult]:
        """Every vote one member cast in a session — the member-first axis.

        :meth:`get_vote` answers "who voted on this bill".  This answers the
        other direction.  The two agree: a member's rows for a bill carry the
        same ``VoteID`` values a bill-first lookup returns.

        **A row is one (vote, bill) pair, not one vote.**  A block vote repeats
        once per bill it disposed of, so member 503's 2026 history holds 2,910
        rows over only 2,293 distinct ``VoteID`` values, with one vote
        appearing 105 times.  Count distinct ``VoteID`` to count votes.

        **Filter on ``LegislationNumber``, not ``ClassificationName``.**  The
        classification is null on all 688 committee and subcommittee rows,
        which are still votes on bills.  Only the 43 attendance roll calls
        lack a ``LegislationNumber``.

        This is the heaviest response in the API at roughly **1.9 MB per
        member**, and there is no way to ask for several members at once:
        both parameters are required, and omitting either returns HTTP 400.
        ``chamberCode`` is accepted and ignored.  Prefer
        :meth:`LISService.member_votes`, which caches.

        Args:
            member_id: ``MemberID`` from the roster or a ballot.
            session_code: e.g. ``20261``.  Required.

        Envelope key: ``MemberVoteList`` — one row, whose ``VoteResult`` holds
        the votes.  This returns that inner list.
        """
        data = self._get(
            "/MemberVoteSearch/api/getmembervotelistasync",
            params={"memberID": member_id, "sessionCode": session_code},
        )
        if data is None:
            return []

        rows = data.get("MemberVoteList", [])
        if not rows:
            return []

        return [MemberVoteResult.model_validate(v) for v in rows[0].get("VoteResult", [])]

    def get_districts(self) -> list[District]:
        """Reference list of 140 districts — 100 House and 40 Senate.

        A district row labels itself ``Title``; the same value appears on a
        member as ``DistrictName``.

        Envelope key: ``Districts``.
        """
        data = self._get("/Member/api/getdistrictreferencesasync")
        if data is None:
            return []
        return [District.model_validate(d) for d in data.get("Districts", [])]

    # ------------------------------------------------------------------
    # Bills by member (the member-first bill axis)
    # ------------------------------------------------------------------

    def get_member_legislation(
        self,
        member_id: int,
        session_id: int,
        patron_type_id: int | None = None,
    ) -> list[MemberLegislation]:
        """Every bill a member patrons in a session, in any role.

        The bill-first list (:meth:`get_session_bills`) names only the chief
        patron, so this is the only route to a member's co-patronage without
        one detail call per bill.

        **Pass ``session_id``, never a session code.**  The endpoint accepts
        ``sessionCode`` too, but it caches each answer under a key naming the
        member and the ``sessionID`` only.  A ``sessionCode`` call is honored
        on a cache miss and then stored under the member alone, so every
        later ``sessionCode`` call for that member, from any partner, gets
        the first session's answer back.  Verified 2026-09-11: member 186
        queried with ``sessionCode=20251`` and then ``20271`` got the same 225
        rows of session 57 both times, while ``sessionID=61`` got 25 rows of
        session 61.  :meth:`LISService.session_id` resolves a code.

        **Never call this without a member.**  With no parameters the server
        sets out to return every bill for every member, and the request hangs
        past 60 seconds.  An unknown ``member_id`` answers 204.

        **One row per summary version.**  A bill with three published
        summaries appears three times; see :class:`MemberLegislation`.  The
        rows carry the summary HTML, so the response is heavy: 334 KB for
        member 544's 236 rows.  Prefer :meth:`LISService.member_bills`, which
        caches and collapses.

        Args:
            member_id: ``MemberID`` from the roster.
            session_id: ``Session.SessionID``, e.g. ``59`` for 20261.
            patron_type_id: Restrict to one role: ``1`` chief patron, ``2``
                chief co-patron, ``4`` co-patron (see ``PATRON_ROLES``).
                Omit for every role.

        Envelope key: ``Legislations``.
        """
        params: dict[str, int] = {"memberID": member_id, "sessionID": session_id}
        if patron_type_id is not None:
            params["patronTypeID"] = patron_type_id

        data = self._get("/LegislationByMember/api/getmemberlegislationlistasync", params=params)
        if data is None:
            return []
        return [MemberLegislation.model_validate(r) for r in data.get("Legislations", [])]

    # ------------------------------------------------------------------
    # Patrons
    # ------------------------------------------------------------------

    def get_bill_patrons(self, legislation_id: int) -> list[Patron]:
        """Every patron of a bill, in every role, in display order.

        The same list :meth:`get_bill` carries, without the rest of the
        detail record: 12 rows for HB1408 in 20261, one chief patron and
        eleven co-patrons.  Names arrive padded (``" Charlie Schmidt"``);
        the model strips them.

        The sibling ``getlegislationpatronlistasync`` is not wired.  It serves
        chief patron relationships only (any other ``patronType`` answers
        204), and unfiltered it returns every chief patron in a chamber with
        their bills, 1.3 MB for the 2026 House.

        Args:
            legislation_id: Surrogate PK, e.g. ``100874`` for HB1408 in 20261.

        Envelope key: ``Patrons``.
        """
        data = self._get(f"/LegislationPatron/api/getlegislationpatronsbyidasync/{legislation_id}")
        if data is None:
            return []
        return [Patron.model_validate(p) for p in data.get("Patrons", [])]

    def get_patron_roles(self) -> list[PatronRole]:
        """Reference list of 5 patron roles.

        ``PATRON_ROLES`` in :mod:`va_lis_client.models` holds the same
        vocabulary as a constant, plus the ``0`` that
        ``getmemberpatrontypelistasync`` returns for budget amendment
        requests.  That endpoint (the roles one member holds in a session) is
        not wired.

        Envelope key: ``PatronRolesList``.
        """
        data = self._get("/LegislationPatron/api/getpatronroletypelistasync")
        if data is None:
            return []
        return [PatronRole.model_validate(r) for r in data.get("PatronRolesList", [])]

    # ------------------------------------------------------------------
    # Committees and seats
    # ------------------------------------------------------------------

    def get_committees(
        self,
        chamber_code: str | None = None,
        *,
        session_code: int | None = None,
        parent_committee_id: int | None = None,
        include_subcommittees: bool = False,
    ) -> list[Committee]:
        """The standing committees, and optionally every subcommittee.

        14 House and 11 Senate standing committees; the House list grows to
        67 rows with subcommittees (measured 2026-09-11).

        **The session is ignored.**  The response's cache key reads
        ``{SESSIONID=0}`` whatever ``session_code`` is sent, and 20251 and
        20261 return the same rows.  The list describes the committees that
        exist now; who sits on them is session scoped and comes from
        :meth:`get_committee_members`.

        The list nulls ``EffectiveBeginDate``, ``MeetingNote``, and
        ``IsPublic``.  :meth:`get_committee` and
        :meth:`get_committee_by_number` fill them.

        Args:
            chamber_code: ``"H"`` or ``"S"``.  Omit for both.
            session_code: Accepted and ignored by LIS; sent when given.
            parent_committee_id: Only the subcommittees of one committee.
            include_subcommittees: Add every subcommittee to the list.

        Envelope key: ``Committees``.
        """
        params: dict[str, str | int] = {}
        if chamber_code:
            params["chamberCode"] = chamber_code
        if session_code is not None:
            params["sessionCode"] = session_code
        if parent_committee_id is not None:
            params["parentCommitteeID"] = parent_committee_id
        if include_subcommittees:
            params["includeSubCommittees"] = "true"

        data = self._get("/Committee/api/getcommitteelistasync", params=params or None)
        if data is None:
            return []
        return [Committee.model_validate(c) for c in data.get("Committees", [])]

    def get_committee(self, committee_id: int, session_id: int) -> Committee | None:
        """One committee with the fields the list leaves null.

        Adds ``EffectiveBeginDate``, ``MeetingNote`` (e.g. "Monday,
        Wednesday, and Friday, 1 hour after adjournment"), and ``IsPublic``.

        Args:
            committee_id: e.g. ``8`` for House Courts of Justice.
            session_id: ``Session.SessionID``; the spec marks it required.
                :meth:`LISService.session_id` resolves a code.

        Returns:
            The :class:`Committee`, or ``None`` when LIS answers 204.

        Envelope key: ``Committees`` — a list holding one committee.
        """
        data = self._get(
            "/Committee/api/getcommitteebyidasync",
            params={"id": committee_id, "sessionID": session_id},
        )
        if data is None:
            return None

        rows = data.get("Committees", [])
        return Committee.model_validate(rows[0]) if rows else None

    def get_committee_by_number(
        self,
        committee_number: str,
        effective_date: str | None = None,
    ) -> Committee | None:
        """One committee by its number, e.g. ``"H14"``, with the detail fields.

        Args:
            committee_number: Chamber prefix and number, e.g. ``"S02"``.
            effective_date: Passed through to LIS as ``effectiveDate``.

        Returns:
            The :class:`Committee`, or ``None`` when LIS answers 204.

        Envelope key: ``Committees``.
        """
        params: dict[str, str] = {"committeeNumber": committee_number}
        if effective_date:
            params["effectiveDate"] = effective_date

        data = self._get("/Committee/api/getcommitteesasync", params=params)
        if data is None:
            return None

        rows = data.get("Committees", [])
        return Committee.model_validate(rows[0]) if rows else None

    def get_committee_members(self, committee_id: int, session_code: int) -> list[CommitteeMember]:
        """Who sits on a committee in a session, with their roles.

        Works for subcommittees too: the House Courts of Justice Criminal
        subcommittee returns 11 seats, with the full committee's chair as
        ``Ex-Officio``.  The session filter is real (cache key
        ``{SESSIONID=59}{COMMITTEEID=8}``): Courts of Justice returns 23 seats
        for 20261 and 22 for 20251.

        The rows carry no party or district.  :meth:`LISService.committee_members`
        joins the session roster on.

        Args:
            committee_id: ``CommitteeID`` from :meth:`get_committees`.
            session_code: e.g. ``20261``.  Required: omitting it returns HTTP
                400 "Must provide either a valid Session ID or Session Code".

        Envelope key: ``MemberList``.
        """
        data = self._get(
            "/MembersByCommittee/api/getcommitteememberslistasync",
            params={"committeeID": committee_id, "sessionCode": session_code},
        )
        if data is None:
            return []
        return [CommitteeMember.model_validate(m) for m in data.get("MemberList", [])]

    def get_committee_roles(self) -> list[CommitteeRole]:
        """Reference list of 8 committee roles, with chamber specific IDs.

        A House chair is ``3`` and a Senate chair is ``1``; compare titles,
        not IDs, across chambers.  Envelope key: ``CommitteeRoles``.
        """
        data = self._get("/MembersByCommittee/api/getcommitteerolesasync")
        if data is None:
            return []
        return [CommitteeRole.model_validate(r) for r in data.get("CommitteeRoles", [])]

    def get_committee_actions(self) -> list[CommitteeAction]:
        """Reference list of 41 committee actions.

        This is the only data operation in ``/CommitteeLegislationReferral``.
        The service does not list the bills referred to a committee; that
        question still has no endpoint (probed 2026-09-11).

        Envelope key: ``CommitteeActions``.
        """
        data = self._get("/CommitteeLegislationReferral/api/getcommitteeactionreferencesasync")
        if data is None:
            return []
        return [CommitteeAction.model_validate(a) for a in data.get("CommitteeActions", [])]

    # ------------------------------------------------------------------
    # Meetings: the master schedule (when and where)
    # ------------------------------------------------------------------

    def get_schedules(
        self,
        start_date: str | date | datetime | None = None,
        end_date: str | date | datetime | None = None,
        *,
        owner_id: int | None = None,
        schedule_type_id: int | None = None,
        vote_room_id: int | None = None,
        schedule_ids: str | None = None,
    ) -> list[Schedule]:
        """Meetings from the master schedule: committees, caucuses, floor, other.

        **Always pass a date range.**  With no parameters the endpoint
        returns every meeting it holds, 3,631 rows and 2 MB spanning October
        2022 to December 2026 (measured 2026-09-11).  The week of
        2026-02-02 alone is 147 rows and 80 KB.

        ``owner_id`` is a ``CommitteeID``: ``8`` with the 2026 session dates
        returns the 22 House Courts of Justice meetings.  Rows that are not
        committee meetings omit ``OwnerID`` and ``CommitteeNumber``.

        ``ScheduleTime`` is free text and often blank; ``IsCancelled`` is
        set on cancelled meetings, which stay in the list.  Senate dockets
        appear here too, as type ``"Docket"``.

        Args:
            start_date: Inclusive; a ``date``, ``datetime``, or
                ``"YYYY-MM-DD"``.
            end_date: Inclusive, same forms.
            owner_id: ``CommitteeID`` of the meeting's owner.
            schedule_type_id: 1 Committee, 2 Chamber, 3 Conference, 4 Caucus,
                5 Other, 6 Docket.
            vote_room_id: A room from :meth:`get_meeting_rooms`.
            schedule_ids: Passed through to LIS as ``scheduleIDs``.

        Envelope key: ``Schedules``.
        """
        params: dict[str, str | int] = {}
        if start_date is not None:
            params["startDate"] = _date_param(start_date)
        if end_date is not None:
            params["endDate"] = _date_param(end_date)
        if owner_id is not None:
            params["ownerID"] = owner_id
        if schedule_type_id is not None:
            params["scheduleTypeID"] = schedule_type_id
        if vote_room_id is not None:
            params["voteRoomID"] = vote_room_id
        if schedule_ids:
            params["scheduleIDs"] = schedule_ids

        data = self._get("/Schedule/api/getschedulelistasync", params=params or None)
        if data is None:
            return []
        return [Schedule.model_validate(s) for s in data.get("Schedules", [])]

    def get_schedule_types(self) -> list[ScheduleType]:
        """Reference list of 6 schedule types.  Envelope key: ``ScheduleTypes``."""
        data = self._get("/Schedule/api/getscheduletypesreferenceasync")
        if data is None:
            return []
        return [ScheduleType.model_validate(t) for t in data.get("ScheduleTypes", [])]

    def get_meeting_rooms(self, chamber_code: str | None = None) -> list[MeetingRoom]:
        """Reference list of meeting rooms; 18 for the House.

        Args:
            chamber_code: ``"H"`` or ``"S"``.  Omit for both.

        Envelope key: ``MeetingRooms``.
        """
        params = {"chamberCode": chamber_code} if chamber_code else None
        data = self._get("/Schedule/api/getmeetingroomsreferenceasync", params=params)
        if data is None:
            return []
        return [MeetingRoom.model_validate(r) for r in data.get("MeetingRooms", [])]

    # ------------------------------------------------------------------
    # Meetings: floor calendars and Senate dockets (what is up)
    # ------------------------------------------------------------------

    def get_calendars(self, chamber_code: str, session_code: int) -> list[CalendarItem]:
        """The floor calendars of a chamber for a session.

        56 for the 2026 House, every one of type ``"Chamber"``, with the
        PDF and JSON files attached.  The list carries no agendas; call
        :meth:`get_calendar` for one calendar's bills and votes.

        The endpoint's ``calendarDate`` filter answered 204 for
        ``2026-02-17`` (measured 2026-09-11), so it is not wired.  Filter
        the list on ``CalendarDate`` instead.

        Args:
            chamber_code: ``"H"`` or ``"S"``.  Required.
            session_code: e.g. ``20261``.

        Envelope key: ``Calendars``.
        """
        data = self._get(
            "/Calendar/api/getcalendarlistasync",
            params={"chamberCode": chamber_code, "sessionCode": session_code},
        )
        if data is None:
            return []
        return [CalendarItem.model_validate(c) for c in data.get("Calendars", [])]

    def get_calendar(self, calendar_id: int) -> CalendarDetail | None:
        """One floor calendar with its categories, agendas, and vote rows.

        Calendar 20885 (``HC20114``) carries one Resolutions category with
        four agendas, each naming a bill, and 88 ``VoteMember`` rows on the
        agenda items.  ``CalendarDetail.bills`` flattens the agendas that
        name a bill.

        Args:
            calendar_id: ``CalendarID`` from :meth:`get_calendars`.

        Returns:
            The :class:`CalendarDetail`, or ``None`` when LIS answers 204.

        Envelope key: ``Calendars`` — a list holding one calendar.
        """
        data = self._get("/Calendar/api/getcalendarsbyidasync", params={"calendarId": calendar_id})
        if data is None:
            return None

        rows = data.get("Calendars", [])
        return CalendarDetail.model_validate(rows[0]) if rows else None

    def get_dockets(
        self,
        committee_id: int,
        session_code: int,
        chamber_code: str = "S",
    ) -> list[DocketListItem]:
        """A Senate committee's dockets for a session.

        **Senate only.**  A House committee answers 204, so this returns an
        empty list for one.  Senate Courts of Justice (``CommitteeID`` 202)
        has 16 dockets in 20261.  The list carries no bills; call
        :meth:`get_docket` for one docket's items.

        Args:
            committee_id: ``CommitteeID`` from :meth:`get_committees`.
            session_code: e.g. ``20261``.
            chamber_code: ``"S"``; sent as given.

        Envelope key: ``Dockets``.
        """
        data = self._get(
            "/Calendar/api/getdocketlistasync",
            params={
                "committeeID": committee_id,
                "sessionCode": session_code,
                "chamberCode": chamber_code,
            },
        )
        if data is None:
            return []
        return [DocketListItem.model_validate(d) for d in data.get("Dockets", [])]

    def get_dockets_by_committee_number(
        self,
        committee_number: str,
        session_code: int,
        chamber_code: str = "S",
    ) -> list[DocketListItem]:
        """The same docket list by committee number, e.g. ``"S13"``.

        All three parameters are required by LIS.  Envelope key: ``Dockets``.
        """
        data = self._get(
            "/Calendar/api/getdocketlistbycommitteenumberasync",
            params={
                "committeeNumber": committee_number,
                "sessionCode": session_code,
                "chamberCode": chamber_code,
            },
        )
        if data is None:
            return []
        return [DocketListItem.model_validate(d) for d in data.get("Dockets", [])]

    def get_docket(self, docket_id: int) -> DocketDetail | None:
        """One Senate docket with its bills, members, staff, and schedule.

        Docket 21123 (Senate Courts of Justice, 2026-03-09) carries one
        category of 16 bills, each with its summary and patrons, and one
        linked schedule with the room.  ``DocketDetail.bills`` flattens the
        items that name a bill.

        The envelope reports ``Success: false`` with a null
        ``FailureMessage`` on this complete response; the flag is ignored.

        Args:
            docket_id: ``DocketID`` from :meth:`get_dockets`.

        Returns:
            The :class:`DocketDetail`, or ``None`` when LIS answers 204.

        Envelope key: ``Dockets`` — a list holding one docket.
        """
        data = self._get("/Calendar/api/getdocketsbyidasync", params={"docketId": docket_id})
        if data is None:
            return None

        rows = data.get("Dockets", [])
        return DocketDetail.model_validate(rows[0]) if rows else None

    def get_calendar_types(self) -> list[CalendarType]:
        """Reference list of 2 calendar types: 1 Chamber, 2 Committee.

        Envelope key: ``CalendarTypes``.
        """
        data = self._get("/Calendar/api/getcalendartypesreferenceasync")
        if data is None:
            return []
        return [CalendarType.model_validate(t) for t in data.get("CalendarTypes", [])]

    def get_calendar_category_types(
        self,
        chamber_code: str | None = None,
    ) -> list[CalendarCategoryType]:
        """Reference list of 98 calendar category types, 44 House and 54 Senate.

        The vocabulary behind ``CategoryCode`` on calendar and docket
        categories.  The sibling ``getcalendaractionsreferenceasync`` is not
        wired: it returns 4,952 rows and 2 MB.

        Args:
            chamber_code: ``"H"`` or ``"S"``.  Omit for both.

        Envelope key: ``CalendarCategoryTypes``.
        """
        params = {"chamberCode": chamber_code} if chamber_code else None
        data = self._get("/Calendar/api/getcalendarcategorytypesreferenceasync", params=params)
        if data is None:
            return []
        return [
            CalendarCategoryType.model_validate(t) for t in data.get("CalendarCategoryTypes", [])
        ]


def _date_param(value: str | date | datetime) -> str:
    """``YYYY-MM-DD`` for a date or datetime; a string passes through as sent."""
    if isinstance(value, datetime):
        return value.date().isoformat()

    if isinstance(value, date):
        return value.isoformat()

    return value
