"""Bill version models: the LegislationVersion service's rows.

**The name is taken.**  :class:`~va_lis_client.models.legislation.LegislationVersion`
is the 13 row vocabulary of version *types* (Introduced, Substitute, Enrolled)
from ``/LegislationText/api/getlegislationversionlistasync``.  The
LegislationVersion *service* returns something else: the published versions of
one bill.  Those rows are :class:`BillVersion` here, and each one points back
into the vocabulary through ``LegislationVersionID``.
"""

from datetime import datetime

from va_lis_client.models.common import LISModel
from va_lis_client.models.text import TextFile


class BillVersion(LISModel):
    """One published version of one bill.

    Returned by ``getlegislationversionbybillnumberasync`` and
    ``getlegislationversionbylegislationidasync``.

    **These are the text list's records under another shape.**
    ``LegislationTextID`` matched
    :meth:`~va_lis_client.client.LISClient.get_bill_texts` one for one for
    HB1 in 20261 and 20241 and for HB1503 in 20261 (checked 2026-09-17).  This
    shape adds :attr:`ChamberCode`, :attr:`Sponsor`, :attr:`TextDisposition`
    and :attr:`IsActive`, and drops ``IsComplete``, ``GovernorsRequest``,
    ``IsEmergency`` and ``IsReprint``.  It earns its place twice: the bulk
    form fetches the versions of many bills in one request, and the single id
    form is the one call here that returns the files.

    The joins hold.  ``LegislationVersionID`` resolves into the version type
    vocabulary, and ``Version`` equalled that row's ``Name`` on all 551 rows
    checked.  ``LegislationID`` and ``LegislationNumber`` agreed with the
    session's own bill list on every one of them.

    **:attr:`ChamberCode` is the version's chamber, not the bill's.**  44 of
    404 rows sampled from 20261 differ from the bill number's prefix, and every
    one is a substitute or an amendment made in the other chamber.

    **:attr:`IsActive` does not pick one current version.**  6 of 102 bills
    sampled from 20261 had two active rows, and 3 of 60 from 20241 had none.
    Order by :attr:`DraftDate` instead.

    **:attr:`DocumentCode` does not follow the vocabulary's suffix.**  The
    vocabulary gives Substitute the suffix ``S``, but HB1503's substitutes read
    ``HB1503HC1``, ``HB1503HC2`` and ``HB1503H1``.  A Chaptered row carries the
    chapter number instead of the bill number, e.g. ``CHAP0350`` for HB1.
    20241 rows pad the code to ten characters, which this model strips.

    :attr:`Description` is the specific name ("Education Substitute") and
    :attr:`Version` the vocabulary name ("Substitute").  They differed on 169
    of those 404 rows.

    **Older rows omit keys.**  ``TextDispositionID`` and ``TextDisposition``
    were absent, not null, on 145 of 147 rows sampled from 20241 and on 8 of
    404 from 20261.  20241 also sent :attr:`DraftDate` null on 19 rows.  For
    HB1's veto explanation the text list sends ``0001-01-01`` instead.  Those
    dates can also run backwards: SJ76 was Enrolled on 2024-02-06 and
    Introduced on 2024-02-15.

    **Only a request by one ``legislationID`` fills the file arrays.**  That
    form returned a PDF and an HTML link on each of HB1's three versions and
    HB1503's six, plus impact statement PDFs.  The bill number form, the bulk
    ``legislationIDs`` form (even with a single id in it), and the text list
    sent every array empty for the same versions (checked 2026-09-17).
    :attr:`LinkFile` was empty everywhere.  :attr:`IsPublic` was ``True`` on
    all 551 rows.

    Example::

        {"LegislationID": 98525, "LegislationNumber": "HB1",
         "ChamberCode": "H", "SessionID": 59, "LegislationTextID": 268485,
         "DraftDate": "2026-04-08T13:12:02", "DocumentCode": "CHAP0350",
         "Description": "Chaptered", "Sponsor": "Governor",
         "LDNumber": "26101997D", "LegislationVersionID": 4,
         "Version": "Chaptered", "IsPublic": true, "IsActive": true,
         "PdfFile": [], "HtmlFile": [], "LinkFile": [], "ImpactFile": [],
         "TextDispositionID": 11, "TextDisposition": "Posted"}
    """

    LegislationTextID: int  # PK; the same id the text list carries
    LegislationID: int | None = None
    LegislationNumber: str | None = None  # e.g. "HB1"
    ChamberCode: str | None = None  # the chamber that made this version
    SessionID: int | None = None  # the even year session for a carried bill
    DraftDate: datetime | None = None  # null on some 20241 rows
    DocumentCode: str | None = None  # e.g. "HB1ER", "HB1503HC1", "CHAP0350"
    Description: str | None = None  # e.g. "Education Substitute"
    Sponsor: str | None = None  # House, Senate, Committee, Conference, Governor, Member
    LDNumber: str | None = None
    LegislationVersionID: int | None = None  # FK to the LegislationVersion vocabulary
    Version: str | None = None  # the vocabulary Name, e.g. "Substitute"
    IsPublic: bool = True  # always True
    IsActive: bool = False  # not one per bill; see above
    # The file arrays are filled only on a request by one legislationID.
    PdfFile: list[TextFile] | None = None  # TextFormatID 1
    HtmlFile: list[TextFile] | None = None  # TextFormatID 2
    LinkFile: list[TextFile] | None = None  # empty on every row seen
    ImpactFile: list[TextFile] | None = None  # TextFormatID 3, impact statement PDFs
    TextDispositionID: int | None = None  # omitted on most 20241 rows
    TextDisposition: str | None = None  # e.g. "Posted", "Offered", "Adopted"
