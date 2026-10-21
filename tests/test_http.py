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

import requests
import truststore

from va_lis_client import LISClient
from va_lis_client.client import BASE_URL
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


if __name__ == "__main__":
    unittest.main()
