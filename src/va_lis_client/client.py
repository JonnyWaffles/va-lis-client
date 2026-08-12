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

import requests as _requests

from va_lis_client.exceptions import LISClientError
from va_lis_client.http import requests_session
from va_lis_client.models import (
    ActorType,
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
    Partner,
    Session,
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

    def _request(self, method: str, path: str, **kwargs):
        """Send an HTTP request with exponential backoff on transient failures.

        Retries on connection errors, timeouts, and HTTP 429/5xx responses.
        Respects ``Retry-After`` header when present.  Logs all response
        headers on 429 to help discover LIS rate-limit SLAs.
        """
        url = f"{BASE_URL}{path}"
        kwargs.setdefault("headers", self._headers())
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

    def _get(self, path: str, params: dict | None = None):
        return self._request("GET", path, params=params)

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
    ) -> list[LegislationSummaryItem]:
        """Lightweight bill list for a session.

        Provide **one of**:

        - ``session_code``: e.g. ``20261`` (2026 Regular Session)
        - ``session_id``: e.g. ``59``

        Returns every bill in the session with basic metadata and the
        chief patron.  For full detail (all patrons, dates, status ID),
        call :meth:`get_bill` with the ``LegislationID``.

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
        data = self._get("/Legislation/api/getlegislationsessionlistasync", params=params)
        if data is None:
            return []
        return [
            LegislationSummaryItem.model_validate(item) for item in data.get("Legislations", [])
        ]

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
        """Reference list of ~3,900 legislation event types.

        Key fields for filtering:

        - ``IsPassage``: True for passage/defeat events (H5000/S5000 family)
        - ``EventCode``: Prefix = actor (H=House, S=Senate, G=Governor)
        - ``LegislationDescription``: Human-readable action name

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
