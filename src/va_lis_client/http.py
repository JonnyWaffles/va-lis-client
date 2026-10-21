"""
Requests session that verifies TLS against the operating system's trust store.

The adapter hands urllib3 a ``truststore.SSLContext``.  On Windows and macOS
that context passes each server chain to the platform verifier
(``CertGetCertificateChain`` on Windows), so the platform's own distrust list
applies, as do enterprise and government roots that IT pushes to the machine.
On Linux it verifies with OpenSSL against the system CA bundle.

Do not go back to ``ssl.create_default_context()`` + ``load_default_certs()``.
On Windows that copies the ROOT and CA stores into OpenSSL, and OpenSSL never
reads Microsoft's distrust list.  After the eMudhra intermediate CA compromise
(October 2026) such a context still accepted both revoked chains while
Windows itself rejected them.

requests also loads certifi into this context on each new connection (it sets
``ca_certs`` whenever ``verify`` is true).  On Windows and macOS truststore
tries those extra roots only after the platform rejects a chain, and the
platform's distrust list still applies then.  On Linux the trust is the union
of the system bundle and certifi, so both must be current.

To choose trust yourself, pass your own ``requests.Session`` to
``LISClient(session=...)``.
"""

import ssl

import requests
import truststore
from requests.adapters import HTTPAdapter
from urllib3 import PoolManager

ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


class SystemCertSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=ssl_context,
            **pool_kwargs,
        )


requests_session = requests.Session()
requests_session.mount("https://", SystemCertSSLAdapter())
