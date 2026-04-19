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


class ClientConfigTest(unittest.TestCase):
    """Unit tests for client configuration (no network calls)."""

    def test_missing_api_key_raises(self):
        with self.assertRaises(LISClientError):
            LISClient(api_key="")

    def test_explicit_api_key(self):
        client = LISClient(api_key="test-key-123")
        self.assertEqual(client.api_key, "test-key-123")
        self.assertEqual(client._headers(), {"WebAPIKey": "test-key-123"})
