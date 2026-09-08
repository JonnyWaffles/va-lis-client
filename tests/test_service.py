"""Unit tests for the service layer.

These never touch the network.  ``FakeClient`` stands in for
:class:`LISClient` and counts calls, so the caching tests can prove that a
second call does not refetch.

Run with:
    pytest tests/test_service.py
"""

import unittest

from va_lis_client.exceptions import (
    BillNotFoundError,
    InvalidBillNumberError,
    LISError,
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
    Vote,
)
from va_lis_client.service import (
    BillVote,
    LISService,
    normalize_bill_number,
    pick_text_version,
    strip_html,
)


def bill_row(legislation_id=98525, number="HB1", status="In Committee"):
    return LegislationSummaryItem(
        LegislationID=legislation_id,
        LegislationNumber=number,
        Description="Minimum wage; increases incrementally.",
        ChamberCode="H",
        LegislationTypeCode="B",
        LegislationStatus=status,
    )


def bill_detail(status=None, status_id=None):
    return Legislation(
        LegislationID=98525,
        LegislationNumber="HB1",
        Description="Minimum wage; increases incrementally.",
        ChamberCode="H",
        LegislationTypeCode="B",
        LegislationStatus=status,
        LegislationStatusID=status_id,
    )


def text_item(text_id, document_code):
    return LegislationTextItem(
        LegislationID=98525,
        LegislationNumber="HB1",
        SessionID=59,
        LegislationTextID=text_id,
        LegislationVersionID=1,
        DocumentCode=document_code,
        Version=document_code,
    )


def text_detail(text_id, document_code, draft_text="<p>An Act to amend.</p>"):
    return LegislationTextDetail(
        LegislationTextID=text_id,
        LegislationVersionID=1,
        LegislationID=98525,
        LegislationNumber="HB1",
        DocumentCode=document_code,
        DraftText=draft_text,
    )


def event(event_id=1, code="H5000", vote_id=None, number="HB1", chamber="H", is_passed=True):
    return LegislationEvent(
        LegislationEventID=event_id,
        EventCode=code,
        LegislationID=98525,
        LegislationNumber=number,
        ChamberCode=chamber,
        IsPassed=is_passed,
        VoteID=vote_id,
    )


def vote(
    vote_id=294006,
    responses=("Y", "Y", "N"),
    is_voice=False,
    is_block=False,
    bills=1,
    committee_id=None,
    tally="(2-Y 1-N 0-A)",
    statements=(),
):
    return Vote(
        VoteID=vote_id,
        ChamberCode="H",
        VoteTypeID=1 if committee_id else 3,
        VoteType="Committee" if committee_id else "Floor",
        CommitteeID=committee_id,
        VoteTally=tally,
        IsVoice=is_voice,
        IsBlock=is_block,
        # Mirror the real shape: the ballot ID looks nothing like the person
        # ID, so a test cannot pass by confusing the two.
        VoteMember=[
            {"VoteMemberID": 10988427 + i, "MemberID": i, "ResponseCode": code}
            for i, code in enumerate(responses, start=1)
        ],
        VoteLegislation=[
            {"VoteLegislationID": n, "LegislationID": 98525 + n, "LegislationNumber": f"HB{n}"}
            for n in range(1, bills + 1)
        ],
        # VoteMemberID on a statement really holds a MemberID.
        VoteStatements=[
            {"VoteStatementID": i, "VoteMemberID": member_id, "VoteStatement": text}
            for i, (member_id, text) in enumerate(statements, start=1)
        ],
    )


def service_with_types():
    return LISService(FakeClient())


class FakeClient:
    """A stand-in for LISClient that serves fixtures and counts calls."""

    def __init__(self, bills=None, detail=None, texts=None, details=None, events=None, votes=None):
        self.events = events if events is not None else []
        self.votes = votes if votes is not None else {}
        self.bills = bills if bills is not None else [bill_row()]
        self.detail = detail if detail is not None else bill_detail()
        self.texts = (
            texts
            if texts is not None
            else [
                text_item(257719, "HB1"),
                text_item(266240, "HB1ER"),
            ]
        )
        self.details = (
            details
            if details is not None
            else [
                text_detail(257719, "HB1"),
                text_detail(266240, "HB1ER"),
            ]
        )
        self.calls = {}

    def _count(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1

    def get_session_bills(self, session_code=None, session_id=None):
        self._count("get_session_bills")
        return list(self.bills)

    def get_bill(self, legislation_id):
        self._count("get_bill")
        return self.detail

    def get_bill_texts(self, *, legislation_number=None, session_code=None, **kwargs):
        self._count("get_bill_texts")
        self.last_legislation_number = legislation_number
        return list(self.texts)

    def get_bill_text_detail(self, *, legislation_id, session_code):
        self._count("get_bill_text_detail")
        return list(self.details)

    def get_event_types(self):
        self._count("get_event_types")
        return [
            LegislationEventType(
                EventCode="H5000", LegislationDescription="Passed House", IsPassage=True
            ),
            LegislationEventType(EventCode="H1401", LegislationDescription="Referred"),
            LegislationEventType(EventCode=None, LegislationDescription="Unusable row"),
            # The real H1405 family.  LIS repeats 1,326 codes like this, and
            # two of these four rows mean the opposite of the other two.
            LegislationEventType(
                EventCode="H1405",
                LegislationChamberCode="H",
                IsPassed=True,
                LegislationDescription="Reported from Labor and Commerce",
            ),
            LegislationEventType(
                EventCode="H1405",
                LegislationChamberCode="S",
                IsPassed=True,
                LegislationDescription="Reported from Labor and Commerce",
            ),
            LegislationEventType(
                EventCode="H1405",
                LegislationChamberCode="H",
                IsPassed=False,
                LegislationDescription="Failed to report (defeated) in Labor and Commerce",
            ),
            LegislationEventType(
                EventCode="H1405",
                LegislationChamberCode="S",
                IsPassed=False,
                LegislationDescription="Failed to report (defeated) in Labor and Commerce",
            ),
        ]

    def get_bill_events(self, legislation_id):
        self._count("get_bill_events")
        return list(self.events)

    def get_vote(self, vote_id):
        self._count("get_vote")
        return self.votes.get(vote_id)

    def get_legislation_statuses(self):
        self._count("get_legislation_statuses")
        return [
            LegislationStatus(LegislationStatusID=1, Name="Introduced", DisplayName="Introduced"),
            LegislationStatus(
                LegislationStatusID=38,
                Name="AwaitingGovernor",
                DisplayName="Awaiting Governor's Action",
            ),
        ]


class NormalizeBillNumberTest(unittest.TestCase):
    def test_unpads_and_uppercases(self):
        self.assertEqual(normalize_bill_number("hb0001"), "HB1")

    def test_strips_surrounding_and_inner_spaces(self):
        self.assertEqual(normalize_bill_number("  hj 5 "), "HJ5")

    def test_leaves_an_already_normal_number_alone(self):
        self.assertEqual(normalize_bill_number("SB234"), "SB234")

    def test_rejects_a_string_that_is_not_a_bill_number(self):
        for bad in ["", "HB", "123", "HB1A", "H-B1"]:
            with self.subTest(value=bad):
                with self.assertRaises(InvalidBillNumberError):
                    normalize_bill_number(bad)

    def test_error_is_also_a_value_error(self):
        with self.assertRaises(ValueError):
            normalize_bill_number("nonsense")


class StripHtmlTest(unittest.TestCase):
    def test_drops_tags_and_keeps_words(self):
        self.assertEqual(strip_html("<p>Hello <b>world</b></p>"), "Hello world")

    def test_drops_script_and_style_bodies(self):
        result = strip_html("<p>A</p><script>var x = 1;</script><style>p{}</style><p>B</p>")
        self.assertIn("A", result)
        self.assertIn("B", result)
        self.assertNotIn("var", result)
        self.assertNotIn("{}", result)

    def test_flattens_amendment_markup(self):
        markup = '<p>The <em class="new">new</em> and <s>old</s> words.</p>'
        self.assertEqual(strip_html(markup), "The new and old words.")

    def test_collapses_runs_of_whitespace(self):
        self.assertEqual(strip_html("<p>a     b</p>"), "a b")

    def test_returns_empty_string_for_markup_with_no_text(self):
        self.assertEqual(strip_html("<div><br/></div>"), "")


class PickTextVersionTest(unittest.TestCase):
    def setUp(self):
        self.texts = [text_item(257719, "HB1"), text_item(266240, "HB1ER")]

    def test_defaults_to_the_highest_text_id(self):
        self.assertEqual(pick_text_version(self.texts).DocumentCode, "HB1ER")

    def test_selects_by_document_code_ignoring_case(self):
        self.assertEqual(pick_text_version(self.texts, "hb1").LegislationTextID, 257719)

    def test_unknown_document_code_lists_what_is_available(self):
        with self.assertRaises(TextVersionNotFoundError) as ctx:
            pick_text_version(self.texts, "HB1S1")

        self.assertIn("HB1ER", str(ctx.exception))


class SessionBillsCacheTest(unittest.TestCase):
    def test_second_call_uses_the_cache(self):
        client = FakeClient()
        service = LISService(client)

        service.session_bills(20261)
        service.session_bills(20261)

        self.assertEqual(client.calls["get_session_bills"], 1)

    def test_refresh_forces_a_refetch(self):
        client = FakeClient()
        service = LISService(client)

        service.session_bills(20261)
        service.session_bills(20261, refresh=True)

        self.assertEqual(client.calls["get_session_bills"], 2)

    def test_an_expired_entry_is_refetched(self):
        client = FakeClient()
        service = LISService(client, bill_list_ttl=0)

        service.session_bills(20261)
        service.session_bills(20261)

        self.assertEqual(client.calls["get_session_bills"], 2)

    def test_each_session_is_cached_separately(self):
        client = FakeClient()
        service = LISService(client)

        service.session_bills(20261)
        service.session_bills(20271)

        self.assertEqual(client.calls["get_session_bills"], 2)

    def test_clear_cache_forces_a_refetch(self):
        client = FakeClient()
        service = LISService(client)

        service.session_bills(20261)
        service.clear_cache()
        service.session_bills(20261)

        self.assertEqual(client.calls["get_session_bills"], 2)

    def test_two_services_do_not_share_a_cache(self):
        first, second = FakeClient(), FakeClient()

        LISService(first).session_bills(20261)
        LISService(second).session_bills(20261)

        self.assertEqual(first.calls["get_session_bills"], 1)
        self.assertEqual(second.calls["get_session_bills"], 1)


class ResolveBillTest(unittest.TestCase):
    def test_resolves_a_padded_lowercase_number(self):
        service = LISService(FakeClient())

        self.assertEqual(service.resolve_bill("hb0001", 20261).LegislationID, 98525)

    def test_missing_bill_raises(self):
        service = LISService(FakeClient())

        with self.assertRaises(BillNotFoundError):
            service.resolve_bill("HB999", 20261)

    def test_missing_bill_error_is_also_a_lookup_error(self):
        service = LISService(FakeClient())

        with self.assertRaises(LookupError):
            service.resolve_bill("HB999", 20261)

    def test_every_error_shares_one_base(self):
        service = LISService(FakeClient())

        with self.assertRaises(LISError):
            service.resolve_bill("HB999", 20261)


class ResolveBillIdTest(unittest.TestCase):
    """The cheap path: the text list takes a number and hands back the ID."""

    def test_uses_the_text_list_and_skips_the_session_list(self):
        client = FakeClient()
        service = LISService(client)

        self.assertEqual(service.resolve_bill_id("HB1", 20261), 98525)
        self.assertEqual(client.calls.get("get_bill_texts"), 1)
        self.assertNotIn("get_session_bills", client.calls)

    def test_normalizes_before_the_text_lookup(self):
        client = FakeClient()

        LISService(client).resolve_bill_id("hb0001", 20261)

        self.assertEqual(client.last_legislation_number, "HB1")

    def test_falls_back_to_the_session_list_without_text(self):
        client = FakeClient(texts=[])
        service = LISService(client)

        self.assertEqual(service.resolve_bill_id("HB1", 20261), 98525)
        self.assertEqual(client.calls.get("get_session_bills"), 1)

    def test_raises_when_neither_source_has_the_bill(self):
        service = LISService(FakeClient(texts=[], bills=[]))

        with self.assertRaises(BillNotFoundError):
            service.resolve_bill_id("HB999", 20261)

    def test_rejects_a_bad_number_before_any_call(self):
        client = FakeClient()

        with self.assertRaises(InvalidBillNumberError):
            LISService(client).resolve_bill_id("nonsense", 20261)

        self.assertEqual(client.calls, {})


class GetBillTest(unittest.TestCase):
    def test_returns_the_detail_record(self):
        service = LISService(FakeClient())

        self.assertEqual(service.get_bill("HB1", 20261).LegislationNumber, "HB1")

    def test_absent_detail_record_raises(self):
        client = FakeClient()
        client.detail = None
        service = LISService(client)

        with self.assertRaises(BillNotFoundError):
            service.get_bill("HB1", 20261)


class BillTextTest(unittest.TestCase):
    def test_defaults_to_the_newest_version(self):
        service = LISService(FakeClient())

        item, detail = service.bill_text("HB1", 20261)

        self.assertEqual(item.DocumentCode, "HB1ER")
        self.assertEqual(detail.LegislationTextID, 266240)

    def test_selects_a_named_version(self):
        service = LISService(FakeClient())

        item, detail = service.bill_text("HB1", 20261, "HB1")

        self.assertEqual(detail.LegislationTextID, 257719)

    def test_normalizes_the_number_before_querying(self):
        client = FakeClient()

        LISService(client).bill_text("hb0001", 20261)

        self.assertEqual(client.last_legislation_number, "HB1")

    def test_supplied_versions_avoid_a_second_fetch(self):
        client = FakeClient()
        service = LISService(client)
        versions = service.bill_text_versions("HB1", 20261)

        service.bill_text("HB1", 20261, versions=versions)

        self.assertEqual(client.calls["get_bill_texts"], 1)

    def test_no_versions_raises(self):
        service = LISService(FakeClient(texts=[]))

        with self.assertRaises(TextVersionNotFoundError):
            service.bill_text("HB1", 20261)

    def test_missing_body_raises(self):
        client = FakeClient(details=[text_detail(266240, "HB1ER", draft_text=None)])
        service = LISService(client)

        with self.assertRaises(TextVersionNotFoundError):
            service.bill_text("HB1", 20261)

    def test_unmatched_detail_row_raises(self):
        client = FakeClient(details=[text_detail(999999, "HB1XX")])
        service = LISService(client)

        with self.assertRaises(TextVersionNotFoundError):
            service.bill_text("HB1", 20261)


class ReferenceJoinTest(unittest.TestCase):
    def test_event_types_group_every_row_under_its_code(self):
        service = LISService(FakeClient())

        types = service.event_types_by_code()

        self.assertTrue(types["H5000"][0].IsPassage)
        # A code is not unique, so nothing may be dropped.
        self.assertEqual(len(types["H1405"]), 4)

    def test_event_types_drop_rows_with_no_event_code(self):
        service = LISService(FakeClient())

        self.assertEqual(len(service.event_types_by_code()), 3)

    def test_event_type_for_picks_the_row_matching_the_outcome(self):
        service = LISService(FakeClient())

        passed = service.event_type_for(event(code="H1405", is_passed=True))
        failed = service.event_type_for(event(code="H1405", is_passed=False))

        self.assertEqual(passed.LegislationDescription, "Reported from Labor and Commerce")
        self.assertEqual(
            failed.LegislationDescription,
            "Failed to report (defeated) in Labor and Commerce",
        )

    def test_event_type_for_reads_the_chamber_off_the_bill_not_the_actor(self):
        # A Senate bill reported from a House committee: actor H, bill S.
        senate_bill = event(code="H1405", number="SB1", chamber="H")

        picked = service_with_types().event_type_for(senate_bill)

        self.assertEqual(picked.LegislationChamberCode, "S")

    def test_event_type_for_keeps_the_outcome_when_the_chamber_is_unknown(self):
        orphan = event(code="H1405", number="", chamber=None, is_passed=False)

        picked = service_with_types().event_type_for(orphan)

        self.assertIs(picked.IsPassed, False)

    def test_event_type_for_returns_none_for_an_unknown_code(self):
        self.assertIsNone(service_with_types().event_type_for(event(code="Z9999")))

    def test_event_types_are_fetched_once(self):
        client = FakeClient()
        service = LISService(client)

        service.event_types_by_code()
        service.event_types_by_code()

        self.assertEqual(client.calls["get_event_types"], 1)

    def test_both_status_indexes_come_from_one_fetch(self):
        client = FakeClient()
        service = LISService(client)

        by_name = service.statuses_by_name()
        by_id = service.statuses_by_id()

        self.assertEqual(client.calls["get_legislation_statuses"], 1)
        self.assertEqual(by_name["AwaitingGovernor"].LegislationStatusID, 38)
        self.assertEqual(by_id[38].Name, "AwaitingGovernor")


class BillStatusLabelTest(unittest.TestCase):
    def setUp(self):
        self.service = LISService(FakeClient())

    def test_prefers_the_label_on_the_detail_record(self):
        label = self.service.bill_status_label(bill_detail(status="Passed House"))

        self.assertEqual(label, "Passed House")

    def test_falls_back_to_the_status_reference(self):
        label = self.service.bill_status_label(bill_detail(status_id=38))

        self.assertEqual(label, "Awaiting Governor's Action")

    def test_falls_back_to_the_session_list_row(self):
        label = self.service.bill_status_label(bill_detail(), bill_row(status="In Committee"))

        self.assertEqual(label, "In Committee")

    def test_returns_none_when_nothing_is_available(self):
        self.assertIsNone(self.service.bill_status_label(bill_detail()))

    def test_unknown_status_id_falls_through_to_the_list_row(self):
        label = self.service.bill_status_label(
            bill_detail(status_id=9999), bill_row(status="In Committee")
        )

        self.assertEqual(label, "In Committee")


class BillVoteTest(unittest.TestCase):
    def test_walks_events_and_fetches_each_vote(self):
        client = FakeClient(
            events=[
                event(1, "H1405", vote_id=291609),
                event(2, "H4110"),  # no VoteID, so no request
                event(3, "H5000", vote_id=294006),
            ],
            votes={
                291609: vote(291609, committee_id=14),
                294006: vote(294006),
            },
        )

        votes = LISService(client).bill_votes("HB1", 20261)

        self.assertEqual([v.vote.VoteID for v in votes], [291609, 294006])
        self.assertEqual(client.calls["get_vote"], 2)

    def test_keeps_events_in_chronological_order(self):
        client = FakeClient(
            events=[event(1, "H1405", vote_id=1), event(2, "S5100", vote_id=2)],
            votes={1: vote(1), 2: vote(2)},
        )

        votes = LISService(client).bill_votes("HB1", 20261)

        self.assertEqual([v.event.EventCode for v in votes], ["H1405", "S5100"])

    def test_skips_a_vote_id_the_api_cannot_resolve(self):
        client = FakeClient(events=[event(1, "H5000", vote_id=999)], votes={})

        self.assertEqual(LISService(client).bill_votes("HB1", 20261), [])

    def test_a_voice_vote_is_not_a_roll_call(self):
        voice = vote(
            300174, responses=(), is_voice=True, is_block=True, bills=50, tally="(Voice Vote)"
        )

        record = BillVote(event=event(1, "S4160", vote_id=300174), vote=voice)

        self.assertFalse(record.is_roll_call)

    def test_a_block_vote_is_not_a_roll_call_for_one_bill(self):
        block = vote(300173, responses=("Y",) * 40, is_block=True, bills=50)

        record = BillVote(event=event(1, "S4145", vote_id=300173), vote=block)

        self.assertFalse(record.is_roll_call)
        self.assertEqual(record.bill_count, 50)

    def test_a_plain_floor_vote_is_a_roll_call(self):
        record = BillVote(event=event(1, "H5000", vote_id=294006), vote=vote())

        self.assertTrue(record.is_roll_call)
        self.assertFalse(record.is_committee)

    def test_roll_calls_only_drops_voice_and_block_votes(self):
        client = FakeClient(
            events=[
                event(1, "H5000", vote_id=1),
                event(2, "S4160", vote_id=2),
                event(3, "S4145", vote_id=3),
            ],
            votes={
                1: vote(1),
                2: vote(2, responses=(), is_voice=True, is_block=True, bills=50),
                3: vote(3, responses=("Y",) * 40, is_block=True, bills=50),
            },
        )

        votes = LISService(client).bill_votes("HB1", 20261, roll_calls_only=True)

        self.assertEqual([v.vote.VoteID for v in votes], [1])

    def test_responses_group_by_code_including_x(self):
        # "X" is absent from the tally string, so the groups must not be
        # derived from it.
        record = BillVote(
            event=event(1, "H5000", vote_id=294006),
            vote=vote(responses=("Y", "Y", "N", "X"), tally="(2-Y 1-N 0-A)"),
        )

        grouped = record.responses()

        self.assertEqual({k: len(v) for k, v in grouped.items()}, {"Y": 2, "N": 1, "X": 1})

    def test_member_name_strips_the_lis_leading_space(self):
        record = BillVote(event=event(1, "H5000", vote_id=1), vote=vote())

        member = record.vote.vote_members[0]
        member.MemberDisplayName = " Jessica L. Anderson"

        self.assertEqual(member.name, "Jessica L. Anderson")

    def test_statements_key_on_member_id_not_vote_member_id(self):
        """VoteStatement.VoteMemberID is misnamed and holds a MemberID.

        Guards against anyone "simplifying" the join back to the two columns
        that share a name.  That join matches nothing and raises nothing.
        """
        # MemberID 2 is the second member; their VoteMemberID is 10988429.
        record = BillVote(
            event=event(1, "H5000", vote_id=294006),
            vote=vote(
                294006,
                responses=("Y", "X"),
                statements=[(2, "Delegate Knight was recorded as not voting.")],
            ),
        )

        keyed = record.statements_by_member()

        knight = record.vote.vote_members[1]
        self.assertEqual(knight.MemberID, 2)
        self.assertEqual(knight.VoteMemberID, 10988429)
        self.assertEqual(list(keyed), [knight.MemberID])
        self.assertNotIn(knight.VoteMemberID, keyed)

    def test_the_naive_statement_join_finds_nothing(self):
        """Documents the failure mode rather than only the fix."""
        record = BillVote(
            event=event(1, "H5000", vote_id=294006),
            vote=vote(294006, responses=("Y", "X"), statements=[(2, "...")]),
        )

        ballot_ids = {m.VoteMemberID for m in record.vote.vote_members}
        statement_ids = {s.VoteMemberID for s in record.vote.VoteStatements}

        self.assertEqual(statement_ids & ballot_ids, set())

    def test_statements_are_empty_when_none_were_filed(self):
        record = BillVote(event=event(1, "H5000", vote_id=1), vote=vote())

        self.assertEqual(record.statements_by_member(), {})

    def test_committee_votes_report_their_committee(self):
        record = BillVote(
            event=event(1, "H1405", vote_id=291609), vote=vote(291609, committee_id=14)
        )

        self.assertTrue(record.is_committee)


if __name__ == "__main__":
    unittest.main()
