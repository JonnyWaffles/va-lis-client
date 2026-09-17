"""Directory models — the clerks behind a committee, and where to write to a member.

Three small reference services sit here.  Personnel is the useful one: it is
the only place LIS says who clerks a committee, and its ``AffiliationID`` is
a ``CommitteeID``.  Contact holds member district offices.  Organization is
the state budget agency roster, which touches nothing legislative.

None of the three is session scoped.  No session parameter exists, and
``sessionCode`` is silently ignored on all of them, so the client keeps these
for the life of the service instance.
"""

from datetime import datetime

from va_lis_client.models.common import LISModel


class Personnel(LISModel):
    """A clerk or staffer, and the committee they serve.

    **``AffiliationID`` is a ``CommitteeID``.**  All 34 distinct values
    resolve (checked 2026-09-15): 1 is H01 Agriculture, 8 is H08 Courts of
    Justice, 22 is S01 Agriculture.  Ten of them are subcommittees that
    :meth:`~va_lis_client.client.LISClient.get_committees` does not return,
    because that list holds only the 25 standing committees; fetch those by
    id instead.  Id 55 is S04001 Public Education under parent 25, and 109 is
    S01003 Rural Affairs under parent 22.

    **The grain is one row per assignment, not per person.**  ``StaffID`` is
    the row key and ``IdentityID`` the person, so one person appears more
    than once: identity 679 serves committees 202 and 22.

    **Four fields are omitted unless you filter.**  A bare
    :meth:`~va_lis_client.client.LISClient.get_personnel` call returns 7 keys
    and no ``StaffID``, ``AffiliationID``, ``StaffRoleTypeID`` or
    ``EffectiveBeginDate`` at all; pass any parameter and all 11 arrive.  So
    filter if you want the committee link.

    **Three fields are dead.**  ``PhoneNumber`` and ``EmailAddress`` are null
    on all 146 rows, and ``OrganizationName`` is null on every filtered row.
    ``EffectiveEndDate`` is in the spec and never sent.

    ``IsPublic`` is constant per call (``False`` for all 33 bare rows,
    ``True`` for all 54 clerks), so it is not a filter.  ``FullName`` arrives
    in two formats in one response, ``"Kindermann, Stephen"`` beside
    ``"Jeff Sharp"``, and LIS pads 26 of them.

    Example::

        {"StaffID": 1883, "AffiliationID": 202, "StaffRoleTypeID": 1,
         "IdentityID": 679, "OrganizationName": null,
         "FullName": "Michael Jackson", "PhoneNumber": null,
         "EmailAddress": null, "Sequence": 1, "IsPublic": true,
         "EffectiveBeginDate": "2024-01-10T00:00:00"}
    """

    StaffID: int | None = None  # the assignment; omitted on an unfiltered call
    IdentityID: int | None = None  # the person; repeats across assignments
    AffiliationID: int | None = None  # a CommitteeID — omitted when unfiltered
    StaffRoleTypeID: int | None = None  # 1 clerk, 2 staff — omitted when unfiltered
    FullName: str | None = None  # LIS pads this; two name formats in one list
    OrganizationName: str | None = None  # null on every filtered row
    PhoneNumber: str | None = None  # always null
    EmailAddress: str | None = None  # always null
    Sequence: int | None = None
    IsPublic: bool = True  # constant per call
    EffectiveBeginDate: datetime | None = None  # omitted when unfiltered


class StaffRoleType(LISModel):
    """The 7 staff roles, from ``getstaffroletypesasync``.

    The vocabulary behind :attr:`Personnel.StaffRoleTypeID`.  1 is the clerk
    role that the 54 committee clerks carry and 2 is the staff role the 59
    DLS staffers carry; the two sets share no ``StaffID``.

    Example::

        {"StaffRoleTypeID": 4, "Name": "Clerk of the House of Delegates"}
    """

    StaffRoleTypeID: int
    Name: str | None = None


class Contact(LISModel):
    """A postal address, phone, and email for one owner.

    **For a member, this is the district office**, which is the part LIS
    publishes nowhere else.  A :class:`~va_lis_client.models.member.Member`
    already carries ``GABEmailAddress`` and ``GABPhoneNumber``, and those
    match the contact's email on 115 of 124 rows, so the address and the
    district phone are the reason to come here.

    ``ContactTypeID`` 9 (``"Mailing"``, owner type 3 ``Member``) is the
    member row.  **Not every member has one**: 390 such rows exist against
    148 sitting members in 20261, so the set is part historical and part
    incomplete.  Member 503 has one and member 527 answers 204.

    **``OwnerTypeID`` is declared required and never sent.**  No row carries
    it, so read the owner type off :class:`ContactType` instead.

    Most fields are genuinely null rather than omitted, and for the member
    rows they are mostly filled: of the 390, 384 carry a postal address and
    367 a phone.  ``IsActive`` and ``IsPublic`` are ``True`` on all but one
    row each, so neither is a useful filter.  ``StateCode`` and ``ZipCode``
    are padded on a handful, including one state of ``"  "``.

    Example::

        {"ContactInformationID": 373, "OwnerID": 503, "ContactTypeID": 9,
         "ContactType": "Mailing", "Address1": "P.O. Box 1853",
         "City": "Norfolk", "StateCode": "VA", "ZipCode": "23501",
         "PhoneNumber": "(757) 204-5492",
         "EmailAddress": "DelBAnthony@house.virginia.gov",
         "IsActive": true, "IsPublic": true}
    """

    ContactInformationID: int | None = None
    OwnerID: int | None = None  # a MemberID when ContactTypeID is 9
    ContactTypeID: int | None = None  # 9 = a member's mailing address
    ContactType: str | None = None  # e.g. "Mailing", denormalized
    Address1: str | None = None
    Address2: str | None = None
    Address3: str | None = None
    City: str | None = None
    StateCode: str | None = None  # LIS pads this
    ZipCode: str | None = None  # LIS pads this
    PhoneNumber: str | None = None  # the district phone, not the GAB one
    EmailAddress: str | None = None
    IsActive: bool = True
    IsPublic: bool = True


class ContactType(LISModel):
    """The 10 contact types, and the owner type each belongs to.

    This is the only way to read a contact's owner type, because
    :class:`Contact` never sends ``OwnerTypeID``.  The pairs matter: type 9
    is a member's mailing address, type 3 is a website user account, and the
    two mean entirely different things.

    Example::

        {"ContactTypeID": 9, "OwnerTypeID": 3, "OwnerType": "Member",
         "ContactType": "Mailing"}
    """

    ContactTypeID: int
    ContactType: str | None = None  # "Home", "Work", "User", "Main", "Mailing"
    OwnerTypeID: int | None = None
    OwnerType: str | None = None  # "Person", "User", "Member", "Chamber", ...


class OwnerType(LISModel):
    """The 6 owner types a contact can hang off.

    1 Person, 2 User, 3 Member, 4 Chamber, 5 District, 6 Organization.

    Example::

        {"OwnerTypeID": 4, "OwnerType": "Chamber"}
    """

    OwnerTypeID: int
    OwnerType: str | None = None


class Agency(LISModel):
    """A state budget agency, from ``getagencyreferencesasync``.

    **This touches nothing legislative.**  It is the executive budget
    roster: 295 agencies grouped by :attr:`Branch` (Education, Public Safety,
    Central Appropriations, and so on).  Its ``IdentityID`` shares no value
    with a ``MemberID``, a ``CommitteeID``, or a
    :attr:`Personnel.IdentityID` (checked 2026-09-15, zero overlap in every
    direction).  Wired for completeness, not for joining.

    It supersedes ``getorganizationlistasync``, which returns the same 295
    identities with only ``Name`` — equal to :attr:`AgencyName` on every row
    — plus one extra row, identity 749 "General Assembly".

    Example::

        {"IdentityID": 750, "AgencyName": "9(C) Revenue Bonds",
         "Abbreviation": "RBNC", "AgencyNumber": "950",
         "Branch": "Central Appropriations"}
    """

    IdentityID: int
    AgencyName: str | None = None
    Abbreviation: str | None = None
    AgencyNumber: str | None = None  # a string, not an int
    Branch: str | None = None  # e.g. "Education", "Public Safety"
