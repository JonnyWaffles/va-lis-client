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
  ``PartnerList``.  MinutesBook answers its list under ``Minutes`` and its
  detail under ``MinutesBooks``, so the spec is wrong twice for one service.
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
    ActionReferenceType,
    ActorType,
    Agency,
    AmendedLegislation,
    BillVersion,
    CalendarAction,
    CalendarCategoryType,
    CalendarDetail,
    CalendarItem,
    CalendarType,
    CategoryType,
    ChamberStatistic,
    ChamberType,
    Committee,
    CommitteeAction,
    CommitteeMember,
    CommitteeRole,
    CommitteeStatistic,
    Communication,
    CommunicationCategoryType,
    CommunicationFile,
    CommunicationListItem,
    ConferenceLegislation,
    Contact,
    ContactType,
    District,
    DocketDetail,
    DocketListItem,
    Heartbeat,
    IntroductionDate,
    Legislation,
    LegislationCategory,
    LegislationEvent,
    LegislationEventType,
    LegislationIdEntry,
    LegislationNumberEntry,
    LegislationSearchResult,
    LegislationStatus,
    LegislationSummary,
    LegislationSummaryItem,
    LegislationTextDetail,
    LegislationTextItem,
    LegislationVersion,
    MeetingRoom,
    Member,
    MemberContact,
    MemberLegislation,
    MemberStatusType,
    MemberVoteResult,
    MinutesBook,
    MinutesBookListItem,
    Organization,
    OwnerType,
    PagedList,
    Pagination,
    Partner,
    Party,
    Patron,
    PatronLegislation,
    PatronRole,
    Personnel,
    Schedule,
    ScheduleType,
    Session,
    SessionEventType,
    SessionType,
    ShallowSession,
    ShallowVote,
    SponsorType,
    StaffRoleType,
    Subject,
    SummaryVersion,
    TextAction,
    TextDisposition,
    TextHitCount,
    Vote,
    VoteLink,
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
        raw: bool = False,
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
            raw: Return the body as text.  A few paths answer with HTML,
                plain text or iCalendar instead of JSON.
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

                return resp.text if raw else resp.json()

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

    def _get_text(self, path: str, params: dict | None = None) -> str | None:
        """GET a path whose body is text, not JSON.  ``None`` on a 204."""
        return self._request("GET", path, params=params, raw=True)

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

    def get_sessions_by_year(self, year: int | str) -> list[Session]:
        """Sessions for one year, with the year in the path.

        The same rows as :meth:`get_sessions` with a year: two for 2026.  A
        year with no session answers 204 and gives ``[]`` (``1990``).

        Envelope key: ``Sessions``.
        """
        data = self._get(f"/Session/api/getsessionsbyyearasync/{year}")
        if data is None:
            return []

        return [Session.model_validate(s) for s in data.get("Sessions", [])]

    def get_session(self, session_id: int) -> Session | None:
        """One session by its ``SessionID``, with its events.  ``None`` if unknown.

        ``59`` is the 2026 Regular Session.  An unknown id answers 204.

        Envelope key: ``Sessions`` (single-element list).
        """
        data = self._get(f"/Session/api/getsessionbyidasync/{session_id}")
        if data is None:
            return None

        sessions = data.get("Sessions", [])
        return Session.model_validate(sessions[0]) if sessions else None

    def get_session_by_code(self, session_code: int | str) -> Session | None:
        """One session by its ``SessionCode``, with its events.  ``None`` if unknown.

        The one request that turns a code into a ``SessionID``: ``20261``
        gives ``59``.  An unknown code answers 204.

        Envelope key: ``Sessions`` (single-element list).
        """
        data = self._get(f"/Session/api/getsessionbycodeasync/{session_code}")
        if data is None:
            return None

        sessions = data.get("Sessions", [])
        return Session.model_validate(sessions[0]) if sessions else None

    def get_sessions_shallow(
        self,
        *,
        session_type_id: int | None = None,
        session_year: int | None = None,
        is_active: bool | None = None,
    ) -> list[ShallowSession]:
        """Every session without its events, newest first.

        59 sessions back to 1994 in 9 KB with no argument (measured
        2026-10-12).  ``session_year=2026`` gave 2 rows,
        ``session_type_id=2`` gave the 25 special sessions, and
        ``is_active=True`` gave 3.

        **``is_active=False`` filters nothing.**  It returned all 59 rows,
        the three active ones among them.  Filter on ``IsActive`` yourself
        for the inactive sessions.

        **``IsActive`` is not "in session".**  The three active sessions in
        October 2026 were 20271, 20261 and 20262, and only 20271 was the
        default.  Use :meth:`get_default_session` for the working session.

        Args:
            session_type_id: ``1`` regular, ``2`` special.
            session_year: e.g. ``2026``.
            is_active: ``True`` keeps the sessions LIS flags active.
                ``False`` is sent and ignored.

        Envelope key: ``Sessions``.
        """
        params: dict = {}
        if session_type_id is not None:
            params["sessionTypeID"] = session_type_id

        if session_year is not None:
            params["sessionYear"] = session_year

        if is_active is not None:
            params["IsActive"] = "true" if is_active else "false"

        data = self._get("/Session/api/getsessionshallowlistasync", params=params or None)
        if data is None:
            return []

        return [ShallowSession.model_validate(s) for s in data.get("Sessions", [])]

    def get_session_event_types(self) -> list[SessionEventType]:
        """Reference list of the 11 session milestone types.

        The vocabulary behind ``SessionEvent.EventTypeID``.  Its labels and
        the labels on the events disagree; see :class:`SessionEventType`.

        Envelope key: ``SessionEventTypes``.
        """
        data = self._get("/Session/api/getsessioneventtypereferencesasync")
        if data is None:
            return []

        return [SessionEventType.model_validate(t) for t in data.get("SessionEventTypes", [])]

    def get_session_types(self) -> list[SessionType]:
        """Reference list of the 2 session types.  Envelope key: ``SessionTypes``."""
        data = self._get("/Session/api/getsessiontypereferencesasync")
        if data is None:
            return []

        return [SessionType.model_validate(t) for t in data.get("SessionTypes", [])]

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

    def get_amended_legislation(
        self,
        *,
        session_code: int | None = None,
        session_id: int | None = None,
        chamber_code: str | None = None,
    ) -> list[AmendedLegislation]:
        """Every bill in a session that has an amendment, with the amendments.

        The only list of amendments in LIS.  Every other route starts from
        one bill.

        **A session is required.**  With none LIS answers 400, "Must provide
        either a valid Session ID or Session Code".

        **``chamber_code`` picks the amendments, not the bills.**  ``"H"``
        returns the bills that carry a House amendment, 71 Senate bills among
        them, and only their House amendments.  For 20261 that was 278 rows
        and 391 KB; ``"S"`` was 229 rows; neither was 462 rows and 691 KB
        (measured 2026-10-12).

        **A bill repeats once per summary version.**  The House rows held 263
        unique bills.  See :class:`AmendedLegislation`.

        Args:
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.  An alternative to ``session_code``.
            chamber_code: ``"H"`` or ``"S"``, the chamber that offered the
                amendments.  Omit for both.

        Envelope key: ``LegislationAmendmentList``.
        """
        params: dict = {}
        if session_code is not None:
            params["sessionCode"] = session_code

        if session_id is not None:
            params["sessionID"] = session_id

        if chamber_code is not None:
            params["chamberCode"] = chamber_code

        data = self._get("/Legislation/api/getlegislationamendmentlistasync", params=params)
        if data is None:
            return []

        return [
            AmendedLegislation.model_validate(r) for r in data.get("LegislationAmendmentList", [])
        ]

    def get_conference_legislation(
        self,
        session_id: int | None = None,
        *,
        chamber_code: str | None = None,
        member_id: int | None = None,
    ) -> list[ConferenceLegislation]:
        """Bills that went to a conference committee.

        170 for the 2026 Regular Session (``session_id=59``), 85 from each
        chamber (measured 2026-10-12).

        **This path takes a ``sessionID`` and no session code.**  LIS ignores
        ``sessionCode`` here without an error, and a request with no session
        it understands returns every session at once: 2,826 rows and 1.5 MB,
        back to 1994.  That is why this method has no ``session_code``
        argument.  Turn a code into an id with :meth:`get_session_by_code`.

        ``member_id`` narrows to the conferences a member sat on.  Member 544
        returned five bills, two of them Senate bills, and the events of one
        (HB361) name that member among the House conferees.

        Args:
            session_id: e.g. ``59``.  Omit for every session.
            chamber_code: ``"H"`` or ``"S"``, the bill's chamber.
            member_id: A conferee's ``MemberID``.

        Envelope key: ``ConferenceLegislationList``.
        """
        params: dict = {}
        if session_id is not None:
            params["sessionID"] = session_id

        if chamber_code is not None:
            params["chamberCode"] = chamber_code

        if member_id is not None:
            params["memberID"] = member_id

        data = self._get(
            "/Legislation/api/getconferencelegislationlistasync", params=params or None
        )
        if data is None:
            return []

        return [
            ConferenceLegislation.model_validate(r)
            for r in data.get("ConferenceLegislationList", [])
        ]

    def get_legislation_list(self, **criteria) -> list[LegislationSearchResult]:
        """Search bills through the Legislation service.  Rows arrive as sent.

        The same request body and the same rows as :meth:`search_legislation`,
        from ``/Legislation`` instead of ``/AdvancedLegislationSearch``.
        Member 544 in 20261 returned 236 rows on both.  Every warning on
        :meth:`search_legislation` applies: no paging, and a bill arrives more
        than once.

        Only ``session_code`` and ``member_id`` were exercised on this path
        (2026-10-12).  Prefer :meth:`search_legislation`, whose criteria were
        each probed.

        Args:
            **criteria: The keyword arguments of :meth:`search_legislation`.

        Envelope key: ``Legislations``.
        """
        data = self._post("/Legislation/api/getlegislationlistasync", json=_search_body(**criteria))
        if data is None:
            return []

        return [LegislationSearchResult.model_validate(row) for row in data.get("Legislations", [])]

    def get_legislation_ids(self, **criteria) -> list[LegislationIdEntry]:
        """Search bills and get back only their ids.

        The cheap form of :meth:`get_legislation_list`: 30 KB where the full
        rows are 440 KB, for member 544 in 20261 (measured 2026-10-12).

        **The ids repeat.**  Those 228 rows held 203 unique bills, because
        the search behind it emits a row per summary version and exact
        duplicates as well.  Collapse on ``LegislationID``.

        Args:
            **criteria: The keyword arguments of :meth:`search_legislation`.

        Envelope key: ``LegislationIds``.
        """
        data = self._post(
            "/Legislation/api/getlegislationidslistasync", json=_search_body(**criteria)
        )
        if data is None:
            return []

        return [LegislationIdEntry.model_validate(row) for row in data.get("LegislationIds", [])]

    def get_legislation_by_ids(
        self,
        legislation_ids: list[int | dict | LegislationIdEntry],
        session_id: int | None = None,
    ) -> list[LegislationSearchResult]:
        """Search rows for a set of bills you already hold the ids of.

        The second half of :meth:`get_legislation_ids`: pass its rows straight
        back in.  The rows that return are the search rows, repeats included.

        **Every entry needs a ``SessionID``.**  ``{"LegislationID": 98525}``
        alone answers 204, and the same entry with ``"SessionID": 59``
        returns the bill (checked 2026-10-12).  ``session_id`` fills it in on
        any entry that lacks one.

        Args:
            legislation_ids: ``LegislationID`` values, or
                :class:`LegislationIdEntry` rows, or the raw entry dicts.
            session_id: The session for entries that do not name their own.

        Envelope key: ``Legislations``.
        """
        entries = []
        for item in legislation_ids:
            if isinstance(item, LegislationIdEntry):
                entry = {"LegislationID": item.LegislationID, "SessionID": item.SessionID}
            elif isinstance(item, dict):
                entry = dict(item)
            else:
                entry = {"LegislationID": item}

            if entry.get("SessionID") is None and session_id is not None:
                entry["SessionID"] = session_id

            entries.append(entry)

        if not entries:
            return []

        data = self._post(
            "/Legislation/api/getlegislationbylegislationidsasync",
            json={"LegislationIds": entries},
        )
        if data is None:
            return []

        return [LegislationSearchResult.model_validate(row) for row in data.get("Legislations", [])]

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

    def get_text_dispositions(self) -> list[TextDisposition]:
        """Reference list of the 13 text dispositions, e.g. 6 ``Adopted``.

        The vocabulary behind ``TextDispositionID`` on text rows, bill
        versions and amendments.

        Envelope key: ``TextDispositionList``.
        """
        data = self._get("/LegislationText/api/gettextdispositionsreferenceasync")
        if data is None:
            return []

        return [TextDisposition.model_validate(r) for r in data.get("TextDispositionList", [])]

    def get_text_actions(self) -> list[TextAction]:
        """Reference list of the 31 actions that produce a text.

        The vocabulary behind ``LegislationTextActionID``.  Most of its
        ``EventCode`` values lack the chamber letter; see :class:`TextAction`.

        Envelope key: ``LegislationTextActions``.
        """
        data = self._get("/LegislationText/api/getlegislationtextactionlistasync")
        if data is None:
            return []

        return [TextAction.model_validate(r) for r in data.get("LegislationTextActions", [])]

    def get_sponsor_types(self) -> list[SponsorType]:
        """Reference list of the 6 sponsor types, e.g. 4 ``Member``.

        Envelope key: ``SponsorTypes``.
        """
        data = self._get("/LegislationText/api/getsponsortypelistasync")
        if data is None:
            return []

        return [SponsorType.model_validate(r) for r in data.get("SponsorTypes", [])]

    def get_draft_text(self, legislation_text_id: int) -> str | None:
        """The HTML body of one text, by its ``LegislationTextID`` alone.

        The lightest route to a bill's words: 3 KB for HB1's introduced text
        (``257719``), with no record around it.  It needs no session, where
        :meth:`get_bill_text_detail` answers 400 without one.  The id comes
        from :meth:`get_bill_texts`, a :class:`BillVersion`, or an
        :class:`Amendment`.

        The body starts at the enacting clause.  It has no session line, no
        bill number and no patron block; :meth:`get_bill_html` returns the
        page as printed.

        The ids are one sequence across every session: ``1`` is a 1994
        chapter.

        Returns:
            The HTML, or ``None`` when LIS answers 204.

        Envelope key: ``TextsList`` (single-element list holding ``DraftText``).
        """
        data = self._get(
            "/LegislationText/api/getdrafttextbylegislationtextidasync",
            params={"legislationTextID": legislation_text_id},
        )
        if data is None:
            return None

        texts = data.get("TextsList", [])
        return texts[0].get("DraftText") if texts else None

    def get_bill_html(
        self,
        session_code: int,
        document_code: str,
        *,
        hilite: bool = False,
    ) -> str | None:
        """One text as the printed page, in HTML.  Not JSON.

        LIS answers ``text/html``: a fragment with inline styles that opens
        with the session line ("2026 SESSION", "INTRODUCED") and runs through
        the bill, 6 KB for HB1.  There is no ``<html>`` wrapper.

        **The spec calls the parameter ``documentNumber`` and it takes a
        document code.**  ``"HB1"`` is the introduced text and ``"HB1ER"`` the
        enrolled one.  Take the code from :meth:`get_bill_texts`.

        ``hilite=True`` colors the changes to existing law, and has nothing
        to do with search keywords: struck text gains ``color:red`` and new
        text gains ``background-color:yellow``.

        Args:
            session_code: e.g. ``20261``.  Required.
            document_code: e.g. ``"HB1"`` or ``"HB1ER"``.
            hilite: Color the struck and the new language.

        Raises:
            requests.HTTPError: 400 for a code that session does not hold,
                "There is no draft text for the provided document code and
                session code".
        """
        params: dict = {"sessionCode": session_code, "documentNumber": document_code}
        if hilite:
            params["hilite"] = "true"

        return self._get_text("/LegislationText/api/getlegislationhtmlcontentasync", params=params)

    def get_bill_pdf_url(
        self,
        session_code: int,
        document_code: str,
        *,
        reference_number: str | None = None,
    ) -> str | None:
        """The URL of one text's PDF.  A string, not the file.

        Despite the path name ``getpdfasync``, the body is a JSON string
        holding a blob address such as
        ``https://lis.blob.core.windows.net/files/1081188.PDF``.  It is the
        same address as the ``PdfFile`` entry on a :class:`BillVersion`
        fetched by single id, at the price of knowing the document code and
        not the id.

        Args:
            session_code: e.g. ``20261``.  Required.
            document_code: e.g. ``"HB1"`` or ``"HB1ER"``.
            reference_number: The spec offers this to reach an impact
                statement.  Not exercised: no payload this client reads
                carries a value to send.

        Raises:
            requests.HTTPError: 400 for a code that session does not hold.
        """
        params: dict = {"sessionCode": session_code, "documentCode": document_code}
        if reference_number is not None:
            params["referenceNumber"] = reference_number

        return self._get("/LegislationText/api/getpdfasync", params=params)

    def get_text_hit_counts(
        self,
        legislation_text_ids: list[int],
        keyword: str | None = None,
        *,
        words: list[str] | None = None,
        location: str | None = None,
    ) -> list[TextHitCount]:
        """Count a keyword in a set of texts, one count per text.

        The counting half of a keyword search: it tells you how often the
        term occurs in texts you already chose, and finds nothing itself.
        ``"wage"`` over HB1's three texts returned 11, 12 and 12.

        ``keyword`` and ``words`` are two ways to send the term, and
        ``"wage"`` counted the same through either.  Several terms add up:
        ``"minimum"`` counts 12 and ``"wage"`` 11 in HB1's introduced text,
        and both together count 23 as a word list or as ``"minimum AND
        wage"``.

        **Send a term, and send real ids.**  With neither ``keyword`` nor
        ``words`` LIS answers 204, and it answers 204 as well when a text id
        does not exist.  Both come back as ``[]``.

        Args:
            legislation_text_ids: ``LegislationTextID`` values.
            keyword: Sent as ``KeywordExpression``.
            words: Sent as ``WordList``, one ``Hitword`` each.
            location: Sent as ``Location``.  Not exercised.

        Envelope key: ``HitCounts``.
        """
        body: dict = {"Documents": [{"LegislationTextID": i} for i in legislation_text_ids]}
        if keyword is not None:
            body["KeywordExpression"] = keyword

        if words:
            body["WordList"] = [{"Hitword": w} for w in words]

        if location is not None:
            body["Location"] = location

        data = self._post("/LegislationText/api/getlegislationtextshitcountsasync", json=body)
        if data is None:
            return []

        return [TextHitCount.model_validate(r) for r in data.get("HitCounts", [])]

    # ------------------------------------------------------------------
    # Legislation versions (one bill's published versions, and a bulk form)
    # ------------------------------------------------------------------

    def get_bill_versions(
        self,
        legislation_number: str,
        *,
        session_code: int | None = None,
        session_id: int | None = None,
    ) -> list[BillVersion]:
        """Every published version of one bill in a session, oldest first.

        The same records :meth:`get_bill_texts` lists, in the
        :class:`BillVersion` shape, which adds the chamber that made each
        version, its sponsor, and its disposition.

        **The file arrays come back empty here.**  Call
        :meth:`get_bill_versions_by_id` with the one id for the PDF, HTML and
        impact statement links.

        **A session is required.**  With none LIS answers 400, "SessionID or
        SessionCode not provided."  The two forms returned the same rows for
        HB1 in 20261 (checked 2026-09-17).

        The query key is ``BillNumber``.  LIS ignores ``legislationNumber``
        here and answers 400, "The BillNumber field is required."  The value
        may be any case, but must be unpadded: ``HB0001`` answers 204.

        **A carried bill answers with its even year rows.**  HB1013 in 20251
        returned its one 20241 row, ``SessionID`` 55, exactly as 20241 did,
        and HB9 in 20271 returned its 20261 row.  A number the session does
        not hold answers 204, so HB1 in 20251 and in 20271 both return ``[]``.

        Args:
            legislation_number: Unpadded, e.g. ``"HB1"``.
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.  An alternative to ``session_code``.

        Envelope key: ``LegislationsVersion``, plural on the first word, beside
        a ``SearchCriteria`` key that was null.
        """
        params: dict = {"BillNumber": legislation_number}
        if session_code is not None:
            params["sessionCode"] = session_code

        if session_id is not None:
            params["sessionID"] = session_id

        data = self._get(
            "/LegislationVersion/api/getlegislationversionbybillnumberasync",
            params=params,
        )
        if data is None:
            return []
        return [BillVersion.model_validate(v) for v in data.get("LegislationsVersion", [])]

    def get_bill_versions_by_id(
        self,
        legislation_id: int | None = None,
        *,
        legislation_ids: list[int] | None = None,
    ) -> list[BillVersion]:
        """Every published version of one bill, or of many, by ``LegislationID``.

        No session is involved, because the id names the bill across its
        carry over.  HB9 (98631) returned the same row here as by number.

        **Only the single id request returns files.**  With one id this
        method sends ``legislationID``, and LIS fills ``PdfFile``,
        ``HtmlFile`` and ``ImpactFile``.  With more it sends
        ``legislationIDs``, and LIS sends every file array empty, even when
        that list holds a single id (checked 2026-09-17).  So one request per
        bill is the price of the links.

        **The bulk form interleaves the bills.**  The rows come back sorted
        by ``DraftDate`` across the whole response, nulls first, not grouped
        by bill and not in the order you asked.  Group them on
        ``LegislationID``, or call ``LISService.bill_versions``, which does.
        102 ids in one request returned 404 rows and 228 KB (2026-09-17).

        Three traps in the parameters, which this method steps around:

        - **No id at all is not refused.**  LIS neither answered nor errored
          within 60 seconds, so this method raises ``ValueError`` instead of
          sending that request.
        - **``legislationID`` and ``legislationIDs`` together answer 204.**
          This method folds ``legislation_id`` into the list and sends one
          form.
        - **The list rides repeated keys.**  A comma joined value answers 400,
          "The value '98631,98525' is not valid."

        Args:
            legislation_id: One id, e.g. ``98525`` for HB1 in 20261.
            legislation_ids: Many ids, e.g. from :meth:`get_legislative_numbers`.
                A list of one is sent as ``legislation_id`` would be.

        Raises:
            ValueError: Neither argument names an id.

        Envelope key: ``LegislationsVersion``.
        """
        ids = list(legislation_ids or [])
        if legislation_id is not None:
            ids.insert(0, legislation_id)

        if not ids:
            raise ValueError(
                "Pass legislation_id or legislation_ids. LIS does not refuse a request "
                "with neither, and it did not answer one within 60 seconds."
            )

        # One id goes out singular, which is the only form that returns files.
        # requests sends a list as a repeated key, which is the form LIS wants.
        params = {"legislationID": ids[0]} if len(ids) == 1 else {"legislationIDs": ids}

        data = self._get(
            "/LegislationVersion/api/getlegislationversionbylegislationidasync",
            params=params,
        )
        if data is None:
            return []
        return [BillVersion.model_validate(v) for v in data.get("LegislationsVersion", [])]

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

    def get_summary_versions(self) -> list[SummaryVersion]:
        """Reference list of the 4 summary stages, in progression order.

        The official order of the ``SummaryVersion`` labels, and the
        vocabulary behind ``LegislationSummary.SummaryVersionID``.  See
        :class:`SummaryVersion`.

        Envelope key: ``SummaryVersions``.
        """
        data = self._get("/LegislationSummary/api/getsummaryversionsreferenceasync")
        if data is None:
            return []

        return [SummaryVersion.model_validate(r) for r in data.get("SummaryVersions", [])]

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

    def get_event(self, legislation_event_id: int) -> LegislationEvent | None:
        """One bill event by its ``LegislationEventID``.  ``None`` on a 204.

        The same row :meth:`get_bill_events` returns, for when an event id is
        all you hold: a minutes activity, a vote, and a communication each
        carry one.  The ids are one sequence back to 1994; ``1`` is HJ30 on
        1994-01-07.

        Envelope key: ``LegislationEvents`` (single-element list).
        """
        data = self._get(
            f"/LegislationEvent/api/getlegislationeventbyidasync/{legislation_event_id}"
        )
        if data is None:
            return None

        events = data.get("LegislationEvents", [])
        return LegislationEvent.model_validate(events[0]) if events else None

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

    def get_shallow_votes(
        self,
        chamber_code: str,
        *,
        session_code: int | None = None,
        session_id: int | None = None,
        legislation_id: int | None = None,
        member_id: int | None = None,
        start_date: str | date | datetime | None = None,
        end_date: str | date | datetime | None = None,
        vote_classification_id: int | None = None,
    ) -> list[ShallowVote]:
        """Votes without their members, filtered by bill, member, or date.

        The one vote endpoint that takes a ``legislationID``.  HB1 in 20261
        returned 3 House votes and 4 Senate votes, the tally and the
        committee on each, in two requests.  The roll call behind a row is
        :meth:`get_vote`.

        **A chamber and a session are both required.**  Without the chamber
        LIS answers 400, "The chamberCode field is required."  Without a
        session it answers 400, "Must provide either a valid Session ID or
        Session Code".  The chamber is the one that voted, so a bill costs
        one request per chamber.

        **``member_id`` and ``legislation_id`` together are an OR, not an
        AND.**  The answer is every vote on the bill plus every vote the
        member took part in: 1,911 rows and 1.5 MB for member 544 with HB1
        in 20261, including two HB1 committee votes that member had no part
        in.  It is not that member's votes on the bill.  Send one or the
        other.

        **``end_date`` is exclusive.**  A start and an end on the same day
        answer 204.  ``"2026-01-20"`` to ``"2026-01-21"`` returned the 49
        House votes of the 20th.  With ``member_id`` added, that became the
        one vote the member took part in.  The dates narrow whatever the
        other filters select.

        Read :class:`ShallowVote` before you filter the rows: ``IsBlock`` is
        false on a block vote, and most voice votes are missing.

        Not offered: ``voteNumber``.  LIS answers 500 to it, "Please specify
        a Floor Vote number", for a committee number and for a floor number
        alike, and this client retries a 500 for minutes before it gives up.

        Args:
            chamber_code: ``"H"`` or ``"S"``, the chamber that voted.
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.  An alternative to ``session_code``.
            legislation_id: Every vote that chamber took on one bill.
            member_id: Every vote one member took part in.
            start_date: Inclusive; a ``date``, ``datetime``, or
                ``"YYYY-MM-DD"``.
            end_date: Exclusive, same forms.
            vote_classification_id: ``2`` returned ``Attendance`` votes only.

        Envelope key: ``ShallowVotes``.
        """
        params: dict = {"chamberCode": chamber_code}
        if session_code is not None:
            params["sessionCode"] = session_code

        if session_id is not None:
            params["sessionID"] = session_id

        if legislation_id is not None:
            params["legislationID"] = legislation_id

        if member_id is not None:
            params["memberID"] = member_id

        if start_date is not None:
            params["startDate"] = _date_param(start_date)

        if end_date is not None:
            params["endDate"] = _date_param(end_date)

        if vote_classification_id is not None:
            params["voteClassificationID"] = vote_classification_id

        data = self._get("/Vote/api/getshallowvoteasync", params=params)
        if data is None:
            return []

        return [ShallowVote.model_validate(v) for v in data.get("ShallowVotes", [])]

    def get_vote_links(self, legislation_event_id: int) -> list[VoteLink]:
        """The vote behind one bill event, without the roster.

        One row for an event that recorded a vote, and ``[]`` for one that
        did not, which answers 204.  See :class:`VoteLink`.

        Envelope key: ``VoteLinks``.
        """
        data = self._get(
            "/Vote/api/getvotelinksasync",
            params={"legislationEventID": legislation_event_id},
        )
        if data is None:
            return []

        return [VoteLink.model_validate(v) for v in data.get("VoteLinks", [])]

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

        :meth:`get_member_list` returns the same members with fewer fields.
        It drops ``DistrictID`` and ``DistrictName``, so it cannot back a
        voting record.

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

    def get_active_members(
        self,
        session_code: int | None = None,
        session_id: int | None = None,
    ) -> list[Member]:
        """A session's members with the fields the roster leaves null.

        Both chambers in one request: 146 rows for 20261, 104 House and 42
        Senate (measured 2026-10-12).  Every row fills ``MemberDetailID``,
        ``ChamberName`` and ``SessionID``, and most fill ``SeatNumber`` and
        ``VotingSequence``, all of which :meth:`get_members` sends null.

        **"Active" is not "sitting".**  Those 146 included 5 ``Outgoing`` and
        3 ``Inactive`` members, and 8 carried a ``ServiceEndDate``.

        **It is two short of the roster.**  :meth:`get_members` returned 148
        for the same session, and the two missing here were one ``Active``
        and one ``Outgoing`` member.  Build a roll call join on the roster,
        and use this list for the extra fields.

        **A session is required.**  With none LIS answers 400.

        Args:
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.  An alternative to ``session_code``.

        Envelope key: ``Members``.
        """
        params: dict = {}
        if session_code is not None:
            params["sessionCode"] = session_code

        if session_id is not None:
            params["sessionID"] = session_id

        data = self._get("/Member/api/getactivemembersasync", params=params)
        if data is None:
            return []

        return [Member.model_validate(m) for m in data.get("Members", [])]

    def get_member_list(
        self,
        *,
        session_code: int | None = None,
        session_id: int | None = None,
        chamber_code: str | None = None,
        member_number: str | None = None,
        party_code: str | None = None,
    ) -> list[Member]:
        """The roster with 16 fields, and the only one that filters on party.

        The 148 members :meth:`get_members` returns for 20261, in 64 KB, with
        no district and no room on any row.  Prefer :meth:`get_members` for a
        roll call join.  What this path adds is its filters: ``party_code``
        and ``member_number`` both work here, and the roster takes neither.
        The Senate's Republicans came back as 19 rows and ``"H0402"`` as one
        (measured 2026-10-12).

        **``MemberStatusID`` disagrees with ``MemberStatus`` here too.**  61
        of the 148 rows said ``0`` beside ``"Active"``.  Read the name.

        ``ServiceEndDate`` is a key only on a member who left: 9 of 148.

        **A session is required.**  With none LIS answers 400, "Request
        requires valid Session Code".

        Not offered: ``statuses``.  LIS ignored it as ``3``, as
        ``"Outgoing"`` and as a repeated key, and returned all 148 each time.

        Args:
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.  An alternative to ``session_code``.
            chamber_code: ``"H"`` or ``"S"``.
            member_number: e.g. ``"H0402"``.  The number, not the ``MemberID``.
            party_code: ``"D"``, ``"R"`` or ``"I"``.

        Envelope key: ``ShallowMembers``.
        """
        params: dict = {}
        if session_code is not None:
            params["sessionCode"] = session_code

        if session_id is not None:
            params["sessionID"] = session_id

        if chamber_code is not None:
            params["chamberCode"] = chamber_code

        if member_number is not None:
            params["memberNumber"] = member_number

        if party_code is not None:
            params["partyCode"] = party_code

        data = self._get("/Member/api/getmemberlistasync", params=params)
        if data is None:
            return []

        return [Member.model_validate(m) for m in data.get("ShallowMembers", [])]

    def get_member_contacts(
        self,
        *,
        session_code: int | None = None,
        session_id: int | None = None,
        chamber_code: str | None = None,
        member_number: str | None = None,
        party_code: str | None = None,
        contact_type_id: int | None = None,
        effective_date: str | date | datetime | None = None,
    ) -> list[MemberContact]:
        """Members with their Capitol and district addresses in one row each.

        106 rows and 120 KB for the 20261 House.  The only member endpoint
        that splits the name into first, middle and last.

        **A session is required.**  With none LIS answers 400, "Request
        requires valid Session Code".

        **The bill counts on a row are session totals unless you name one
        member.**  See :class:`MemberContact`.

        ``chamber_code``, ``party_code`` and ``member_number`` all filter:
        the Senate's Republicans came back as 19 rows, and ``"H0402"`` as
        one.  ``contact_type_id=9`` changed nothing; the Senate rows still
        carried both address kinds.

        Args:
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.  An alternative to ``session_code``.
            chamber_code: ``"H"`` or ``"S"``.
            member_number: e.g. ``"H0402"``.  The number, not the ``MemberID``.
            party_code: ``"D"``, ``"R"`` or ``"I"``.
            contact_type_id: Sent as ``contactTypeID``; no effect observed.
            effective_date: Sent as ``effectiveDate``.  Not exercised.

        Envelope key: ``MemberContactInformationList``.
        """
        params: dict = {}
        if session_code is not None:
            params["sessionCode"] = session_code

        if session_id is not None:
            params["sessionID"] = session_id

        if chamber_code is not None:
            params["chamberCode"] = chamber_code

        if member_number is not None:
            params["memberNumber"] = member_number

        if party_code is not None:
            params["partyCode"] = party_code

        if contact_type_id is not None:
            params["contactTypeID"] = contact_type_id

        if effective_date is not None:
            params["effectiveDate"] = _date_param(effective_date)

        data = self._get("/Member/api/getmemberscontactinformationlistasync", params=params)
        if data is None:
            return []

        return [
            MemberContact.model_validate(m) for m in data.get("MemberContactInformationList", [])
        ]

    def get_member_status_types(self) -> list[MemberStatusType]:
        """Reference list of the 4 member statuses.  Envelope key: ``MemberStatuses``."""
        data = self._get("/Member/api/getmemberstatustypereferencesasync")
        if data is None:
            return []

        return [MemberStatusType.model_validate(r) for r in data.get("MemberStatuses", [])]

    def get_chamber_types(self) -> list[ChamberType]:
        """Reference list of the 2 chambers.  Envelope key: ``ChamberTypes``."""
        data = self._get("/Member/api/getchambertypereferencesasync")
        if data is None:
            return []

        return [ChamberType.model_validate(r) for r in data.get("ChamberTypes", [])]

    def get_member_owner_types(self) -> list[OwnerType]:
        """The owner types again, from the Member service.

        The six rows of :meth:`get_owner_types`.  LIS sends ``OwnerTypeID``
        as a string here (``"4"``) and as a number there; the model reads
        both as an ``int``.

        Envelope key: ``OwnerTypes``.
        """
        data = self._get("/Member/api/getownertypereferencesasync")
        if data is None:
            return []

        return [OwnerType.model_validate(r) for r in data.get("OwnerTypes", [])]

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

    def get_patron_legislation(
        self,
        session_code: int,
        chamber_code: str,
    ) -> list[PatronLegislation]:
        """Every chief patron in one chamber, with their bills, in one request.

        A cheap chamber-wide index: 102 rows and 2,342 bills for the 20261
        House, 42 rows and 1,304 bills for its Senate. One row per member,
        with the bills nested under :attr:`PatronLegislation.bills`.

        **It is chief patron only**, despite the name. Every row of both
        chambers came back as ``Chief Patron`` on 2026-09-15, and the row
        count equals the distinct member count. Use
        :meth:`get_bill_patrons` for a bill's full list and
        :meth:`~va_lis_client.service.LISService.member_bills` for one
        member's co-patronage.

        Both arguments are required. LIS answers 400 with "The chamberCode
        field is required." without the chamber, and "Must provide either a
        valid Session ID or Session Code" without the session. 20271
        answered 204 on 2026-09-15.

        Args:
            session_code: e.g. ``20261``.
            chamber_code: ``"H"`` or ``"S"``.

        Envelope key: ``PatronLegislation``.
        """
        data = self._get(
            "/LegislationPatron/api/getlegislationpatronlistasync",
            params={"sessionCode": session_code, "chamberCode": chamber_code},
        )
        if data is None:
            return []
        return [PatronLegislation.model_validate(p) for p in data.get("PatronLegislation", [])]

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

    def get_member_patron_types(
        self,
        member_id: int,
        *,
        session_code: int | None = None,
        session_id: int | None = None,
    ) -> list[PatronRole]:
        """The patron roles one member holds in a session.

        A cheap test of what a member has to show before you fetch their
        bills.  Member 544 held four in 20261: ``1`` Chief Patron, ``2``
        Chief Co-Patron, ``4`` Co-Patron, and ``0``.  Member 308 held ``1``,
        ``3``, ``4``, ``5`` and ``0``.

        **``PatronTypeID`` 0 is not a patron role.**  It is "Budget Amendment
        Requests", it came back for both members, and
        :meth:`get_patron_roles` does not list it.

        **A session is required.**  With none LIS answers 400.

        Args:
            member_id: e.g. ``544``.
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.  An alternative to ``session_code``.

        Envelope key: ``MemberPatronTypes``.
        """
        params: dict = {"memberID": member_id}
        if session_code is not None:
            params["sessionCode"] = session_code

        if session_id is not None:
            params["sessionID"] = session_id

        data = self._get("/LegislationPatron/api/getmemberpatrontypelistasync", params=params)
        if data is None:
            return []

        return [PatronRole.model_validate(r) for r in data.get("MemberPatronTypes", [])]

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

    def get_committee_members_text(
        self,
        committee_id: int,
        *,
        session_code: int | None = None,
        session_id: int | None = None,
    ) -> str | None:
        """A committee's roster as one line of surnames.  Plain text, not JSON.

        The line LIS prints under a committee name: ``"Aird (Chair), Stanley,
        McPike, ..."`` for Senate committee 28 in 20261, 17 names in 180
        bytes.  It carries surnames and the chair only, so reach for
        :meth:`get_committee_members` when you need a ``MemberID``.

        **A session is required, and its absence is a 406.**  Not the 400
        the other paths give.  An unknown committee answers 204.

        Args:
            committee_id: e.g. ``28``.
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.  An alternative to ``session_code``.

        Returns:
            The text, or ``None`` on a 204.

        Raises:
            requests.HTTPError: 406 when no session is sent.
        """
        params: dict = {"CommitteeID": committee_id}
        if session_code is not None:
            params["sessionCode"] = session_code

        if session_id is not None:
            params["sessionID"] = session_id

        return self._get_text(
            "/MembersByCommittee/api/getcommitteememberslisttextasync", params=params
        )

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

    def get_schedule_vcal(
        self,
        start_date: str | date | datetime | None = None,
        end_date: str | date | datetime | None = None,
        *,
        owner_id: int | None = None,
        schedule_type_id: int | None = None,
        vote_room_id: int | None = None,
        schedule_ids: list[int] | None = None,
    ) -> str | None:
        """The master schedule as an iCalendar feed.  Text, not JSON.

        LIS answers ``text/calendar``: a ``VCALENDAR`` holding one ``VEVENT``
        per meeting, ready for a calendar application.  2026-02-02 to
        2026-02-03 returned 32 events in 13 KB, where :meth:`get_schedules`
        returned 34 rows for the same dates.  The filters are those of
        :meth:`get_schedules`, and a list of two ``schedule_ids`` returned
        exactly those two events.

        **The id filter is ``scheduleIDs``, plural.**  LIS ignores an unknown
        parameter such as ``scheduleID`` without an error and returns its
        default feed, so a misspelled filter looks like it worked.

        This method never sends ``writeToAzureCDN``, which the spec also
        offers.  That flag publishes a file and is not a read.

        Args:
            start_date: A ``date``, ``datetime``, or ``"YYYY-MM-DD"``.
            end_date: Same forms.
            owner_id: ``CommitteeID`` of the meeting's owner.
            schedule_type_id: See :meth:`get_schedule_types`.
            vote_room_id: A room from :meth:`get_meeting_rooms`.
            schedule_ids: ``ScheduleID`` values, sent as repeated keys.

        Returns:
            The iCalendar text, or ``None`` on a 204.
        """
        params: dict = {}
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
            params["scheduleIDs"] = list(schedule_ids)

        return self._get_text("/Schedule/api/previewvcalfileasync", params=params or None)

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

    def get_category_types(self) -> list[CategoryType]:
        """Reference list of the 8 coarse category types.

        The vocabulary behind ``CategoryTypeID`` on a minutes category and on
        a calendar category type: 1 Order, 2 Regular Calendar, 3 Uncontested
        Calendar, 4 Miscellaneous, and four more. Takes no parameters.

        Envelope key: ``CategoryTypes``.
        """
        data = self._get("/Calendar/api/getcategorytypesreferenceasync")
        if data is None:
            return []
        return [CategoryType.model_validate(t) for t in data.get("CategoryTypes", [])]

    def get_calendar_actions(self) -> list[CalendarAction]:
        """The action vocabulary that resolves a minutes activity.

        **This is a 2 MB download of 4,952 rows, and it takes no filter.**
        Fetch it once and keep it;
        :meth:`~va_lis_client.service.LISService.calendar_actions_by_id`
        does that for you and indexes it.

        The 4,952 rows hold only 490 distinct ``CalendarActionID``, because
        LIS emits one row per calendar category the action can appear under.
        Treat the id as one-to-many, never one-to-one.

        Envelope key: ``CalendarActions``.
        """
        data = self._get("/Calendar/api/getcalendaractionsreferenceasync")
        if data is None:
            return []
        return [CalendarAction.model_validate(a) for a in data.get("CalendarActions", [])]

    # ------------------------------------------------------------------
    # Directory (committee clerks, member offices, budget agencies)
    # ------------------------------------------------------------------

    def get_personnel(
        self,
        *,
        staff_role_type_id: int | None = None,
        affiliation_id: int | None = None,
        effective_begin_date: str | date | datetime | None = None,
    ) -> list[Personnel]:
        """Committee clerks and DLS staff, with the committee they serve.

        **Pass a filter.** An unfiltered call returns 33 rows that omit
        ``StaffID``, ``AffiliationID``, ``StaffRoleTypeID`` and
        ``EffectiveBeginDate`` entirely, so it cannot tell you who clerks
        what. Any parameter at all makes LIS send all 11 fields.
        ``staff_role_type_id=1`` gives the 54 committee clerks and ``2`` the
        59 DLS staff.

        Not session scoped. There is no session parameter, and ``sessionCode``
        and ``chamberCode`` are both silently ignored.
        ``effective_begin_date`` is the only real time filter and it works:
        ``"2026-01-01"`` cut the clerk list from 54 to 44.

        Three sibling paths return slices of this list:
        :meth:`get_clerk_personnel` is this call with
        ``staff_role_type_id=1``, :meth:`get_staff_personnel` is it with
        ``2``, and :meth:`get_personnel_references` is the unfiltered call.

        Args:
            staff_role_type_id: ``1`` clerks, ``2`` staff. See
                :meth:`get_staff_role_types`.
            affiliation_id: A ``CommitteeID``, to get one committee's clerks.
            effective_begin_date: Keep only assignments starting on or after
                this date.

        Envelope key: ``Personnels``.
        """
        params: dict = {}
        if staff_role_type_id is not None:
            params["staffRoleTypeID"] = staff_role_type_id

        if affiliation_id is not None:
            params["affiliationID"] = affiliation_id

        if effective_begin_date is not None:
            params["effectiveBeginDate"] = _date_param(effective_begin_date)

        data = self._get("/Personnel/api/getpersonnellistasync", params=params or None)
        if data is None:
            return []
        return [Personnel.model_validate(p) for p in data.get("Personnels", [])]

    def get_staff_role_types(self) -> list[StaffRoleType]:
        """Reference list of the 7 staff roles.

        The vocabulary behind ``StaffRoleTypeID``.

        Envelope key: ``StaffRoleTypes``.
        """
        data = self._get("/Personnel/api/getstaffroletypesasync")
        if data is None:
            return []
        return [StaffRoleType.model_validate(t) for t in data.get("StaffRoleTypes", [])]

    def get_contacts(
        self,
        owner_id: int | None = None,
        owner_type_id: int | None = None,
    ) -> list[Contact]:
        """Contact records for one owner, or every contact LIS holds.

        For a member pass ``owner_type_id=3``; the row that comes back is the
        district office, ``ContactTypeID`` 9. Not every member has one, so a
        204 and an empty list are normal — member 503 has a row and member
        527 does not.

        **Pass both arguments or neither.** One alone gives 400 with
        "OwnerID and OwnerTypeID must be used in conjunction with each
        other".

        **With neither, LIS returns every contact**, 6,882 rows on
        2026-09-16 in one response with no paging. The total grows as people
        register on the LIS website. Three populations, told apart by
        ``ContactTypeID``:

        ==  ========  =====  ==============================================
        ID  Type      Rows   What it is
        ==  ========  =====  ==============================================
        9   Mailing     390  Member district offices; ``OwnerID`` is a
                             ``MemberID``.  384 carry an address.
        10  Main          2  The two General Assembly buildings.
        3   User      6,490  LIS website accounts: an email and nothing
                             else.  No address or phone on any row.
        ==  ========  =====  ==============================================

        **``OwnerID`` on a type 3 row joins to nothing.** Its range covers
        every member and personnel identity, so it looks joinable, but it is
        a separate account namespace: matched against member identities, 0
        of 145 emails carried the member's surname. Filter to type 9 before
        you join ``OwnerID`` to a member.

        Not session scoped; ``sessionCode`` is silently ignored.

        Args:
            owner_id: A ``MemberID`` when ``owner_type_id`` is ``3``.
            owner_type_id: See :meth:`get_owner_types`.

        Envelope key: ``Contacts``.
        """
        params: dict = {}
        if owner_id is not None:
            params["ownerID"] = owner_id

        if owner_type_id is not None:
            params["ownerTypeID"] = owner_type_id

        data = self._get("/Contact/api/getcontactsbyownerasync", params=params or None)
        if data is None:
            return []
        return [Contact.model_validate(c) for c in data.get("Contacts", [])]

    def get_contact_types(self) -> list[ContactType]:
        """Reference list of the 10 contact types, each with its owner type.

        The only way to read a contact's owner type, since a
        :class:`Contact` row never carries ``OwnerTypeID``.

        Envelope key: ``ContactTypeReferences``.
        """
        data = self._get("/Contact/api/getcontacttypereferencesasync")
        if data is None:
            return []
        return [ContactType.model_validate(t) for t in data.get("ContactTypeReferences", [])]

    def get_owner_types(self) -> list[OwnerType]:
        """Reference list of the 6 contact owner types.

        1 Person, 2 User, 3 Member, 4 Chamber, 5 District, 6 Organization.

        Envelope key: ``OwnerTypeReferences``.
        """
        data = self._get("/Contact/api/getownertypereferencesasync")
        if data is None:
            return []
        return [OwnerType.model_validate(t) for t in data.get("OwnerTypeReferences", [])]

    def get_agencies(self) -> list[Agency]:
        """The 295 state budget agencies, grouped by branch.

        Executive budget reference data. It joins to nothing legislative —
        its ``IdentityID`` shares no value with a ``MemberID``, a
        ``CommitteeID``, or a personnel identity. Wired for completeness.

        This supersedes ``getorganizationlistasync``, which is not wired: it
        returns the same 295 identities with only a ``Name`` equal to
        ``AgencyName``, plus one extra row for the General Assembly itself.
        ``getorganizationbyidasync`` is not wired either; it answers 204 with
        an empty body for an unknown id rather than 404.

        Envelope key: ``Agencies``.
        """
        data = self._get("/Organization/api/getagencyreferencesasync")
        if data is None:
            return []
        return [Agency.model_validate(a) for a in data.get("Agencies", [])]

    def get_clerk_personnel(
        self,
        *,
        affiliation_id: int | None = None,
        effective_begin_date: str | date | datetime | None = None,
    ) -> list[Personnel]:
        """Committee clerks, from their own path.

        The rows of :meth:`get_personnel` with ``staff_role_type_id=1``: 54
        with no argument, 44 from ``effective_begin_date="2026-01-01"``, and
        2 for committee 28 (measured 2026-10-12).  All 11 fields arrive even
        with no argument, where the general list drops four.

        Args:
            affiliation_id: A ``CommitteeID``.
            effective_begin_date: Keep only assignments starting on or after
                this date.

        Envelope key: ``Personnels``.
        """
        return self._personnel("getclerkpersonnellistasync", affiliation_id, effective_begin_date)

    def get_staff_personnel(
        self,
        *,
        affiliation_id: int | None = None,
        effective_begin_date: str | date | datetime | None = None,
    ) -> list[Personnel]:
        """Committee DLS staff, from their own path.

        The rows of :meth:`get_personnel` with ``staff_role_type_id=2``: 59
        with no argument, 47 from ``effective_begin_date="2026-01-01"``,
        and 2 for committee 28 (measured 2026-10-12).  All 11 fields arrive
        even with no argument.

        Args:
            affiliation_id: A ``CommitteeID``.
            effective_begin_date: Keep only assignments starting on or after
                this date.

        Envelope key: ``Personnels``.
        """
        return self._personnel("getstaffpersonnellistasync", affiliation_id, effective_begin_date)

    def get_personnel_references(
        self,
        effective_begin_date: str | date | datetime | None = None,
    ) -> list[Personnel]:
        """The unfiltered personnel list under a second path.

        Byte for byte the answer of :meth:`get_personnel` with no argument:
        33 rows of seven keys that name people and no committee.  LIS ignores
        ``affiliationID`` here.  Prefer :meth:`get_personnel` with a filter.

        Args:
            effective_begin_date: Sent as ``effectiveBeginDate``.  Not
                exercised.

        Envelope key: ``Personnels``.
        """
        return self._personnel("getreferencesasync", None, effective_begin_date)

    def _personnel(
        self,
        tail: str,
        affiliation_id: int | None,
        effective_begin_date: str | date | datetime | None,
    ) -> list[Personnel]:
        params: dict = {}
        if affiliation_id is not None:
            params["affiliationID"] = affiliation_id

        if effective_begin_date is not None:
            params["effectiveBeginDate"] = _date_param(effective_begin_date)

        data = self._get(f"/Personnel/api/{tail}", params=params or None)
        if data is None:
            return []

        return [Personnel.model_validate(p) for p in data.get("Personnels", [])]

    def get_organizations(self) -> list[Organization]:
        """Every organization as an id and a name, 296 rows in 19 KB.

        The budget agencies of :meth:`get_agencies` without their branch or
        number, plus the General Assembly itself as ``749``.

        Envelope key: ``Organizations``.
        """
        data = self._get("/Organization/api/getorganizationlistasync")
        if data is None:
            return []

        return [Organization.model_validate(o) for o in data.get("Organizations", [])]

    def get_organization(self, organization_id: int) -> Organization | None:
        """One organization's name by ``IdentityID``.  ``None`` if unknown.

        ``749`` is the General Assembly.  An unknown id answers 204 with an
        empty body, not 404.

        Envelope key: ``Organizations`` (single-element list).
        """
        data = self._get(f"/Organization/api/getorganizationbyidasync/{organization_id}")
        if data is None:
            return None

        rows = data.get("Organizations", [])
        return Organization.model_validate(rows[0]) if rows else None

    # ------------------------------------------------------------------
    # Communications (what one chamber formally told the other)
    # ------------------------------------------------------------------

    def get_communications(
        self,
        *,
        session_code: int | None = None,
        session_id: int | None = None,
        chamber_code: str | None = None,
    ) -> list[CommunicationListItem]:
        """Floor communications, newest business last.

        **Every filter is optional, and that is a trap.** With no arguments
        LIS streams all 4,383 communications it holds, about 2 MB, reaching
        back to 1994-01-13. It has no paging. Always pass a session unless
        you want the lot.

        ``session_code`` and ``session_id`` agree: both returned the same 131
        rows for 20261. An unknown ``chamber_code`` answers 204 rather than
        400.

        The rows are shallow and name no bills. Call
        :meth:`get_communication` for those.

        Args:
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``. An alternative to ``session_code``.
            chamber_code: ``"H"`` or ``"S"``. Omit for both.

        Envelope key: ``CommunicationsList``.
        """
        params: dict = {}
        if session_code is not None:
            params["sessionCode"] = session_code

        if session_id is not None:
            params["sessionID"] = session_id

        if chamber_code:
            params["chamberCode"] = chamber_code

        data = self._get(
            "/LegislationCommunications/api/getlegislationcommunicationslistasync",
            params=params,
        )
        if data is None:
            return []
        return [CommunicationListItem.model_validate(c) for c in data.get("CommunicationsList", [])]

    def get_communication(
        self,
        communication_id: int | None = None,
        *,
        session_code: int | None = None,
        session_id: int | None = None,
        chamber_code: str | None = None,
        communication_type_id: int | None = None,
        communication_date: str | date | datetime | None = None,
        communication_number: int | None = None,
        reference_number: str | None = None,
    ) -> Communication | None:
        """One communication with its categories, bills, and patrons.

        ``communication_id`` alone is enough. The alternative is the full
        tuple of session, chamber, type id, date, and number; a partial tuple
        answers 400 with "Must have Vote Id or Vote # combined with Session
        Id or Session Code or Session Id or Session Code Combined with
        Chamber Code, Communication Type ID, Communication Date and
        Communication Number".

        **The ``voteID`` and ``voteNumber`` selectors are dead.** Both answer
        204 for every value tried, and no row carries a vote id. Do not reach
        here for votes.

        Ask for a communication older than May 2024 and the categories come
        back empty; see :class:`Communication`.

        Args:
            communication_id: ``CommunicationID`` from
                :meth:`get_communications`. The easy way in.

        Returns:
            The :class:`Communication`, or ``None`` when LIS answers 204.

        Envelope key: ``CommunicationResponse`` — note the *list* endpoint
        answers under ``CommunicationsList`` instead.
        """
        params: dict = {}
        if communication_id is not None:
            params["communicationID"] = communication_id

        if session_code is not None:
            params["sessionCode"] = session_code

        if session_id is not None:
            params["sessionID"] = session_id

        if chamber_code:
            params["chamberCode"] = chamber_code

        if communication_type_id is not None:
            params["communicationTypeID"] = communication_type_id

        if communication_date is not None:
            params["communicationDate"] = _date_param(communication_date)

        if communication_number is not None:
            params["communicationNumber"] = communication_number

        if reference_number is not None:
            params["referenceNumber"] = reference_number

        data = self._get(
            "/LegislationCommunications/api/getlegislationcommunicationsbyidasync",
            params=params,
        )
        if data is None:
            return None

        rows = data.get("CommunicationResponse", [])
        return Communication.model_validate(rows[0]) if rows else None

    def get_communication_files(self, communication_id: int) -> list[CommunicationFile]:
        """The generated files for one communication, HTML and JSON.

        The same pair the list row already carries, so reach for this only
        when you hold an id and not the row.

        The documented ``textFormat`` parameter is **silently ignored** —
        ``HTML``, ``JSON`` and ``PDF`` all returned both files — so it is not
        exposed.

        Envelope key: ``CommunicationFileResponse``.
        """
        data = self._get(
            "/LegislationCommunications/api/getlegislationcommunicationsbloburlasync",
            params={"communicationID": communication_id},
        )
        if data is None:
            return []
        return [
            CommunicationFile.model_validate(f) for f in data.get("CommunicationFileResponse", [])
        ]

    def get_communication_category_types(
        self,
        chamber_code: str | None = None,
    ) -> list[CommunicationCategoryType]:
        """Reference list of 231 communication sentences, 87 House and 144 Senate.

        The vocabulary behind ``CommunicationCategoryTypeID``, holding the
        singular and plural form of each clerk's sentence.

        Args:
            chamber_code: ``"H"`` or ``"S"``. Omit for both.

        Envelope key: ``CommunicationCategoryTypesReference``.
        """
        params = {"chamberCode": chamber_code} if chamber_code else None
        data = self._get(
            "/LegislationCommunications/api/getcommunicationcategoryreferencesasync",
            params=params,
        )
        if data is None:
            return []
        return [
            CommunicationCategoryType.model_validate(t)
            for t in data.get("CommunicationCategoryTypesReference", [])
        ]

    # ------------------------------------------------------------------
    # Minutes books (what a body actually did, with the votes attached)
    # ------------------------------------------------------------------

    def get_minutes_books(
        self,
        *,
        session_code: int | None = None,
        session_id: int | None = None,
        committee_id: int | None = None,
        chamber_code: str | None = None,
        start_date: str | date | datetime | None = None,
        end_date: str | date | datetime | None = None,
    ) -> list[MinutesBookListItem]:
        """Published minutes books for a session, newest business last.

        **One of ``session_code`` or ``session_id`` is required.**  Every
        other filter needs a session alongside it; LIS answers 400 with
        "Must provide either a valid Session ID or Session Code" otherwise —
        including for ``committee_id`` on its own.  The two session forms
        agree: both returned the same 388 rows for 20261 (checked
        2026-09-15).

        The rows are shallow and name no bills.  Call
        :meth:`get_minutes_book` for the categories, entries, and votes.

        Args:
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.  An alternative to ``session_code``.
            committee_id: ``CommitteeID`` from :meth:`get_committees`.
                Committee books only, so it excludes every floor book.
            chamber_code: ``"H"`` or ``"S"``.  Omit for both.
            start_date: Inclusive lower bound on ``MinutesDate``.
            end_date: Inclusive upper bound on ``MinutesDate``.

        Envelope key: ``Minutes`` — note the *detail* call answers under
        ``MinutesBooks`` instead, and the OpenAPI spec says ``ListItems`` for
        both and is wrong twice.
        """
        params: dict = {}
        if session_code is not None:
            params["sessionCode"] = session_code

        if session_id is not None:
            params["sessionID"] = session_id

        if committee_id is not None:
            params["committeeID"] = committee_id

        if chamber_code:
            params["chamberCode"] = chamber_code

        if start_date is not None:
            params["startDate"] = _date_param(start_date)

        if end_date is not None:
            params["endDate"] = _date_param(end_date)

        data = self._get("/MinutesBook/api/getpublishedminutesbooklistasync", params=params)
        if data is None:
            return []
        return [MinutesBookListItem.model_validate(b) for b in data.get("Minutes", [])]

    def get_minutes_book(
        self,
        minutes_book_id: int | None = None,
        calendar_id: int | None = None,
    ) -> MinutesBook | None:
        """One book with its categories, entries, activities, and votes.

        Address it by either key.  Passing neither returns 400
        ("minutesBookID should be supplied if no calendarID is supplied").
        A floor book's ``CalendarID`` comes off
        :attr:`~va_lis_client.models.minutes.MinutesBookListItem.MinutesCalendars`.

        Senate book 3861 (2026-04-22) carries 11 categories, 195 entries, 181
        of them on a bill, and 205 activities of which 171 name a vote.

        Args:
            minutes_book_id: ``MinutesBookID`` from :meth:`get_minutes_books`.
            calendar_id: ``CalendarID`` of the book's floor calendar.

        Returns:
            The :class:`MinutesBook`, or ``None`` when LIS answers 204.

        Envelope key: ``MinutesBooks`` — a list holding one book.
        """
        params: dict = {}
        if minutes_book_id is not None:
            params["minutesBookID"] = minutes_book_id

        if calendar_id is not None:
            params["calendarID"] = calendar_id

        data = self._get("/MinutesBook/api/getminutesbookasync", params=params)
        if data is None:
            return None

        rows = data.get("MinutesBooks", [])
        return MinutesBook.model_validate(rows[0]) if rows else None

    def get_minutes_book_interactive(
        self,
        minutes_book_id: int | None = None,
        calendar_id: int | None = None,
        reference_number: str | None = None,
        session_code: int | None = None,
    ) -> MinutesBook | None:
        """The same book shaped for the LIS web calendar.  Prefer the plain call.

        **This view is lossy.**  Against Senate book 3861 it returned 168
        entries where :meth:`get_minutes_book` returned 195, 8 categories
        against 11, and it dropped ``EventCode``, ``IsBlock`` and every one
        of the 171 ``VoteTally`` strings (checked 2026-09-15).  It gains only
        narrative summaries: 39 against 18.  Reach for it when you want the
        prose, never when you want the votes.

        ``reference_number`` and ``session_code`` must travel together; LIS
        answers 400 for either alone.

        Envelope key: ``MinutesBooks``.
        """
        params: dict = {}
        if minutes_book_id is not None:
            params["minutesBookID"] = minutes_book_id

        if calendar_id is not None:
            params["calendarID"] = calendar_id

        if reference_number is not None:
            params["referenceNumber"] = reference_number

        if session_code is not None:
            params["sessionCode"] = session_code

        data = self._get("/MinutesBook/api/getminutesbookinteractivecalendarasync", params=params)
        if data is None:
            return None

        rows = data.get("MinutesBooks", [])
        return MinutesBook.model_validate(rows[0]) if rows else None

    def get_action_reference_types(self) -> list[ActionReferenceType]:
        """Reference list of 15 activity-reference kinds.

        ``ObjectName`` names the entity a reference points at, so it says
        which service resolves ``ActivityReference.ReferenceID``.  The ids
        run 1-9 and 11-16; there is no 10.

        Envelope key: ``ActionReferenceTypeList``.
        """
        data = self._get("/MinutesBook/api/getactionreferencetypelistasync")
        if data is None:
            return []
        return [
            ActionReferenceType.model_validate(t) for t in data.get("ActionReferenceTypeList", [])
        ]

    # ------------------------------------------------------------------
    # Advanced legislation search (the only keyword-capable bill endpoint)
    # ------------------------------------------------------------------

    def search_legislation(
        self,
        *,
        session_code: int | None = None,
        session_id: int | None = None,
        keyword: str | None = None,
        keyword_location: str | None = None,
        keyword_all_sessions: bool | None = None,
        chamber_code: str | None = None,
        status_id: int | None = None,
        category_id: int | None = None,
        committee_id: int | None = None,
        member_id: int | None = None,
        patron_types: list[int] | None = None,
        subject_index_id: int | None = None,
        chapter_number: str | None = None,
        legislation_numbers: list[str] | None = None,
        legislation_ids: list[int] | None = None,
        start_date: str | date | datetime | None = None,
        end_date: str | date | datetime | None = None,
        exclude_failed: bool | None = None,
        current_status: bool | None = None,
        summary_length: int | None = None,
        extra: dict | None = None,
    ) -> list[LegislationSearchResult]:
        """Search bills by keyword and filters.  Rows arrive exactly as sent.

        This is the only LIS endpoint that searches bill text and summaries,
        and the only one that filters on status, category, committee, chapter
        number, and patron at once.  Criteria ride a JSON body, not the query
        string.

        **Warning: there is no paging.**  ``pageNumber`` and ``pageSize`` on
        the query string change nothing, and the body carries no paging
        fields, so every search returns its whole result set in one response.
        A whole-session search (``session_code`` alone) measured 3,007 rows
        and 5.7 MB for 20261.  Send a filter.

        **Warning: a bill can arrive more than once.**  The endpoint returns
        one row per summary version *and* emits exact duplicate rows.  This
        method does not touch them, because deciding which row wins is
        interpretation.  Call ``LISService.search_bills`` for one row per
        bill, or ``LISService.search_bill_summaries`` for one row per summary
        version; see :class:`LegislationSearchResult` for the measurements.

        **Do not trust ``X-Pagination.TotalCount`` here.**  It reported 2,836
        against 3,007 rows holding 2,827 unique IDs, and matches no count
        derivable from the payload.  Count the rows yourself.

        Two traps in the criteria themselves:

        - ``subject_index_id`` returns HTTP 204 every time, with either
          session parameter.  The subject vocabulary from
          ``/LegislationSubject`` is therefore not searchable.  The parameter
          is wired so the behavior is discoverable, not because it works.
        - A common word finds nothing.  ``keyword="the"`` returned 204 while
          ``keyword="firearm"`` returned 50 bills, so LIS drops stop words.

        Args:
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.  Send one session parameter or the other.
            keyword: A keyword expression.  Supports ``AND``, ``OR``, and
                parentheses.
            keyword_location: Where to search, ``"Summary"`` or
                ``"Bill Text"``.
            keyword_all_sessions: Search every session, not just this one.
            chamber_code: ``"H"`` or ``"S"``.  Sent as the body key
                ``Chambercode`` — lowercase ``c``, unlike every other key.
            status_id: ``LegislationStatusID`` from
                :meth:`get_legislation_statuses`.
            category_id: ``LegislationCategoryID`` from
                :meth:`get_legislation_categories`.
            committee_id: Restrict to bills before one committee.
            member_id: Restrict to one patron.  A second route to the data
                :meth:`get_member_legislation` returns.
            patron_types: ``PatronTypeID`` values, e.g. ``[1]`` for chief
                patron only.  A flat list of integers, unlike the two below.
            subject_index_id: Always yields 204; see above.
            chapter_number: e.g. ``"CHAP0350"``.
            legislation_numbers: Unpadded numbers, e.g. ``["HB1", "SB2"]``.
                LIS wants each entry wrapped as
                ``{"LegislationNumber": "HB1"}`` and answers 400 to a flat
                list of strings; this method wraps them for you.
            legislation_ids: ``LegislationID`` values, e.g. ``[98525]``.
                Wrapped the same way, as ``{"LegislationId": 98525}`` —
                lowercase ``d``, unlike the ``LegislationIDs`` list holding
                it.
            start_date: Lower bound on the status date.
            end_date: Upper bound on the status date.
            exclude_failed: Drop bills that failed.
            current_status: Match only the bill's current status.
            summary_length: Truncate the summary HTML to this many characters.
            extra: Body keys merged last, for a field this signature omits.

        A compound keyword search rides ``extra``.  ``Keywords`` takes a list
        of ``{"Keyword": ..., "Operator": "AND" | "OR"}`` objects, and an
        object may nest its own ``Keywords`` list to act as parentheses::

            client.search_legislation(
                session_code=20261,
                keyword_location="Summary",
                extra={"Keywords": [
                    {"Keyword": "firearm", "Operator": "AND"},
                    {"Keyword": "ammunition", "Operator": "AND"},
                ]},
            )

        Envelope key: ``Legislations``.
        """
        body = _search_body(
            session_code=session_code,
            session_id=session_id,
            keyword=keyword,
            keyword_location=keyword_location,
            keyword_all_sessions=keyword_all_sessions,
            chamber_code=chamber_code,
            status_id=status_id,
            category_id=category_id,
            committee_id=committee_id,
            member_id=member_id,
            patron_types=patron_types,
            subject_index_id=subject_index_id,
            chapter_number=chapter_number,
            legislation_numbers=legislation_numbers,
            legislation_ids=legislation_ids,
            start_date=start_date,
            end_date=end_date,
            exclude_failed=exclude_failed,
            current_status=current_status,
            summary_length=summary_length,
            extra=extra,
        )

        data = self._post("/AdvancedLegislationSearch/api/getlegislationlistasync", json=body)
        if data is None:
            return []

        return [LegislationSearchResult.model_validate(row) for row in data.get("Legislations", [])]

    def get_most_frequent_legislation(self, session_id: int) -> list[LegislationSearchResult]:
        """The 100 most frequently viewed bills in a session.

        Rows match :meth:`search_legislation`, so the duplicate-row warning on
        :class:`LegislationSearchResult` applies here too.

        This endpoint takes ``sessionID`` only.  It has no ``sessionCode``
        parameter, so resolve the code first with
        ``LISService.session_id(session_code)``.

        Args:
            session_id: e.g. ``59`` for the 2026 Regular Session.

        Envelope key: ``Legislations``.
        """
        data = self._get(
            "/AdvancedLegislationSearch/api/getmostfrequentlegislationsasync",
            params={"sessionID": session_id},
        )
        if data is None:
            return []

        return [LegislationSearchResult.model_validate(row) for row in data.get("Legislations", [])]

    def get_legislative_numbers(
        self,
        session_code: int | None = None,
        session_id: int | None = None,
    ) -> list[LegislationNumberEntry]:
        """Every bill number in a session paired with its ``LegislationID``.

        The cheapest number-to-ID map LIS offers: 3,646 rows and 268 KB for
        20261, against roughly 3 MB for :meth:`get_session_bills`.  Reach for
        it when you resolve numbers in bulk and want nothing else.

        **The spec's ``startDate`` and ``endDate`` do nothing.**  LIS accepts
        them and stores them in the cache key, then returns the full list
        anyway: a 1990 range still returned all 3,646 rows for 20261
        (measured 2026-09-15).  They are left off this signature on purpose.

        A session parameter is required; sending dates alone returns 400.

        Args:
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.

        Envelope key: ``LegislationNumbersList``.
        """
        params: dict = {}
        if session_code is not None:
            params["sessionCode"] = session_code
        if session_id is not None:
            params["sessionID"] = session_id

        data = self._get("/AdvancedLegislationSearch/api/getlegislativenumbersasync", params=params)
        if data is None:
            return []

        return [
            LegislationNumberEntry.model_validate(row)
            for row in data.get("LegislationNumbersList", [])
        ]

    def get_introduction_dates(
        self,
        session_code: int | None = None,
        session_id: int | None = None,
    ) -> list[IntroductionDate]:
        """The distinct dates bills were introduced on, 91 rows for 20261.

        The list carries dates and nothing else.  Note that the search result
        rows themselves always return ``IntroductionDate`` null, so this
        endpoint is the only place the dates surface.

        Args:
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.

        Envelope key: ``LegislationDatesList``.
        """
        params: dict = {}
        if session_code is not None:
            params["sessionCode"] = session_code
        if session_id is not None:
            params["sessionID"] = session_id

        data = self._get(
            "/AdvancedLegislationSearch/api/getintroductiondatelistasync", params=params
        )
        if data is None:
            return []

        return [
            IntroductionDate.model_validate(row) for row in data.get("LegislationDatesList", [])
        ]

    def get_legislation_categories(self) -> list[LegislationCategory]:
        """Reference list of 30 legislation categories, e.g. 1 ``Introduced``.

        The vocabulary behind :meth:`search_legislation`'s ``category_id``.

        The sibling status vocabulary is :meth:`get_search_statuses`, which
        returns the same 52 rows as :meth:`get_legislation_statuses`.

        Envelope key: ``LegislationCategoriesList``.
        """
        data = self._get("/AdvancedLegislationSearch/api/getlegislationcategoryreferencesasync")
        if data is None:
            return []

        return [
            LegislationCategory.model_validate(row)
            for row in data.get("LegislationCategoriesList", [])
        ]

    def get_search_statuses(self) -> list[LegislationStatus]:
        """The legislation statuses again, from the search service.

        The 52 rows of :meth:`get_legislation_statuses` under the same
        ``References`` key.  Use either.

        Envelope key: ``References``.
        """
        data = self._get("/AdvancedLegislationSearch/api/getstatusreferencesasync")
        if data is None:
            return []

        return [LegislationStatus.model_validate(r) for r in data.get("References", [])]

    def get_subjects(
        self,
        session_code: int | None = None,
        session_id: int | None = None,
    ) -> list[Subject]:
        """A session's subject index headings, 506 for 20261.

        **The headings join to nothing.**  No bill row carries a
        ``SubjectIndexID``, and :meth:`search_legislation` answers 204 to
        one.  See :class:`Subject`.

        **A session is required.**  With none LIS answers 400.  The two
        session forms returned the same 506 rows (checked 2026-10-12).

        Args:
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.  An alternative to ``session_code``.

        Envelope key: ``SubjectsReference``.
        """
        params: dict = {}
        if session_code is not None:
            params["sessionCode"] = session_code

        if session_id is not None:
            params["sessionID"] = session_id

        data = self._get("/LegislationSubject/api/getsubjectreferencesasync", params=params)
        if data is None:
            return []

        return [Subject.model_validate(r) for r in data.get("SubjectsReference", [])]

    # ------------------------------------------------------------------
    # Statistics (counts LIS aggregates; they do not always add up)
    # ------------------------------------------------------------------

    def get_chamber_statistics(
        self,
        session_code: int | None = None,
        session_id: int | None = None,
    ) -> list[ChamberStatistic]:
        """Bill counts for a session, one row per chamber and legislation type.

        Six rows for a full session, fewer when a type is empty: 20271 had
        five before it convened.  Both chambers always come back.  LIS
        silently ignores ``chamberCode`` here, so this method does not take
        one.

        **A session is required.**  With none LIS answers 400, "Must provide
        either a valid Session ID or Session Code".  The two forms returned
        the same rows for 20261 (checked 2026-09-17).

        Read :class:`ChamberStatistic` before you sum a column.  The outcome
        columns add up differently from session to session, and in 20261 they
        overshoot the session's own totals by a bill or two.

        Args:
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.  An alternative to ``session_code``.

        Envelope key: ``ChamberStatistics``.
        """
        params: dict = {}
        if session_code is not None:
            params["sessionCode"] = session_code

        if session_id is not None:
            params["sessionID"] = session_id

        data = self._get("/Statistics/api/getchamberstatisticsasync", params=params)
        if data is None:
            return []

        return [ChamberStatistic.model_validate(r) for r in data.get("ChamberStatistics", [])]

    def get_committee_statistics(
        self,
        chamber_code: str,
        *,
        session_code: int | None = None,
        session_id: int | None = None,
    ) -> list[CommitteeStatistic]:
        """Referral counts for one chamber's standing committees in a session.

        14 House and 11 Senate rows for 20261.  No subcommittee ever appears.

        **Both a chamber and a session are required.**  Without the chamber
        LIS answers 400, "The chamberCode field is required."  Without a
        session it answers 400, "Must provide either a valid Session ID or
        Session Code".  An unknown chamber answers 204.  The two session forms
        returned the same rows for House 20261 (checked 2026-09-17).

        Every row counts bills from both chambers: the ``H`` and ``S`` column
        prefixes name the bill's chamber, not the committee's.  See
        :class:`CommitteeStatistic`.

        Args:
            chamber_code: ``"H"`` or ``"S"``, the committees' chamber.
            session_code: e.g. ``20261``.
            session_id: e.g. ``59``.  An alternative to ``session_code``.

        Envelope key: ``CommitteeStatistics``.
        """
        params: dict = {"chamberCode": chamber_code}
        if session_code is not None:
            params["sessionCode"] = session_code

        if session_id is not None:
            params["sessionID"] = session_id

        data = self._get("/Statistics/api/getcommitteestatisticsasync", params=params)
        if data is None:
            return []

        return [CommitteeStatistic.model_validate(r) for r in data.get("CommitteeStatistics", [])]


def _date_param(value: str | date | datetime) -> str:
    """``YYYY-MM-DD`` for a date or datetime; a string passes through as sent."""
    if isinstance(value, datetime):
        return value.date().isoformat()

    if isinstance(value, date):
        return value.isoformat()

    return value


def _search_body(
    *,
    session_code: int | None = None,
    session_id: int | None = None,
    keyword: str | None = None,
    keyword_location: str | None = None,
    keyword_all_sessions: bool | None = None,
    chamber_code: str | None = None,
    status_id: int | None = None,
    category_id: int | None = None,
    committee_id: int | None = None,
    member_id: int | None = None,
    patron_types: list[int] | None = None,
    subject_index_id: int | None = None,
    chapter_number: str | None = None,
    legislation_numbers: list[str] | None = None,
    legislation_ids: list[int] | None = None,
    start_date: str | date | datetime | None = None,
    end_date: str | date | datetime | None = None,
    exclude_failed: bool | None = None,
    current_status: bool | None = None,
    summary_length: int | None = None,
    extra: dict | None = None,
) -> dict:
    """The JSON body the advanced search paths share, built from keyword criteria.

    ``/AdvancedLegislationSearch`` and three ``/Legislation`` POST paths take
    the same ``AdvancedSearch`` schema.  :meth:`LISClient.search_legislation`
    documents each criterion.
    """
    body: dict = {}
    if session_code is not None:
        body["SessionCode"] = session_code
    if session_id is not None:
        body["SessionID"] = session_id
    if keyword is not None:
        body["KeywordExpression"] = keyword
    if keyword_location is not None:
        body["KeywordLocation"] = keyword_location
    if keyword_all_sessions is not None:
        body["KeywordUseGlobalSessionSearch"] = keyword_all_sessions
    if chamber_code is not None:
        body["Chambercode"] = chamber_code  # lowercase "c" — LIS spells it this way
    if status_id is not None:
        body["LegislationStatusID"] = status_id
    if category_id is not None:
        body["LegislationCategoryID"] = category_id
    if committee_id is not None:
        body["CommitteeID"] = committee_id
    if member_id is not None:
        body["MemberID"] = member_id
    if patron_types:
        body["PatronTypes"] = patron_types
    if subject_index_id is not None:
        body["SubjectIndexID"] = subject_index_id
    if chapter_number is not None:
        body["ChapterNumber"] = chapter_number
    if legislation_numbers:
        # LIS rejects a flat list of strings with 400; each entry has to
        # be its own object.  Accept either shape and wrap the scalars.
        body["LegislationNumbers"] = [
            n if isinstance(n, dict) else {"LegislationNumber": n} for n in legislation_numbers
        ]
    if legislation_ids:
        # Same wrapper, and the key inside is "LegislationId" — lowercase
        # "d", unlike the "LegislationIDs" that holds it.
        body["LegislationIDs"] = [
            i if isinstance(i, dict) else {"LegislationId": i} for i in legislation_ids
        ]
    if start_date is not None:
        body["StartDate"] = _date_param(start_date)
    if end_date is not None:
        body["EndDate"] = _date_param(end_date)
    if exclude_failed is not None:
        body["ExcludeFailed"] = exclude_failed
    if current_status is not None:
        body["CurrentStatus"] = current_status
    if summary_length is not None:
        body["SummaryLength"] = summary_length

    if extra:
        body.update(extra)

    return body
