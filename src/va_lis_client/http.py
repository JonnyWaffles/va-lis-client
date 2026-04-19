"""
Requests session with a system-certificate SSL adapter.

Python on Windows does not automatically load certificates from the system
certificate store the way Linux OpenSSL builds typically do.  This adapter
mounts a custom PoolManager that passes the default SSL context — populated
with ``ssl.create_default_context()`` + ``load_default_certs()`` — so that
connections to hosts with enterprise or government CA roots succeed without
manually bundling certificates.
"""

import ssl

import requests
from requests.adapters import HTTPAdapter
from urllib3 import PoolManager

ssl_context = ssl.create_default_context()
ssl_context.load_default_certs()


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
