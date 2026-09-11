"""Legislation event (per-bill history timeline) models."""

from datetime import datetime

from va_lis_client.models.common import LISModel


class EventReference(LISModel):
    """A reference link on a legislation event (to a text version, vote, etc.).

    Example::

        {"EventReferenceID": 26822,
         "ReferenceText": "Introduced",
         "ReferenceID": 257719,
         "ActionReferenceTypeID": 9,
         "ActionReferenceType": "LegislationText"}
    """

    EventReferenceID: int | None = None
    ReferenceText: str | None = None
    ReferenceID: int | None = None
    LegislationEventID: int | None = None
    ActionReferenceTypeID: int | None = None
    ActionReferenceType: str | None = None  # "LegislationText", "Vote", etc.
    Sequence: int | None = None
    CalendarActionID: int | None = None
    IsMandatory: bool | None = None


class LegislationEvent(LISModel):
    """A bill history event from ``/LegislationEvent/api/getlegislationeventbylegislationidasync``.

    The chronological action log for a bill — every action from prefiling
    through governor action.  This is the **primary source for passage
    dates**, since ``Legislation.HousePassageDate`` and
    ``SenatePassageDate`` are always null in the LIS API.

    Key: ``LegislationEventID``.  Query by ``legislationID``.

    ``ReferenceType`` indicates what the event links to:
    - ``"LegislationText"`` → a text version (ReferenceID = LegislationTextID)
    - ``"Vote"`` → a vote record (VoteID populated, VoteTally has counts)
    - ``"Committee"`` → a committee action (ReferenceID = CommitteeID)
    - ``"LegislationFile"`` → a file (fiscal impact, etc.)

    ``Status`` is the bill's status *after* this event, carried as the
    internal *name* from the 52-value LegislationStatus vocabulary (e.g.
    "Introduced", "In Committee", "Continued To") — a closed vocabulary
    that joins onto the status reference table, not free text.  The
    payload also defines ``LegislationStatusID``, but the API returns it
    null in practice (verified 2026-08-13), so the ``Status`` name is the
    per-event join key into the status vocabulary.

    Key passage-related event codes::

        H5000  Read third time and passed House
        H5001  Passed House
        H5100  Passed House (crossover)
        S5000  Read third time and passed Senate
        S5001  Passed Senate
        S5100  Passed Senate (crossover)
        G7050  Approved by Governor
        G7900  Vetoed by Governor

    Use ``IsPassage`` on ``LegislationEventType`` (not on this model) to
    reliably identify passage events.

    Example::

        {"LegislationEventID": 1561089,
         "EventCode": "H4020",
         "EventDate": "2025-11-17T07:28:00",
         "Description": "Prefiled and ordered printed; Offered 01-14-2026",
         "LegislationID": 98525, "LegislationNumber": "HB1",
         "Status": "Introduced", "ActorType": "House",
         "IsPassed": true}
    """

    LegislationEventID: int  # surrogate PK — globally unique, durable
    # Populated on events, but the event-type *reference* endpoint returns
    # its rows without this ID — cross-reference via ``EventCode`` instead.
    LegislationEventTypeID: int | None = None
    EventCode: str | None = None  # e.g. "H4020", "H1401", "S0205"
    EventDate: datetime | None = None
    DeletionDate: datetime | None = None
    # Description is the primary source for last-action text.  Contains
    # human-readable text like "Reported from Finance (10-Y 5-N)".
    Description: str | None = None
    LegislationID: int | None = None  # FK to Legislation
    VoteID: int | None = None
    VoteTally: str | None = None  # e.g. "(15-Y 7-N)"
    EffectiveType: str | None = None
    EffectiveTypeID: int | None = None
    LegislationNumber: str | None = None  # e.g. "HB1"
    ChamberCode: str | None = None  # "H" or "S"
    Sequence: int | None = None
    SessionCode: str | None = None
    IsPublic: bool = True
    IsPassed: bool | None = None
    IsMapped: bool | None = None
    # CommitteeName is the SOURCE OF TRUTH for committee tracking.  The
    # Legislation detail endpoint's CommitteeNumber/CommitteeName fields
    # are always null — this event-level field is the only reliable way
    # to determine which committee a bill was referred to or reported from.
    CommitteeName: str | None = None
    ParentCommitteeName: str | None = None
    # Null in practice (verified 2026-08-13) — join on ``Status`` (the
    # status *name*) instead.
    LegislationStatusID: int | None = None
    ReferenceID: str | None = None  # context-dependent (text ID, committee ID, etc.)
    ReferenceNumber: str | None = None
    ReferenceTypeID: int | None = None
    ReferenceType: str | None = None  # "LegislationText", "Vote", etc.
    # Status *name* after the event, from the 52-value LegislationStatus
    # vocabulary (e.g. "Introduced", "Continued To") — closed, not free text.
    Status: str | None = None
    ActorID: int | None = None  # CommitteeID when actor is a committee
    # ActorType values: "House", "Senate", "Governor", "Conference", "Committee"
    ActorType: str | None = None
    EventReferences: list[EventReference] | None = None


class LegislationEventType(LISModel):
    """Event type reference from ``/LegislationEvent/api/getlegislationeventtypereferencesasync``.

    There are 3,912 types — most are duplicates with different chamber
    codes.  The key fields for filtering:

    - ``IsPassage``: True for events where a bill passes a chamber
      (H5000/S5000 family) or is defeated (H9520/S9520).
    - ``EventCode``: Prefix indicates actor (H=House, S=Senate,
      G=Governor).  Codes in the 5000 range are passage/defeat events.
    - Continuance (carry-forward) family: 120 types — one per committee
      per chamber, in three flavors ("Continued to next session in X",
      "with amendment(s)", "with substitute") — with committee-numbered
      codes ending 40, 41, and 42 (e.g. H1940 = Continued to next
      session in Transportation).  Floor and conference continuances are
      covered by the event ``Status`` vocabulary instead (statuses
      46–48, "Continued to House/Senate/Conference").

    Rows from this endpoint come back with ``LegislationEventTypeID``
    null (verified 2026-08-13), so ``EventCode`` is the join key between
    events and this table.

    Envelope key: ``EventTypes``.

    Example::

        {"EventCode": "H5000",
         "LegislationDescription": "Read third time and passed House",
         "IsPassed": true, "IsPassage": true}
    """

    LegislationEventTypeID: int | None = None
    EventCode: str | None = None
    LegislationDescription: str | None = None
    CommitteeDescription: str | None = None
    CalendarDescription: str | None = None
    JournalDescription: str | None = None
    VoteDescription: str | None = None
    LegislationChamberCode: str | None = None
    ActorTypeID: int | None = None
    IsPublic: bool = True
    IsActive: bool = True
    CommitteeComplete: bool = False
    IsPassed: bool | None = None
    IsPassage: bool | None = None  # True for passage/defeat events
    ActionReferences: list[EventReference] | None = None
    AdministrativeAction: bool | None = None
    ReconsiderationDescription: str | None = None


class ActorType(LISModel):
    """Actor type reference from ``/LegislationEvent/api/getactortypereferencesasync``.

    Five actor types::

        1 = House
        2 = Committee
        3 = Conference
        4 = Governor
        5 = Senate

    Envelope key: ``ActorTypes``.

    Example::

        {"ActorTypeID": 1, "Name": "House"}
    """

    ActorTypeID: int
    Name: str
