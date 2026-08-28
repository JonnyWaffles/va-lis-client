"""Unit tests for the X-Pagination protocol support.

These never touch the network.  The client tests replace ``_get`` so they can
assert on the request header that gets built and on the response header that
gets parsed.

Run with:
    pytest tests/test_pagination.py
"""

import json
import unittest

from va_lis_client import LISClient
from va_lis_client.models import LegislationSummaryItem, PagedList, Pagination
from va_lis_client.models.pagination import page_request_header

LIVE_HEADER = (
    '{"PageSize":3,"PageNumber":1,"TotalPages":148,"TotalCount":443,'
    '"CurrentPage":3,"SkippedRecords":6,"HasPrevious":true,"HasNext":true}'
)


def bill_json(number):
    return {
        "LegislationID": 98631,
        "LegislationNumber": number,
        "Description": "A bill.",
        "ChamberCode": "H",
        "LegislationTypeCode": "B",
    }


class PaginationParseTest(unittest.TestCase):
    def test_parses_a_real_header(self):
        page = Pagination.from_header(LIVE_HEADER)

        self.assertEqual(page.TotalCount, 443)
        self.assertEqual(page.SkippedRecords, 6)
        self.assertEqual(page.CurrentPage, 3)
        self.assertTrue(page.HasNext)
        self.assertTrue(page.HasPrevious)

    def test_missing_header_gives_none(self):
        self.assertIsNone(Pagination.from_header(None))

    def test_empty_header_gives_none(self):
        self.assertIsNone(Pagination.from_header(""))

    def test_malformed_header_gives_none(self):
        self.assertIsNone(Pagination.from_header("not json"))

    def test_wrong_types_give_none(self):
        self.assertIsNone(Pagination.from_header('{"TotalCount": "many"}'))

    def test_unknown_fields_are_tolerated(self):
        page = Pagination.from_header('{"TotalCount": 7, "SomethingNew": 1}')

        self.assertEqual(page.TotalCount, 7)


class PageRequestHeaderTest(unittest.TestCase):
    def test_no_arguments_gives_no_header(self):
        self.assertIsNone(page_request_header())

    def test_page_size_only(self):
        header = page_request_header(page_size=25)

        self.assertEqual(json.loads(header["X-Pagination"]), {"PageSize": 25})

    def test_skip_only(self):
        header = page_request_header(skip=10)

        self.assertEqual(json.loads(header["X-Pagination"]), {"SkippedRecords": 10})

    def test_both_fields(self):
        header = page_request_header(page_size=5, skip=10)

        self.assertEqual(
            json.loads(header["X-Pagination"]),
            {"PageSize": 5, "SkippedRecords": 10},
        )

    def test_a_zero_skip_is_sent_rather_than_dropped(self):
        header = page_request_header(page_size=5, skip=0)

        self.assertEqual(json.loads(header["X-Pagination"])["SkippedRecords"], 0)


class PagedListTest(unittest.TestCase):
    def test_it_really_is_a_list(self):
        rows = PagedList([1, 2, 3], Pagination(TotalCount=3))

        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1], 2)
        self.assertEqual([r for r in rows], [1, 2, 3])

    def test_it_carries_the_metadata(self):
        rows = PagedList([1], Pagination.from_header(LIVE_HEADER))

        self.assertEqual(rows.pagination.TotalCount, 443)

    def test_metadata_defaults_to_none(self):
        self.assertIsNone(PagedList().pagination)

    def test_slicing_gives_a_plain_list(self):
        rows = PagedList([1, 2, 3], Pagination(TotalCount=3))

        self.assertNotIsInstance(rows[:2], PagedList)
        self.assertEqual(rows[:2], [1, 2])


class SessionBillsPagingTest(unittest.TestCase):
    """get_session_bills with _get replaced, so no network is involved."""

    def setUp(self):
        self.client = LISClient(api_key="not-a-real-key")
        self.sent = {}

    def install_get(self, header=LIVE_HEADER, rows=("HB9", "HB11")):
        def fake_get(path, params=None, *, page_header=None, capture=None):
            self.sent["path"] = path
            self.sent["params"] = params
            self.sent["page_header"] = page_header

            if capture is not None and header is not None:
                capture["X-Pagination"] = header

            return {"Legislations": [bill_json(n) for n in rows]}

        self.client._get = fake_get

    def test_no_paging_arguments_send_no_header(self):
        self.install_get()

        self.client.get_session_bills(session_code=20271)

        self.assertIsNone(self.sent["page_header"])

    def test_paging_arguments_build_the_header(self):
        self.install_get()

        self.client.get_session_bills(session_code=20271, page_size=5, skip=10)

        self.assertEqual(
            json.loads(self.sent["page_header"]["X-Pagination"]),
            {"PageSize": 5, "SkippedRecords": 10},
        )

    def test_session_code_still_goes_in_the_query_string(self):
        self.install_get()

        self.client.get_session_bills(session_code=20271, page_size=5)

        self.assertEqual(self.sent["params"], {"sessionCode": 20271})

    def test_rows_parse_into_models(self):
        self.install_get()

        page = self.client.get_session_bills(session_code=20271)

        self.assertIsInstance(page[0], LegislationSummaryItem)
        self.assertEqual(page[0].LegislationNumber, "HB9")

    def test_the_response_header_lands_on_the_result(self):
        self.install_get()

        page = self.client.get_session_bills(session_code=20271, page_size=3)

        self.assertEqual(page.pagination.TotalCount, 443)
        self.assertTrue(page.pagination.HasNext)

    def test_a_missing_response_header_leaves_pagination_none(self):
        self.install_get(header=None)

        page = self.client.get_session_bills(session_code=20271)

        self.assertIsNone(page.pagination)
        self.assertEqual(len(page), 2)

    def test_a_204_gives_an_empty_paged_list(self):
        self.client._get = lambda *a, **k: None

        page = self.client.get_session_bills(session_code=99999)

        self.assertIsInstance(page, PagedList)
        self.assertEqual(page, [])
        self.assertIsNone(page.pagination)

    def test_bill_count_reads_total_count_with_one_row(self):
        self.install_get()

        count = self.client.get_session_bill_count(session_code=20271)

        self.assertEqual(count, 443)
        self.assertEqual(json.loads(self.sent["page_header"]["X-Pagination"]), {"PageSize": 1})

    def test_bill_count_is_none_without_a_header(self):
        self.install_get(header=None)

        self.assertIsNone(self.client.get_session_bill_count(session_code=20271))


if __name__ == "__main__":
    unittest.main()
