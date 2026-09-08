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
from collections import Counter

import requests

from va_lis_client import LISClient, LISClientError, LISService
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


@_skip_live
class LiveVoteTest(unittest.TestCase):
    """The ``/Vote`` service, reached through a bill event's ``VoteID``.

    These IDs come from HB1 in the 2026 Regular Session, which completed, so
    its record is final and the numbers below are stable.
    """

    SENATE_FLOOR = 300418  # Passed Senate (21-Y 19-N 0-A)
    HOUSE_FLOOR = 294006  # Read third time and passed House (64-Y 34-N 0-A)
    HOUSE_COMMITTEE = 291609  # Reported from Labor and Commerce (15-Y 7-N)
    SENATE_BLOCK = 300173  # one 40-member roll call covering 50 bills
    SENATE_VOICE = 300174  # a voice vote: no members at all

    def setUp(self):
        self.client = LISClient()

    def test_a_floor_vote_carries_every_member(self):
        vote = self.client.get_vote(self.SENATE_FLOOR)

        self.assertEqual(vote.VoteID, self.SENATE_FLOOR)
        self.assertEqual(vote.ChamberCode, "S")
        self.assertEqual(vote.VoteType, "Floor")
        self.assertEqual(len(vote.vote_members), 40)

    def test_a_committee_vote_names_its_committee(self):
        vote = self.client.get_vote(self.HOUSE_COMMITTEE)

        self.assertEqual(vote.VoteType, "Committee")
        self.assertEqual(vote.CommitteeName, "Labor and Commerce")
        self.assertEqual(len(vote.vote_members), 22)

    def test_response_code_x_is_absent_from_the_tally(self):
        """Summing the member rows does not reproduce VoteTally."""
        vote = self.client.get_vote(self.HOUSE_FLOOR)

        codes = [m.ResponseCode for m in vote.vote_members]
        self.assertEqual(codes.count("X"), 2)
        self.assertEqual(vote.VoteTally, "(64-Y 34-N 0-A)")
        self.assertEqual(len(vote.vote_members), 100)

    def test_a_block_vote_covers_many_bills(self):
        vote = self.client.get_vote(self.SENATE_BLOCK)

        self.assertTrue(vote.IsBlock)
        self.assertEqual(len(vote.vote_members), 40)
        self.assertGreater(len(vote.vote_legislation), 1)

    def test_a_voice_vote_records_no_members(self):
        vote = self.client.get_vote(self.SENATE_VOICE)

        self.assertTrue(vote.IsVoice)
        self.assertEqual(vote.vote_members, [])
        self.assertEqual(vote.VoteTally, "(Voice Vote)")

    def test_the_vote_event_code_can_disagree_with_the_event(self):
        """Vote 294006 says H9999; the event that points at it says H5000."""
        vote = self.client.get_vote(self.HOUSE_FLOOR)
        events = self.client.get_bill_events(legislation_id=98525)

        source = next(e for e in events if e.VoteID == self.HOUSE_FLOOR)
        self.assertEqual(source.EventCode, "H5000")
        self.assertNotEqual(vote.EventCode, source.EventCode)

    def test_vote_legislation_links_back_to_the_event(self):
        vote = self.client.get_vote(self.HOUSE_FLOOR)

        self.assertIn(98525, [v.LegislationID for v in vote.vote_legislation])

    def test_an_out_of_range_vote_id_returns_none(self):
        """LIS answers 204 No Content, which the client turns into None."""
        self.assertIsNone(self.client.get_vote(999999999))

    def test_votes_reach_back_to_1994(self):
        """Votes predate the bill data by three decades, so do not assume 2024."""
        vote = self.client.get_vote(1)

        self.assertEqual(vote.SessionCode, "19941")
        self.assertEqual(vote.VoteDate.year, 1994)

    def test_statement_vote_member_id_is_really_a_member_id(self):
        """The field is misnamed: it joins on MemberID, never on VoteMemberID."""
        vote = self.client.get_vote(self.HOUSE_FLOOR)

        statement_ids = {s.VoteMemberID for s in vote.VoteStatements}
        self.assertEqual(statement_ids, {17, 217})
        self.assertEqual(statement_ids & {m.VoteMemberID for m in vote.vote_members}, set())
        self.assertEqual(statement_ids, statement_ids & {m.MemberID for m in vote.vote_members})

    def test_response_code_x_means_not_voting(self):
        """A statement names an X member as 'recorded as not voting'."""
        vote = self.client.get_vote(self.HOUSE_FLOOR)

        by_member = {m.MemberID: m for m in vote.vote_members}
        knight = by_member[217]
        self.assertEqual(knight.ResponseCode, "X")
        self.assertIn(
            "not voting",
            next(s.VoteStatement for s in vote.VoteStatements if s.VoteMemberID == 217),
        )

    def test_no_padded_strings_survive_on_a_ballot(self):
        vote = self.client.get_vote(self.HOUSE_FLOOR)

        padded = [
            (b.MemberNumber, field)
            for b in vote.vote_members
            for field, value in b.model_dump().items()
            if isinstance(value, str) and value != value.strip()
        ]
        self.assertEqual(padded, [])

    def test_vote_types_reference(self):
        types = self.client.get_vote_types()

        self.assertEqual(
            {t.VoteTypeID: t.Name for t in types},
            {1: "Committee", 2: "Subcommittee", 3: "Floor"},
        )


@_skip_live
class LiveEventTypeJoinTest(unittest.TestCase):
    """EventCode is not unique, so the vocabulary needs a three-part key.

    3,912 rows carry 1,502 distinct codes.  ``H1405`` alone returns four rows,
    two of which mean the opposite of the other two.
    """

    def setUp(self):
        self.types = LISClient().get_event_types()

    def test_event_code_alone_does_not_identify_a_row(self):
        codes = [t.EventCode for t in self.types]

        self.assertGreater(len(codes), len(set(codes)))

    def test_code_chamber_and_outcome_identify_exactly_one_row(self):
        keys = [(t.EventCode, t.LegislationChamberCode, t.IsPassed) for t in self.types]

        self.assertEqual(len(keys), len(set(keys)))

    def test_one_code_carries_opposite_outcomes(self):
        rows = [t for t in self.types if t.EventCode == "H1405"]

        descriptions = {t.LegislationDescription for t in rows}
        self.assertIn("Reported from Labor and Commerce", descriptions)
        self.assertIn("Failed to report (defeated) in Labor and Commerce", descriptions)


@_skip_live
class LiveMemberTest(unittest.TestCase):
    """The roster that gives a roll call party and district.

    Anchored on the completed 2026 Regular Session, so the counts are final.
    """

    SESSION = 20261

    def setUp(self):
        self.client = LISClient()

    def test_roster_carries_party_and_district(self):
        members = self.client.get_members(self.SESSION)

        anderson = next(m for m in members if m.MemberNumber == "H0386")
        self.assertEqual(anderson.PartyCode, "D")
        self.assertEqual(anderson.DistrictName, "71st")
        self.assertEqual(anderson.name, "Jessica L. Anderson")

    def test_roster_holds_more_rows_than_seats(self):
        """Members who left or arrived mid-session stay in the list."""
        members = self.client.get_members(self.SESSION)

        by_chamber = Counter(m.ChamberCode for m in members)
        self.assertGreater(by_chamber["H"], 100)
        self.assertGreater(by_chamber["S"], 40)
        self.assertGreater(len([m for m in members if m.ServiceEndDate]), 0)

    def test_chamber_code_filters(self):
        house = self.client.get_members(self.SESSION, chamber_code="H")
        senate = self.client.get_members(self.SESSION, chamber_code="S")
        both = self.client.get_members(self.SESSION)

        self.assertEqual({m.ChamberCode for m in house}, {"H"})
        self.assertEqual({m.ChamberCode for m in senate}, {"S"})
        self.assertEqual(len(house) + len(senate), len(both))

    def test_the_roster_status_id_holds_the_previous_status(self):
        """Every row whose ID contradicts its name is a departed member."""
        vocab = {1: "Active", 2: "Inactive", 3: "Outgoing"}
        members = self.client.get_members(self.SESSION)

        contradicting = [
            m
            for m in members
            if m.MemberStatusID is not None and vocab[m.MemberStatusID] != m.MemberStatus
        ]
        self.assertTrue(contradicting, "Expected the stale-ID rows to still exist")
        for m in contradicting:
            self.assertEqual(m.MemberStatusID, 1, f"{m.name} should hold the Active code")
            self.assertIsNotNone(m.ServiceEndDate, f"{m.name} should have left")
            self.assertIn(m.MemberStatus, ("Inactive", "Outgoing"))

    def test_the_by_id_status_id_agrees_with_its_own_name(self):
        vocab = {1: "Active", 2: "Inactive", 3: "Outgoing"}

        for member_id in (217, 527):
            one = self.client.get_member(member_id, self.SESSION)
            self.assertEqual(vocab[one.MemberStatusID], one.MemberStatus)

    def test_the_roster_omits_the_status_id_on_many_rows(self):
        members = self.client.get_members(self.SESSION)

        self.assertGreater(len([m for m in members if m.MemberStatusID is None]), 0)

    def test_the_list_nulls_fields_the_by_id_call_fills(self):
        members = self.client.get_members(self.SESSION)

        for field in ("SessionID", "ChamberName", "SeatNumber", "VotingSequence"):
            self.assertTrue(
                all(getattr(m, field) is None for m in members),
                f"Expected {field} null on every list row",
            )

    def test_get_member_is_not_reliable_enough_to_build_on(self):
        """It answers 204 for some sitting members, so the roster is the source."""
        members = self.client.get_members(self.SESSION)

        missing = [
            m for m in members[:40] if self.client.get_member(m.MemberID, self.SESSION) is None
        ]
        self.assertGreater(len(missing), 0)

    def test_get_member_adds_what_the_list_omits(self):
        one = self.client.get_member(217, self.SESSION)

        self.assertEqual(one.MemberNumber, "H0206")
        self.assertEqual(one.ChamberName, "House")
        self.assertIsNotNone(one.SeatNumber)
        self.assertIsNotNone(one.MemberDetailID)

    def test_session_code_is_required(self):
        with self.assertRaises(requests.HTTPError):
            self.client.get_members(None)

    def test_the_roster_changes_between_sessions(self):
        now = {m.MemberID for m in self.client.get_members(20261)}
        then = {m.MemberID for m in self.client.get_members(20241)}

        self.assertGreater(len(now - then), 0)
        self.assertGreater(len(then - now), 0)

    def test_no_padded_strings_survive_validation(self):
        """LIS pads ~50 email addresses per roster; LISModel strips them all."""
        for code in (20241, 20251, 20261, 20271):
            padded = [
                (m.MemberNumber, field)
                for m in self.client.get_members(code)
                for field, value in m.model_dump().items()
                if isinstance(value, str) and value != value.strip()
            ]
            self.assertEqual(padded, [], f"padded strings survived in {code}")

    def test_party_reference(self):
        parties = self.client.get_parties()

        self.assertEqual(
            {p.PartyCode: p.Name for p in parties},
            {"D": "Democrat", "I": "Independent", "R": "Republican"},
        )

    def test_district_reference_covers_every_seat(self):
        districts = self.client.get_districts()

        by_chamber = Counter(d.ChamberCode for d in districts)
        self.assertEqual(by_chamber["H"], 100)
        self.assertEqual(by_chamber["S"], 40)


@_skip_live
class LiveRollCallJoinTest(unittest.TestCase):
    """Every ballot on a roll call must resolve against the session roster."""

    def setUp(self):
        self.client = LISClient()

    def test_every_ballot_resolves_to_a_member(self):
        vote = self.client.get_vote(294006)
        roster = {m.MemberID: m for m in self.client.get_members(20261)}

        unresolved = [b for b in vote.vote_members if b.MemberID not in roster]
        self.assertEqual(unresolved, [])
        self.assertEqual(len(vote.vote_members), 100)

    def test_a_departed_member_still_resolves(self):
        """Barry Knight voted on 2026-02-03 and left service on 2026-02-17."""
        roster = {m.MemberID: m for m in self.client.get_members(20261)}

        knight = roster[217]
        self.assertIsNotNone(knight.ServiceEndDate)
        self.assertFalse(knight.is_serving)
        self.assertEqual(knight.PartyCode, "R")


@_skip_live
class LiveMemberVoteSearchTest(unittest.TestCase):
    """The member-first axis, anchored on Delegate Anthony in 2026."""

    MEMBER = 503  # Bonita G. Anthony, H0386
    SESSION = 20261

    def setUp(self):
        self.client = LISClient()

    def test_a_row_is_a_vote_bill_pair_not_a_vote(self):
        rows = self.client.get_member_votes(self.MEMBER, self.SESSION)

        self.assertGreater(len(rows), len({r.VoteID for r in rows}))

    def test_a_block_vote_repeats_once_per_bill(self):
        rows = self.client.get_member_votes(self.MEMBER, self.SESSION)

        counts = Counter(r.VoteID for r in rows)
        vote_id, fan_out = counts.most_common(1)[0]
        self.assertGreater(fan_out, 1)

        # The derived count must match what the bill-first record reports.
        vote = self.client.get_vote(vote_id)
        self.assertEqual(fan_out, len(vote.vote_legislation))
        self.assertTrue(vote.IsBlock)

    def test_classification_name_is_not_the_legislation_filter(self):
        """Null classification still means a bill; only attendance has none."""
        rows = self.client.get_member_votes(self.MEMBER, self.SESSION)

        null_class = [r for r in rows if r.ClassificationName is None]
        attendance = [r for r in rows if r.ClassificationName == "Attendance"]

        self.assertGreater(len(null_class), 0)
        self.assertTrue(all(r.LegislationNumber for r in null_class))
        self.assertTrue(all(r.LegislationNumber is None for r in attendance))

    def test_null_classification_rows_are_committee_votes(self):
        rows = self.client.get_member_votes(self.MEMBER, self.SESSION)

        null_class = [r for r in rows if r.ClassificationName is None]
        self.assertEqual({r.VoteType for r in null_class}, {"Committee", "Subcommittee"})

    def test_both_parameters_are_required(self):
        with self.assertRaises(requests.HTTPError):
            self.client.get_member_votes(self.MEMBER, None)

    def test_the_two_axes_agree_on_a_bill(self):
        """Member-first and bill-first return the same VoteIDs and responses."""
        rows = self.client.get_member_votes(self.MEMBER, self.SESSION)
        mine = {(r.VoteID, r.ResponseCode) for r in rows if r.LegislationNumber == "HB1"}

        vote_ids = (291609, 294006)  # HB1's two House votes
        theirs = set()
        for vote_id in vote_ids:
            vote = self.client.get_vote(vote_id)
            ballot = next(b for b in vote.vote_members if b.MemberID == self.MEMBER)
            theirs.add((vote_id, ballot.ResponseCode))

        self.assertEqual(mine, theirs)

    def test_statements_use_the_same_misnamed_member_id(self):
        rows = self.client.get_member_votes(self.MEMBER, self.SESSION)

        with_statements = [r for r in rows if r.VoteStatements]
        self.assertGreater(len(with_statements), 0)
        for r in with_statements:
            for s in r.VoteStatements:
                self.assertEqual(s.VoteMemberID, self.MEMBER)

    def test_the_scalar_statement_field_is_always_null(self):
        rows = self.client.get_member_votes(self.MEMBER, self.SESSION)

        self.assertTrue(all(r.vote_statement is None for r in rows))


@_skip_live
class LiveMemberVoteServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = LISService()

    def test_member_votes_drops_only_the_attendance_rows(self):
        every = self.service.member_votes(503, 20261, legislation_only=False)
        bills = self.service.member_votes(503, 20261)

        dropped = len(every) - len(bills)
        self.assertGreater(dropped, 0)
        self.assertTrue(all(v.bill_number for v in bills))

    def test_member_votes_on_agrees_with_roll_call(self):
        mine = {
            (v.result.VoteID, v.response)
            for v in self.service.member_votes_on(503, "hb0001", 20261)
        }
        theirs = {
            (e.vote.VoteID, e.response)
            for e in self.service.roll_call("HB1", 20261)
            if e.member.MemberID == 503
        }

        self.assertEqual(mine, theirs)
        self.assertGreater(len(mine), 0)


class ClientConfigTest(unittest.TestCase):
    """Unit tests for client configuration (no network calls)."""

    def test_missing_api_key_raises(self):
        with self.assertRaises(LISClientError):
            LISClient(api_key="")

    def test_explicit_api_key(self):
        client = LISClient(api_key="test-key-123")
        self.assertEqual(client.api_key, "test-key-123")
        self.assertEqual(client._headers(), {"WebAPIKey": "test-key-123"})
