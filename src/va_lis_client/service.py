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
  ``Name`` instead.
- Bill text spans two endpoints whose rows match on ``LegislationTextID``,
  and the body arrives as HTML.

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
from html.parser import HTMLParser

from va_lis_client.client import LISClient
from va_lis_client.exceptions import (
    BillNotFoundError,
    InvalidBillNumberError,
    TextVersionNotFoundError,
)
from va_lis_client.models import (
    Legislation,
    LegislationEventType,
    LegislationStatus,
    LegislationSummaryItem,
    LegislationTextDetail,
    LegislationTextItem,
)

DEFAULT_BILL_LIST_TTL = 15 * 60

_BILL_NUMBER_RE = re.compile(r"([A-Z]+)(\d+)")


class LISService:
    """Resolution and reference joins over a :class:`LISClient`.

    Args:
        client: The transport client to call.  Omit it to build a
            :class:`LISClient` from the ``LIS_API_KEY`` environment variable.
        bill_list_ttl: Seconds to keep a session's bill list.  Bills change
            through a session, so this list expires.  The event type and
            status vocabularies are static, so the service keeps them for its
            whole life.

    Caches live on the instance, not on the module, so two services built with
    different API keys never share data.
    """

    def __init__(
        self,
        client: LISClient | None = None,
        *,
        bill_list_ttl: float = DEFAULT_BILL_LIST_TTL,
    ):
        self.client = client if client is not None else LISClient()
        self.bill_list_ttl = bill_list_ttl

        self._bill_lists: dict[int, tuple[float, list[LegislationSummaryItem]]] = {}
        self._event_types: dict[str, LegislationEventType] | None = None
        self._statuses_by_name: dict[str, LegislationStatus] | None = None
        self._statuses_by_id: dict[int, LegislationStatus] | None = None

        # One lock per session code, so a cold fetch for one session does not
        # block a fetch for another.  ``_guard`` protects the lock table
        # itself; ``_reference_lock`` covers both static vocabularies.
        self._bill_list_locks: dict[int, threading.Lock] = {}
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

    def event_types_by_code(self) -> dict[str, LegislationEventType]:
        """The event type reference, keyed by ``EventCode``.

        The endpoint returns 3,912 rows with ``LegislationEventTypeID`` null,
        so ``EventCode`` is the only join key onto a bill's events.  The
        vocabulary is static, so the service fetches it once.

        That response is 2.19 MB and the endpoint ignores ``X-Pagination``
        (measured 2026-08-28), so caching is the only way to avoid paying
        for it twice.
        """
        if self._event_types is None:
            with self._reference_lock:
                if self._event_types is None:
                    types = self.client.get_event_types()
                    self._event_types = {t.EventCode: t for t in types if t.EventCode}

        return self._event_types

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
        """Drop every cached bill list and reference vocabulary."""
        with self._guard:
            self._bill_lists.clear()

        with self._reference_lock:
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
