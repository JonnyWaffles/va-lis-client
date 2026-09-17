"""Communication models — what one chamber formally told the other.

A communication is a message the clerk of one chamber sends to the other:
"the House has passed the following Senate bills", "the Senate has agreed to
the House substitute".  It nests three levels: the communication holds
categories, a category holds legislation rows, and a legislation row holds
patrons.

The category is the sentence and the legislation rows are its list.  Read
:attr:`CommunicationCategory.label` rather than either description field
directly, because the two disagree — see that class.

This layer resolves cleanly to bills and members, and **not at all to
votes**.  ``VoteNumber`` is null on every row, ``VoteID`` is never sent, and
the ``voteID`` and ``voteNumber`` query parameters both answer 204.
"""

from datetime import datetime

from pydantic import Field

from va_lis_client.models.common import LISModel
from va_lis_client.models.legislation import Patron


class CommunicationFile(LISModel):
    """A generated file attached to a communication.

    ``TextFormatID`` 2 is HTML and 5 is JSON, and modern communications
    publish both.  **The JSON blob is richer than the API**: it adds
    ``ClerkTitle``, ``ClerkName``, ``FooterSentence``, ``HtmlContent`` and
    ``ModificationDate``, at the cost of sending ``CommunicationNumber`` as a
    string.  Fetch the URL yourself when you need those.

    ``IsPublic`` and ``IsActive`` were ``True`` on all 4,617 files observed,
    so neither tells you anything.

    Example::

        {"CommunicationFileID": 1180129, "CommunicationID": 4520,
         "FileURL": "https://lis.blob.core.windows.net/files/1180129.HTML",
         "TextFormatID": 2, "IsGenerated": true, "IsPublic": true,
         "IsActive": true}
    """

    CommunicationFileID: int | None = None
    CommunicationID: int | None = None
    FileURL: str | None = None
    TextFormatID: int | None = None  # 2=HTML, 5=JSON
    IsGenerated: bool = True
    IsPublic: bool = True  # always True
    IsActive: bool = True  # always True


class CommunicationLegislation(LISModel):
    """One bill named in one category of a communication.

    Its foreign keys are honest.  ``LegislationID`` 98536 resolves to SB2 in
    20261 and ``LegislationTextID`` 267622 to that bill's Enrolled text with
    ``DocumentCode`` ``SB2ER`` (checked 2026-09-15, no mismatches in the
    sample).  ``LegislationKey`` is just the digits of ``LegislationNumber``
    in all 440 rows checked, so it carries nothing extra.

    **Three fields are always null**: :attr:`CommitteeReferredName`,
    :attr:`EventCode` and :attr:`VoteNumber`.  The spec also promises a
    ``VoteID`` that LIS never sends.  There is no vote join here.

    :attr:`DraftTitle` carries HTML that arrives *escaped*, so it holds the
    literal text ``&lt;a href&gt;`` rather than a tag.

    :attr:`Patrons` uses the thin patron shape, and ``DisplayName`` is
    **omitted** on most rows rather than sent as null.

    Example (truncated)::

        {"CommunicationLegislationID": 20115, "CommunicationCategoryID": 1701,
         "LegislationID": 98536, "LegislationNumber": "SB2",
         "LegislationKey": 2, "LegislationTextID": 267622,
         "DocumentCode": "SB2ER", "EffectiveType": "Standard",
         "IsCandidate": false, "Patrons": [{"MemberID": 217, ...}]}
    """

    CommunicationLegislationID: int | None = None
    CommunicationCategoryID: int | None = None
    LegislationID: int | None = None  # FK to Legislation, verified honest
    LegislationNumber: str | None = None  # e.g. "SB2"
    LegislationKey: int | None = None  # the digits of LegislationNumber
    LegislationDescription: str | None = None  # LIS pads this
    LegislationTextID: int | None = None  # FK to a text version
    DocumentCode: str | None = None  # e.g. "SB2ER"
    LDNumber: str | None = None
    DraftTitle: str | None = None  # HTML, arrives escaped
    EffectiveTypeID: int | None = None  # 1 Standard, 2 Emergency
    EffectiveType: str | None = None
    CommitteeReferredName: str | None = None  # always null
    EventCode: str | None = None  # always null
    VoteNumber: str | None = None  # always null — there is no vote join
    IsCandidate: bool = False
    IsActive: bool = True  # always True
    Patrons: list[Patron] = []


class CommunicationCategory(LISModel):
    """One sentence of a communication, with the bills it applies to.

    **The two description fields disagree, and LIS builds the summary from
    the wrong one.**  :attr:`Description` is omitted on most categories, but
    on all 13 rows that carried it (of 56 checked on 2026-09-15) it
    contradicted :attr:`CategoryDescription`.  Communication 4520, category
    code ``HN2300``, read:

    - ``CategoryDescription``: "THE HOUSE OF DELEGATES HAS STRICKEN FROM ITS
      CALENDAR THE FOLLOWING SENATE BILL:"
    - ``Description``: "THE HOUSE OF DELEGATES HAS PASSED BY FOR THE DAY THE
      FOLLOWING SENATE BILL:"

    and :attr:`MinutesSummary` repeated the stricken sentence.  Those are
    different parliamentary acts.  :attr:`label` prefers ``Description`` when
    it is there, so use it instead of either field.

    **A category type repeats within one communication**, so the type id is
    not the identity.  Communication 4519 carried
    ``CommunicationCategoryTypeID`` 1467 four times, and 4520 carried type
    1823 seven times.  Key on :attr:`CommunicationCategoryID`.

    An empty category is normal: 22 of those 56 held no legislation and an
    empty :attr:`MinutesSummary`.

    The payload key for the bills is ``CommunicationLegislation``, which
    collides with the model class, so the field is aliased to
    :attr:`bills`.

    Example::

        {"CommunicationCategoryID": 1701, "CommunicationID": 4520,
         "CommunicationCategoryTypeID": 1823, "CategoryCode": "HN2300",
         "CategoryDescription": "THE HOUSE OF DELEGATES HAS STRICKEN ...",
         "PluralDescription": "...", "Sequence": 1,
         "MinutesSummary": "...", "CommunicationLegislation": [{...}]}
    """

    CommunicationCategoryID: int | None = None  # the identity
    CommunicationID: int | None = None
    CommunicationCategoryTypeID: int | None = None  # repeats within a communication
    CategoryCode: str | None = None  # e.g. "HN2300"
    CategoryDescription: str | None = None  # contradicted by Description
    Description: str | None = None  # omitted on most rows; trust this one
    PluralDescription: str | None = None
    Sequence: int | None = None
    MinutesSummary: str | None = None  # built from CategoryDescription
    # Aliased because the payload key ``CommunicationLegislation`` collides
    # with the model class of the same name, and a field named for its own
    # class shadows it inside the class body.
    bills: list[CommunicationLegislation] = Field(default=[], alias="CommunicationLegislation")

    @property
    def label(self) -> str | None:
        """The sentence to show, preferring the field that is correct.

        ``Description`` when LIS sent it, else ``CategoryDescription``.
        """
        return self.Description or self.CategoryDescription


class CommunicationListItem(LISModel):
    """A row from ``getlegislationcommunicationslistasync``.

    Shallow: it names no bills.  Call
    :meth:`~va_lis_client.client.LISClient.get_communication` for those.

    **Three fields are constant and carry nothing.**  ``IsPublic`` was
    ``True``, ``CommunicationTypeID`` was ``1`` and ``CommunicationType`` was
    ``"Floor"`` on all 4,383 rows LIS holds.  There is no second type, which
    is also why the ``communicationType`` filter does nothing.

    ``ShowSignatureNote`` is the one genuine tri-state: 3,839 false, 212
    true, and 332 **null**.

    ``ReferenceNumber`` is unique within a session but repeats across them —
    ``SN10422`` appears in three — so key on
    ``(SessionID, ReferenceNumber)``.

    Example::

        {"CommunicationID": 4520, "CommunicationDate": "2026-02-17T00:00:00",
         "CommunicationNumber": 1, "ReferenceNumber": "HN10035",
         "IsPublic": true, "CommunicationTypeID": 1,
         "CommunicationType": "Floor", "ChamberCode": "H", "SessionID": 59,
         "ShowSignatureNote": false, "CommunicationFiles": [{...}]}
    """

    CommunicationID: int  # surrogate PK
    CommunicationDate: datetime | None = None
    CommunicationNumber: int | None = None  # 1..15 within a day
    ReferenceNumber: str | None = None  # unique per session only
    ChamberCode: str | None = None  # the chamber that sent it
    SessionID: int | None = None
    CommunicationTypeID: int | None = None  # always 1
    CommunicationType: str | None = None  # always "Floor"
    IsPublic: bool = True  # always True
    ShowSignatureNote: bool | None = None  # genuinely tri-state
    CommunicationFiles: list[CommunicationFile] = []

    @property
    def html_file(self) -> CommunicationFile | None:
        """The generated HTML file, when the communication has one."""
        return next((f for f in self.CommunicationFiles if f.TextFormatID == 2), None)

    @property
    def json_file(self) -> CommunicationFile | None:
        """The generated JSON file, when the communication has one."""
        return next((f for f in self.CommunicationFiles if f.TextFormatID == 5), None)


class Communication(CommunicationListItem):
    """A full communication from ``getlegislationcommunicationsbyidasync``.

    The list row plus :attr:`CommunicationCategories`, which is where the
    bills live, plus ``SessionCode`` and a ``DocumentCode`` that was null on
    every row observed.

    **Structured content starts at SessionID 56, May 2024.**  Ask for an
    older one and you get an empty :attr:`CommunicationCategories` and a
    single HTML file, with no bills to read (verified on communications 3625
    of 20191 and 4192-4194 of 20241).  Before that date, the file is the
    record.

    Example (truncated)::

        {"CommunicationID": 4520, "SessionCode": "20261",
         "DocumentCode": null, "ChamberCode": "H",
         "CommunicationCategories": [{"CategoryCode": "HN2300",
                                      "CommunicationLegislation": [{...}]}]}
    """

    SessionCode: str | None = None
    DocumentCode: str | None = None  # always null
    CommunicationCategories: list[CommunicationCategory] = []

    @property
    def categories(self) -> list[CommunicationCategory]:
        """The categories that actually name a bill, in arrival order."""
        return [c for c in self.CommunicationCategories if c.bills]

    @property
    def bills(self) -> list[CommunicationLegislation]:
        """Every bill across every category, in arrival order."""
        return [b for c in self.CommunicationCategories for b in c.bills]


class CommunicationCategoryType(LISModel):
    """The vocabulary behind ``CommunicationCategoryTypeID``, 231 rows.

    87 House and 144 Senate.  :attr:`Description` is the singular sentence
    and :attr:`PluralDescription` the plural one, which is what a category
    picks between by how many bills it lists.

    The three count fields are **undocumented** — the OpenAPI spec omits
    them — and were ``0`` on the rows sampled.

    ``Sequence`` is omitted on exactly one of the 231 rows
    (``CommunicationCategoryTypeID`` 1380, ``SN0090``), which is why it is
    optional.

    Example::

        {"CommunicationCategoryTypeID": 1380, "CategoryCode": "SN0090",
         "Description": "THE SENATE HAS PASSED WITH AMENDMENTS ...",
         "PluralDescription": "THE SENATE HAS PASSED WITH AMENDMENTS ...",
         "ChamberCode": "S", "FirstChamberCount": 0,
         "SecondChamberCount": 0, "AmendmentCount": 0}
    """

    CommunicationCategoryTypeID: int
    CategoryCode: str | None = None  # e.g. "SN0090"
    Description: str | None = None  # singular form
    PluralDescription: str | None = None  # plural form
    ChamberCode: str | None = None  # "H" or "S"
    Sequence: int | None = None  # omitted on one row of 231
    FirstChamberCount: int | None = None  # undocumented
    SecondChamberCount: int | None = None  # undocumented
    AmendmentCount: int | None = None  # undocumented
