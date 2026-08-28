"""Pagination models for the undocumented ``X-Pagination`` protocol.

LIS supports paging, but not through the query string.  Every query string
parameter tried was silently ignored: ``pageSize``, ``pageNumber``, ``limit``,
``offset``, ``take``, ``skip``, ``maxResults``, ``$top``, and the PascalCase
forms of the first two.  Paging runs on an ``X-Pagination`` **request header**
carrying JSON, and every response returns an ``X-Pagination`` header of its
own.  The API sets ``Access-Control-Expose-Headers: X-Pagination``, so this is
deliberate rather than accidental.

Request fields that work (verified live 2026-08-28 against
``getlegislationsessionlistasync``):

- ``PageSize`` caps the number of rows returned.
- ``SkippedRecords`` is a raw record offset.  It is the field that moves the
  window.

``PageNumber`` is accepted and then ignored: the response echoes it as ``1``
whatever you send.  The response's ``CurrentPage`` is derived instead, from
``SkippedRecords`` divided by ``PageSize``.

Three windows on session 20271 at ``PageSize=3`` show real offsetting::

    SkippedRecords=0  ->  HB9,  HB11, HB13
    SkippedRecords=3  ->  HB28, HB35, HB42
    SkippedRecords=6  ->  HB49, HB57, HB66

None of this appears in the developer portal, which serves a React shell, or
in ``/swagger/v1/swagger.json``, which serves the same HTML rather than a spec.
"""

from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T")


class Pagination(BaseModel):
    """The ``X-Pagination`` response header, parsed.

    ``TotalCount`` is the useful one: it reports the full result size without
    downloading it, so a ``PageSize=1`` request is a cheap way to size a
    session.

    Example (session 20271 at ``PageSize=3``, ``SkippedRecords=6``)::

        {"PageSize": 3, "PageNumber": 1, "TotalPages": 148, "TotalCount": 443,
         "CurrentPage": 3, "SkippedRecords": 6, "HasPrevious": true,
         "HasNext": true}
    """

    PageSize: int | None = None
    PageNumber: int | None = None  # server always reports 1; see module docstring
    TotalPages: int | None = None
    TotalCount: int | None = None
    CurrentPage: int | None = None  # derived from SkippedRecords / PageSize
    SkippedRecords: int | None = None
    HasPrevious: bool | None = None
    HasNext: bool | None = None

    @classmethod
    def from_header(cls, raw: str | None) -> Pagination | None:
        """Parse an ``X-Pagination`` header value, or return None if unusable."""
        if not raw:
            return None

        try:
            return cls.model_validate(json.loads(raw))
        except (ValueError, ValidationError):
            return None


class PagedList(list[T]):
    """Result rows that also carry the server's pagination metadata.

    This is a real ``list``, so code that iterates it, indexes it, or calls
    ``len`` on it needs no change.  Newer code reads ``.pagination`` for the
    total row count and for whether more rows follow.

    ``pagination`` is ``None`` when the server sent no usable header.

    Slicing or concatenating gives back a plain ``list``, because the metadata
    describes the whole window and would be wrong on a subset.
    """

    def __init__(self, rows=(), pagination: Pagination | None = None):
        super().__init__(rows)
        self.pagination = pagination

    def __repr__(self) -> str:
        return f"PagedList({list(self)!r}, pagination={self.pagination!r})"


def page_request_header(
    page_size: int | None = None,
    skip: int | None = None,
) -> dict[str, str] | None:
    """Build the ``X-Pagination`` request header, or None when nothing is set.

    Args:
        page_size: Maximum rows to return.
        skip: Records to skip before the window starts.

    Returns:
        A one entry header dict, or None when both arguments are None.
    """
    fields: dict[str, int] = {}

    if page_size is not None:
        fields["PageSize"] = page_size
    if skip is not None:
        fields["SkippedRecords"] = skip

    if not fields:
        return None

    return {"X-Pagination": json.dumps(fields)}
