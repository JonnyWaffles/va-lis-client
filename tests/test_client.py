"""Integration tests for the LIS API client.

These tests hit the live LIS API and require a valid ``LIS_API_KEY``
environment variable.

Run with:
    pytest tests/
    # or to run only unit tests (no network):
    pytest tests/ -k "not Live"
"""

import os
import unittest

from va_lis_client import LISClient, LISClientError
from va_lis_client.models import (
    Heartbeat,
    Legislation,
    LegislationSummary,
    LegislationTextItem,
    LegislationVersion,
    Partner,
    Session,
)

_skip_live = unittest.skipUnless(
    os.environ.get("LIS_API_KEY"),
    "LIS_API_KEY not set — skipping live API tests",
)


@_skip_live
class LiveApiKeyValidationTest(unittest.TestCase):
    """Verify the API key is registered and active."""

    def setUp(self):
        self.client = LISClient()

    def test_api_key_is_registered(self):
        partner = self.client.check_api_key()
        self.assertIsNotNone(
            partner,
            "API key returned 204 (not found) — register at "
            "https://lis.virginia.gov/apiregistration",
        )
        self.assertIsInstance(partner, Partner)
        self.assertTrue(partner.IsActive)

    def test_heartbeat_returns_model(self):
        hb = self.client.heartbeat(service="Legislation")
        self.assertIsInstance(hb, Heartbeat)
        self.assertTrue(hb.Success)
        self.assertEqual(hb.Service, "Legislation")


@_skip_live
class LiveSessionTest(unittest.TestCase):
    def setUp(self):
        self.client = LISClient()

    def test_get_sessions_returns_models(self):
        sessions = self.client.get_sessions(year=2026)
        self.assertGreater(len(sessions), 0, "No 2026 sessions returned")
        self.assertIsInstance(sessions[0], Session)
        self.assertEqual(sessions[0].SessionYear, 2026)


@_skip_live
class LiveLegislationTest(unittest.TestCase):
    def setUp(self):
        self.client = LISClient()

    def test_get_session_bills(self):
        bills = self.client.get_session_bills(session_code=20261)
        self.assertGreater(len(bills), 0)
        hb1 = next((b for b in bills if b.LegislationNumber == "HB1"), None)
        self.assertIsNotNone(hb1)
        self.assertEqual(hb1.ChamberCode, "H")

    def test_get_bill_detail(self):
        bill = self.client.get_bill(98525)
        self.assertIsInstance(bill, Legislation)
        self.assertEqual(bill.LegislationNumber, "HB1")
        self.assertGreater(len(bill.Patrons), 0)

    def test_get_legislation_versions(self):
        versions = self.client.get_legislation_versions()
        self.assertGreater(len(versions), 0)
        self.assertIsInstance(versions[0], LegislationVersion)
        intro = next((v for v in versions if v.Name == "Introduced"), None)
        self.assertIsNotNone(intro)


@_skip_live
class LiveCarryOverIdentityTest(unittest.TestCase):
    """Carry-over reuses LegislationID across an even→odd session pair.

    Anchored on the settled 2024→2025 term: HB1013 (LegislationID 91089)
    was carried over from 20241 into 20251, so these facts are fixed
    history and stable to assert.
    """

    def setUp(self):
        self.client = LISClient()

    def test_carried_bill_keeps_id_in_both_session_lists(self):
        ids_2024 = {b.LegislationID for b in self.client.get_session_bills(session_code=20241)}
        ids_2025 = {b.LegislationID for b in self.client.get_session_bills(session_code=20251)}

        shared = ids_2024 & ids_2025
        self.assertIn(91089, shared)
        self.assertGreater(len(shared), 100, "Expected hundreds of carried-over IDs")

    def test_sessions_list_is_the_lineage_record(self):
        bill = self.client.get_bill(91089)
        self.assertEqual(bill.LegislationNumber, "HB1013")

        self.assertIsNone(
            bill.SessionCode,
            "Top-level SessionCode expected to be None — session membership lives in Sessions[]",
        )
        codes = {s.SessionCode for s in bill.Sessions}
        self.assertEqual(codes, {"20241", "20251"})


@_skip_live
class LiveTextAndSummaryTest(unittest.TestCase):
    def setUp(self):
        self.client = LISClient()

    def test_get_bill_texts(self):
        texts = self.client.get_bill_texts(legislation_number="HB1", session_code=20261)
        self.assertGreater(len(texts), 0)
        self.assertIsInstance(texts[0], LegislationTextItem)
        doc_codes = {t.DocumentCode for t in texts}
        self.assertIn("HB1", doc_codes)

    def test_get_bill_text_detail(self):
        details = self.client.get_bill_text_detail(legislation_id=98525, session_code=20261)
        self.assertGreater(len(details), 0)
        enrolled = next((d for d in details if d.LegislationVersion == "Enrolled"), None)
        self.assertIsNotNone(enrolled)
        self.assertIn("minimum wage", (enrolled.DraftText or "").lower())

    def test_get_bill_summaries(self):
        summaries = self.client.get_bill_summaries(legislation_number="HB1", session_code=20261)
        self.assertGreater(len(summaries), 0)
        self.assertIsInstance(summaries[0], LegislationSummary)
        passed = next(
            (s for s in summaries if "PASSED" in (s.SummaryVersion or "")),
            None,
        )
        self.assertIsNotNone(passed, "Expected a 'SUMMARY AS PASSED' entry")


@_skip_live
class LivePaginationTest(unittest.TestCase):
    """Paging rides an X-Pagination request header, not the query string."""

    SESSION = 20271

    def setUp(self):
        self.client = LISClient()

    def test_unpaged_list_reports_its_own_total(self):
        page = self.client.get_session_bills(session_code=self.SESSION)
        self.assertIsNotNone(page.pagination, "No X-Pagination header on the response")
        self.assertEqual(len(page), page.pagination.TotalCount)

    def test_page_size_caps_the_rows(self):
        page = self.client.get_session_bills(session_code=self.SESSION, page_size=3)
        self.assertEqual(len(page), 3)
        self.assertGreater(page.pagination.TotalCount, 3)
        self.assertTrue(page.pagination.HasNext)
        self.assertFalse(page.pagination.HasPrevious)

    def test_skip_offsets_the_window(self):
        first = self.client.get_session_bills(session_code=self.SESSION, page_size=3, skip=0)
        second = self.client.get_session_bills(session_code=self.SESSION, page_size=3, skip=3)

        numbers = {b.LegislationNumber for b in first}
        self.assertEqual(numbers & {b.LegislationNumber for b in second}, set())
        self.assertTrue(second.pagination.HasPrevious)
        self.assertEqual(second.pagination.SkippedRecords, 3)

    def test_bill_count_avoids_the_full_download(self):
        count = self.client.get_session_bill_count(session_code=self.SESSION)
        self.assertIsInstance(count, int)
        self.assertGreater(count, 0)

    def test_page_number_is_ignored_by_the_server(self):
        """The server always echoes PageNumber as 1; CurrentPage is the real one."""
        page = self.client.get_session_bills(session_code=self.SESSION, page_size=3, skip=6)
        self.assertEqual(page.pagination.PageNumber, 1)
        self.assertEqual(page.pagination.CurrentPage, 3)


@_skip_live
class LiveTextSessionScopeTest(unittest.TestCase):
    """LISService.resolve_bill_id leans on the text endpoint being session scoped.

    HB1 completed in the 2026 Regular Session, so it never carried into 20271.
    If this endpoint ever stops scoping by session, the cheap id lookup would
    silently resolve a bill into a session it does not belong to.
    """

    def setUp(self):
        self.client = LISClient()

    def test_text_list_answers_for_the_owning_session(self):
        texts = self.client.get_bill_texts(legislation_number="HB1", session_code=20261)
        self.assertGreater(len(texts), 0)
        self.assertEqual({t.LegislationID for t in texts}, {98525})

    def test_text_list_is_empty_for_a_session_the_bill_is_not_in(self):
        bills = self.client.get_session_bills(session_code=20271)
        self.assertNotIn("HB1", {b.LegislationNumber for b in bills})

        texts = self.client.get_bill_texts(legislation_number="HB1", session_code=20271)
        self.assertEqual(texts, [])


class ClientConfigTest(unittest.TestCase):
    """Unit tests for client configuration (no network calls)."""

    def test_missing_api_key_raises(self):
        with self.assertRaises(LISClientError):
            LISClient(api_key="")

    def test_explicit_api_key(self):
        client = LISClient(api_key="test-key-123")
        self.assertEqual(client.api_key, "test-key-123")
        self.assertEqual(client._headers(), {"WebAPIKey": "test-key-123"})
