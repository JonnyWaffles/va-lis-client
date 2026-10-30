"""Boundary tests for the TLS wiring.  These never touch the network.

The trust store is decided by which ``ssl_context`` reaches urllib3's
connection pool.  requests builds that pool's keyword arguments itself, so a
requests release can override the adapter's context without an error.  These
tests ask requests for the pool the way ``HTTPAdapter.send`` does and check
what it got.

Run with:
    pytest tests/test_http.py
"""

import unittest
from unittest import mock

import requests
import truststore

from va_lis_client import LISClient
from va_lis_client.client import BASE_URL, DEFAULT_TIMEOUT
from va_lis_client.http import requests_session, ssl_context


class TestSharedSessionTrust(unittest.TestCase):
    def pool_for(self, verify):
        request = requests.Request("GET", f"{BASE_URL}/Session/api/getsessionlistasync")
        prepared = requests_session.prepare_request(request)
        adapter = requests_session.get_adapter(prepared.url)

        return adapter.get_connection_with_tls_context(prepared, verify=verify)

    def test_the_context_is_the_os_trust_store(self):
        self.assertIsInstance(ssl_context, truststore.SSLContext)

    def test_a_verified_request_reaches_the_pool_with_that_context(self):
        pool = self.pool_for(verify=True)

        self.assertIs(pool.conn_kw["ssl_context"], ssl_context)
        self.assertEqual(pool.cert_reqs, "CERT_REQUIRED")

    def test_a_new_connection_carries_that_context(self):
        connection = self.pool_for(verify=True)._new_conn()

        self.assertIs(connection.ssl_context, ssl_context)


class TestClientSession(unittest.TestCase):
    def test_the_default_is_the_shared_session(self):
        client = LISClient(api_key="not-a-real-key")

        self.assertIs(client.session, requests_session)

    def test_a_caller_session_replaces_it(self):
        session = requests.Session()
        client = LISClient(api_key="not-a-real-key", session=session)

        self.assertIs(client.session, session)


class TestClientHostAndTimeout(unittest.TestCase):
    """What reaches ``session.request``.  A mock session stands in for LIS."""

    def client(self, **kwargs):
        session = mock.Mock(spec=requests.Session)
        response = session.request.return_value
        response.status_code = 200
        response.json.return_value = {"Sessions": []}
        session.get.return_value = response

        return LISClient(api_key="not-a-real-key", session=session, **kwargs), session

    def test_the_defaults_are_production_and_a_bounded_wait(self):
        client, session = self.client()

        client.get_sessions()

        url = session.request.call_args.args[1]
        self.assertTrue(url.startswith(f"{BASE_URL}/"))
        self.assertEqual(session.request.call_args.kwargs["timeout"], DEFAULT_TIMEOUT)

    def test_a_caller_host_and_timeout_reach_the_request(self):
        client, session = self.client(base_url="https://qa.lis.virginia.gov/", timeout=(3, 10))

        client.get_sessions()

        url = session.request.call_args.args[1]
        self.assertTrue(url.startswith("https://qa.lis.virginia.gov/Session/"))
        self.assertEqual(session.request.call_args.kwargs["timeout"], (3, 10))

    def test_none_waits_without_limit(self):
        client, session = self.client(timeout=None)

        client.get_sessions()

        self.assertIsNone(session.request.call_args.kwargs["timeout"])

    def test_the_key_check_uses_the_same_host_and_timeout(self):
        client, session = self.client(base_url="https://qa.lis.virginia.gov", timeout=5)
        session.get.return_value.status_code = 204

        self.assertIsNone(client.check_api_key())

        url = session.get.call_args.args[0]
        self.assertTrue(url.startswith("https://qa.lis.virginia.gov/PartnerAuthentication/"))
        self.assertEqual(session.get.call_args.kwargs["timeout"], 5)

    def test_a_timeout_is_retried(self):
        client, session = self.client(max_retries=1)
        response = session.request.return_value
        session.request.side_effect = [requests.Timeout("slow"), response]

        with mock.patch("va_lis_client.client.time.sleep"):
            client.get_sessions()

        self.assertEqual(session.request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
