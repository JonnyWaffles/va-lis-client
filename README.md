# va-lis-client

Python client for the **Virginia Legislative Information System (LIS) REST API**.

Built from live API responses — the official OpenAPI specs are unreliable
(response envelope keys, field casing, and parameter behavior all differ from
the docs).

This package doubles as the API documentation, recording behavior found
nowhere else, such as [pagination](#pagination-undocumented).

- Developer portal: https://lis.virginia.gov/developers
- API key registration: https://lis.virginia.gov/apiregistration
- Help desk: lis@dlas.virginia.gov

## Installation

```bash
pip install va-lis-client
```

With Redis rate limiting support:

```bash
pip install "va-lis-client[rate-limiting]"
```

## Quick start

```python
import os
from va_lis_client import LISClient

# Pass the key directly, or set the LIS_API_KEY environment variable
client = LISClient(api_key="your-api-key-here")
# or: os.environ["LIS_API_KEY"] = "your-key"; client = LISClient()

# The GA's current working session. During the interim this is the
# *upcoming* session, not the one that just ended — see
# "Bill identity & carry-over" below.
session = client.get_default_session()

# All bills in that session
bills = client.get_session_bills(session_code=int(session.SessionCode))

# Full detail for a bill
bill = client.get_bill(legislation_id=98525)

# Bill text (HTML) and summaries
texts = client.get_bill_text_detail(legislation_id=98525, session_code=20261)
summaries = client.get_bill_summaries(legislation_number="HB1", session_code=20261)
```

## Service layer

`LISClient` is transport: one method per endpoint, and no interpretation.
`LISService` sits above it. It does the work every caller would otherwise
repeat: resolving human bill numbers to IDs, joining the reference
vocabularies, caching the large lists, and flattening bill text HTML.

```python
from va_lis_client import LISClient, LISService, strip_html
from va_lis_client.models import CHIEF_PATRON

service = LISService(LISClient())   # or LISService() to build the client for you

# Bill numbers, not surrogate keys. Case and padding are normalized.
bill = service.get_bill("hb0001", 20261)
bill_id = service.resolve_bill_id("HB1", 20261)  # ~1.3 KB, via the text list
item = service.resolve_bill("HB1", 20261)        # the session row; needs the 3 MB list

# Text: the newest version by default, or name one by DocumentCode.
item, detail = service.bill_text("HB1", 20261)
item, detail = service.bill_text("HB1", 20261, "HB1ER")
print(strip_html(detail.DraftText))

# Who voted which way, with party and district already attached.
for row in service.roll_call("HB1", 20261):
    print(row.name, row.party, row.district, row.response)
# Lashrecse D. Aird   D  13th  Y
# Luther Cifers, III  R  10th  N

# The other axis: everything one member voted on.
for v in service.member_votes(503, 20261):
    print(v.bill_number, v.response, "BLOCK" if v.is_block else "")
votes = service.member_votes_on(503, "hb0001", 20261)

# Members by name, and the bills a member patrons, co-patronage included.
schmidt = service.find_members("schmidt", 20261)[0]            # MemberID 544
for b in service.member_bills(schmidt.MemberID, 20261):
    print(b.LegislationNumber, b.LegislationStatus, b.SummaryVersion)
chief = service.member_bills(schmidt.MemberID, 20261, role=CHIEF_PATRON)
patrons = service.bill_patrons("HB1408", 20261)                # every role

# Committees: who sits where, with party and district, and the reverse.
courts = service.resolve_committee("Courts of Justice", "H")   # H08
for s in service.committee_members(courts.CommitteeID, 20261):
    print(s.name, s.role, s.party, s.district)
seats = service.member_committees(schmidt.MemberID, 20261)     # 14 cached requests

# Senate dockets: every bill a committee has scheduled, with the date and room.
for e in service.docket_entries("Courts of Justice", 20261):
    print(e.date, e.bill_number, e.room)

# Every vote including the ones no member can be credited with.
for record in service.bill_votes("HB1", 20261):
    notes = record.statements_by_member()
    for code, members in record.responses().items():   # "Y" "N" "A" "X"
        for m in members:
            print(code, m.MemberNumber, m.name, notes.get(m.MemberID, []))

# Reference joins. Events carry a null type ID and a null status ID, so these
# index the vocabularies on the keys that events actually carry.
types = service.event_types_by_code()          # EventCode -> [LegislationEventType]
kind = service.event_type_for(event)           # the one row describing an event
statuses = service.statuses_by_name()          # Name      -> LegislationStatus
label = service.bill_status_label(bill, item)  # best available status label
```

**Voting records.** `bill_votes` walks a bill's events, takes each `VoteID`,
and fetches the roll call behind it. Events are the only bill-first route,
because no vote endpoint accepts a `legislationID`. HB1 in 20261 returns eight
votes: two House committee, one House floor, two Senate committee, three
Senate floor. Six of the eight are attributable to HB1 alone. See
[Votes](#votes-per-member-roll-calls) for why the other two are not.

**`roll_call` is `bill_votes` with the roster joined on.** A ballot from LIS
names a member by `MemberID` and nothing else, so party and district take a
second lookup against the session roster, cached for an hour. `roll_call`
returns only the attributable votes, since naming a member on a block or voice
vote misstates the record. HB1 gives 214 named ballots across its six roll
calls. Each entry carries its own `vote`, because one bill has many.

**`EventCode` is not unique.** The event-type vocabulary holds 3,912 rows under
only 1,502 codes, and `H1405` alone returns four. Two of those four read
"Reported from Labor and Commerce" and two read "Failed to report (defeated)
in Labor and Commerce". `event_types_by_code` therefore returns **every** row
per code, and `event_type_for` picks the one that describes a given event. See
[Joining the event vocabulary](#joining-the-event-vocabulary).

**Resolving an id cheaply.** The text list endpoint takes a bill number and
returns rows carrying the `LegislationID`, so `resolve_bill_id` costs about
1.3 KB instead of the 3 MB session list. It is session scoped, so it will not
resolve a bill into a session it does not belong to. Bills with no published
text fall back to the list. Use `resolve_bill` when you want the session row
itself, for its status, description, or chief patron.

**Caching.** The session bill list backs substring search and is the fallback
for id resolution. It runs to roughly 3 MB, so `LISService` caches it for 15
minutes (tune with `bill_list_ttl=`). The member roster changes only when a
member resigns or arrives, so it is cached for an hour (`roster_ttl=`), and a
member's 1.9 MB vote history rides the same TTL, keyed per member. A member's
bill list rides it too, keyed per member, session, and role. The static
reference vocabularies, the session reference included, are kept for the life
of the instance. Caches live on the instance, so build one service and keep
it. Instances are safe to share between threads.

**Errors.** Everything derives from `LISError`, so one `except` clause covers
the package. The resolution errors also derive from the builtin that fits them.

| Exception | Also a | Raised when |
| --- | --- | --- |
| `LISClientError` | | The API returns a non-success response |
| `InvalidBillNumberError` | `ValueError` | A string does not parse as a bill number |
| `BillNotFoundError` | `LookupError` | A bill number is not in the session |
| `TextVersionNotFoundError` | `LookupError` | No text version matches, or the version has no body |
| `SessionNotFoundError` | `LookupError` | A session code is not in the session reference |
| `CommitteeNotFoundError` | `LookupError` | No committee, or more than one, matches a number or name |

## Authentication

Every request sends a `WebAPIKey` header with a partner GUID obtained at
https://lis.virginia.gov/apiregistration.

Set the key via environment variable:

```bash
export LIS_API_KEY=your-guid-here
```

Or pass it directly to the constructor:

```python
client = LISClient(api_key="your-guid-here")
```

**Important:** Heartbeat endpoints accept *any* key — even garbage. Use
`client.check_api_key()` to actually validate a key via the
PartnerAuthentication service.

## Data availability

- The session reference list reaches back to **1994**.
- Bill data has been observed working for the **2024–2027 sessions** (as of
  Aug 2026). Earlier guidance said only 2025–2026 was authorized via the API.
- **Votes reach back to 1994** — much further than bills. `voteID=1` is a
  January 1994 committee vote. Do not assume a vote ID belongs to a recent
  session.
- Older session data is available as `legacylis.virginia.gov` CSV downloads.
- **No rate limit is enforced, but usage is tracked per key.** DLAS said in
  September 2026 that they do not rate limit, that they watch per key usage,
  and that they have revoked keys after sending warnings. Rate limit yourself;
  see [Rate limiting](#rate-limiting-optional).

## Identifier cheat-sheet

Bills are uniquely identified by **session + bill number**, but the API uses
several overlapping identifiers:

| Identifier | Example | Notes |
|---|---|---|
| `SessionCode` | `20261` | Year + sequence. `20261` = 2026 Regular, `20262` = Special I |
| `SessionID` | `59` | Surrogate PK. Either code or ID works as query param |
| `LegislationNumber` | `"HB1"` | **Must be unpadded** — `HB0001` returns 204 |
| `LegislationID` | `98525` | Surrogate PK for the *logical* bill — reused across carry-over (see below) |
| `DocumentCode` | `"HB1ER"` | Bill number + version suffix |
| `LegislationTextID` | `257719` | PK for one immutable text snapshot — follows the bill across carry-over (see below) |
| `CommitteeID` | `14` | Surrogate PK for a committee |
| `CommitteeNumber` | `"H14"` | Chamber prefix + number, e.g. `H14`, `S02` |
| `CalendarID` | `21146` | PK for a House floor calendar |
| `DocketID` | `21114` | PK for a Senate committee docket |
| `ScheduleID` | `3626` | PK for a scheduled meeting |
| `LegislationEventID` | `1561089` | PK for a bill history event — follows the bill across carry-over (see below) |
| `VoteID` | `294006` | PK for one vote. Reachable only from `LegislationEvent.VoteID` |
| `MemberID` | `217` | PK for a **person**. Stable across every vote and session |
| `VoteMemberID` | `10988439` | PK for one **ballot** — that person's response on that one vote. New every vote |
| `MemberNumber` | `"H0206"` | Chamber prefix + number, the human-facing member id |
| `DistrictID` | `98` | Surrogate PK for a seat. The label is `DistrictName` on a member, `Title` on a district |

**`MemberID` and `VoteMemberID` are not interchangeable, and one LIS field
mixes them up.** See [The `VoteStatement.VoteMemberID`
trap](#the-votestatementvotememberid-trap).

## Bill identity & carry-over

Virginia's even-year sessions may carry unfinished bills over into the
following odd-year session — never the reverse, never twice, and never across
a two-year General Assembly term. The API models this by **reusing the
`LegislationID`** (confirmed against the live API, 2026-08-12):

- A carried-over bill appears in **both** sessions' `get_session_bills` lists
  with the same `LegislationID` (e.g. HB9, ID `98631`, in `20261` and `20271`).
- `get_bill(legislation_id)` returns top-level `SessionCode`/`SessionID` as
  `None`. The `Sessions` list carries one cross-ref per session the bill
  appears in — both the even and odd session for a carried-over bill — and is
  the explicit lineage record.
- So `LegislationID` identifies the *logical bill within a GA term*: at most
  two session appearances (even, then odd if continued). Session-scoped
  identity is `(SessionCode, LegislationNumber)` or
  `(SessionCode, LegislationID)`.
- An odd-year bill list starts as pure carry-over — as of Aug 2026, `20271`
  returned 443 bills, every one sharing its ID with `20261`. Continued bills
  must be acted on by mid-November of the even year or they die, so expect
  that set to shrink before the odd session convenes.

**Texts and events are entity-level too.** A bill's text versions and history
events belong to the logical bill, not to a session appearance, so they follow
it across the carry-over with their ids unchanged: `get_bill_texts("HB9", 20261)`
and `get_bill_texts("HB9", 20271)` return the identical record
(`LegislationTextID=257905`, the introduced text — verified live 2026-08-12).
A text id never mutates. When the text changes, LIS appends a **new** version
record with a new id (`HB9` → `HB9E` → `HB9H1` → `HB9ER`); a carried bill
brings its whole existing stack into the new session and appends any odd-year
amendments to it. Event histories behave the same way — the odd-year view
includes every even-year event under its original `LegislationEventID`.

**Sync warning:** if you mirror bills into a database, don't put a global
unique constraint on any of these ids. The first odd-year sync will violate a
global `LegislationID` constraint on the first carried-over bill, violate a
global `LegislationTextID` constraint on its first text version, and — worst —
an upsert keyed on a globally-unique `LegislationEventID` won't error at all:
it silently re-parents the even-year rows to the odd-year bill. Scope every
LIS surrogate to its owning parent: `(session, LegislationID)`,
`(bill, LegislationTextID)`, `(bill, LegislationEventID)`.

**Default session:** `get_default_session()` tracks the GA's *working*
session, not the last one convened. Once a session wraps up (sine die, veto
session, enactments effective July 1), the default advances to the upcoming
session during the interim — by Aug 2026 it was already `20271`, which is
where continued bills and (from mid-November) new prefiles accumulate. For
retrospective work on a just-ended session, pass its explicit session code.

## Response envelope gotchas

The docs say `ListItems` everywhere. Reality:

| Endpoint | Docs say | Actual key |
|---|---|---|
| Sessions | `ListItems` | `Sessions` |
| Legislation list | `ListItems` | `Legislations` |
| Legislation detail | `ListItems` | `Legislations` |
| Legislation statuses | `ListItems` | `References` |
| Text list | `ListItems` | `LegislationTextList` |
| Text detail | `ListItems` | `TextsList` |
| Summaries | `ListItems` | `LegislationSummaries` |
| Version refs | `ListItems` | `LegislationVersionList` |
| Partner check | flat | `PartnerList` |
| Calendars | `ListItems` | `Calendars` |
| Dockets | `ListItems` | `Dockets` |
| Schedules | `ListItems` | `Schedules` |
| Committees | `ListItems` | `Committees` |
| Legislation events | `ListItems` | `LegislationEvents` |
| Schedule types | `ListItems` | `ScheduleTypes` |
| Meeting rooms | `ListItems` | `MeetingRooms` |

## Pagination (undocumented)

Paging works but is documented nowhere, and it is **not** a query string
parameter. Every parameter name is silently ignored. It runs on an
`X-Pagination` request header carrying JSON:

```bash
curl -H "WebAPIKey: $LIS_API_KEY" \
     -H 'X-Pagination: {"PageSize":3,"SkippedRecords":6}' \
     "https://lis.virginia.gov/Legislation/api/getlegislationsessionlistasync?sessionCode=20271"
```

| Request field | Effect |
| --- | --- |
| `PageSize` | Caps the number of rows returned |
| `SkippedRecords` | Raw record offset. This is what moves the window |
| `PageNumber` | Accepted and ignored. Always echoed back as `1` |

Every response carries an `X-Pagination` header back. `CurrentPage` is derived
from `SkippedRecords` divided by `PageSize`:

```json
{"PageSize":3,"PageNumber":1,"TotalPages":148,"TotalCount":443,
 "CurrentPage":3,"SkippedRecords":6,"HasPrevious":true,"HasNext":true}
```

### Using it

```python
page = client.get_session_bills(session_code=20271, page_size=5, skip=10)

len(page)                      # 5. PagedList subclasses list.
page[0].LegislationNumber      # "HB71"
page.pagination.TotalCount     # 443
page.pagination.HasNext        # True

# TotalCount without downloading the list: one row, about a kilobyte.
client.get_session_bill_count(session_code=20271)   # 443
```

`PagedList` is a real `list`, so code that iterates or indexes it needs no
change. `.pagination` is `None` when the server sends no usable header.

Worth using: an unpaged row is about 820 bytes, so a full regular session runs
to roughly 3 MB.

### Which endpoints support it

Verified on `getlegislationsessionlistasync` only. It is **not** global
middleware. The event type reference endpoint ignores the header and returns
all 3,912 rows (2.19 MB) on every call, which is why the client caches that one
instead.

## API services

### Currently implemented

#### Session (`/Session/api/`)
- `get_sessions(year)` → list of sessions for a year
- `get_default_session()` → the current/active session

#### Legislation (`/Legislation/api/`)
- `get_session_bills(session_code)` → lightweight bill list for a session
- `get_bill(legislation_id)` → full bill detail with all patrons
- `get_legislation_statuses()` → all 52 status reference entries

#### LegislationText (`/LegislationText/api/`)
- `get_legislation_versions()` → 13 version type references (Introduced, Enrolled, etc.)
- `get_bill_texts(legislation_number, session_code)` → text version list
- `get_bill_text_detail(legislation_id, session_code)` → full HTML bill text

#### LegislationSummary (`/LegislationSummary/api/`)
- `get_bill_summaries(legislation_number, session_code)` → bill summaries (HTML)

#### LegislationEvent (`/LegislationEvent/api/`)
- `get_bill_events(legislation_id)` → chronological action history for a bill
- `get_event_types()` → 3,912 event type references
- `get_actor_types()` → 5 actor types (House, Senate, Committee, etc.)

**Event join keys** (verified 2026-08-13): an event's `Status` carries the
internal *name* from the 52-status vocabulary (a closed vocabulary, not
free text), and `LegislationStatusID` comes back null — join statuses on
the name. The event-type reference rows come back with
`LegislationEventTypeID` null — join event types on `EventCode`. 120 of
the 3,912 types are the committee continuance family (codes ending
40/41/42, e.g. `H1940` = Continued to next session in Transportation);
floor and conference continuances are covered by statuses 46–48 instead.
`EventDate` is the true action date — a committee continuance is dated on
the committee vote, not at crossover.

#### Joining the event vocabulary

`EventCode` is the join key onto the event-type reference, but **a code does
not identify a row**. Measured 2026-09-08: 3,912 rows carry only 1,502
distinct codes, and 1,326 codes repeat.

The repeats are not harmless copies. `H1405` returns four rows:

| `LegislationChamberCode` | `IsPassed` | `LegislationDescription` |
| --- | --- | --- |
| `H` | `true` | Reported from Labor and Commerce |
| `S` | `true` | Reported from Labor and Commerce |
| `H` | `false` | Failed to report (defeated) in Labor and Commerce |
| `S` | `false` | Failed to report (defeated) in Labor and Commerce |

A `dict[str, LegislationEventType]` keyed on the code keeps whichever row
arrives last, so it can report a bill as **defeated when it passed**.

`(EventCode, LegislationChamberCode, IsPassed)` is the real primary key: it
yields 3,912 distinct keys for 3,912 rows, with no collisions.

Two traps in that key:

- **`LegislationChamberCode` is the bill's chamber, not the actor's.** A
  Senate bill reported from a House committee carries the House actor code
  `H1405` against a row whose chamber is `S`. The event's own `ChamberCode`
  mirrors the code prefix, so it names the actor and cannot serve here. Take
  the chamber from the event's `LegislationNumber` instead.
- **`IsPassed` separates the twins.** Fixing the code and chamber still leaves
  the pass/fail pair.

Fixing `(EventCode, IsPassed)` and flipping only the chamber changes
`LegislationDescription` for just 7 codes, five of which differ by a typo or
a trailing space (`Privilegse` for `Privileges`). The two real ones are
`G7210` (received by House versus Senate) and `H4620` (`Printed as engrossed`
versus `Printed as second chamber engrossed`). `CalendarDescription` differs
across the chamber pair far more often, in 148 codes.

`LISService.event_type_for(event)` applies all of this.

#### Vote (`/Vote/api/`)
- `get_vote(vote_id)` → one vote with its per-member roll call
- `get_vote_types()` → 3 vote types (1=Committee, 2=Subcommittee, 3=Floor)

**This service is absent from the LIS developer portal service list.** It is
the only route to per-member votes. See [Votes](#votes-per-member-roll-calls).

#### Member (`/Member/api/`)
- `get_members(session_code, chamber_code=None)` → the session roster, with party and district
- `get_member(member_id, session_code)` → one member, **204s for some sitting members**
- `get_parties()` → 3 parties (D, I, R)
- `get_districts()` → 140 districts (100 House, 40 Senate)

`MemberID` is the join key from a ballot. See
[Members](#members-the-roster-behind-a-roll-call).

#### MemberVoteSearch (`/MemberVoteSearch/api/`)
- `get_member_votes(member_id, session_code)` → every vote one member cast

The member-first axis, where `/Vote` is bill-first. See
[The member-first axis](#the-member-first-axis).

#### LegislationByMember (`/LegislationByMember/api/`)
- `get_member_legislation(member_id, session_id, patron_type_id=None)` → every bill a member patrons, in any role

**Takes a `session_id`, not a session code**, because the endpoint caches
session codes wrongly. See [Bills by member](#bills-by-member).

#### LegislationPatron (`/LegislationPatron/api/`)
- `get_bill_patrons(legislation_id)` → every patron of a bill, in every role
- `get_patron_roles()` → 5 roles (1 Chief Patron, 2 Chief Co-Patron, 3 Incorporated Chief Co-Patron, 4 Co-Patron, 5 Offered)

Not wired: `getlegislationpatronlistasync` serves chief patron relationships
only (any other `patronType` answers 204) and unfiltered returns every chief
patron in a chamber with their bills, 1.3 MB for the 2026 House;
`getmemberpatrontypelistasync` lists the roles one member holds, including a
`PatronTypeID` of `0` for budget amendment requests.

#### Committee (`/Committee/api/`)
- `get_committees(chamber_code=None, include_subcommittees=False)` → 14 House and 11 Senate standing committees; 67 House rows with subcommittees
- `get_committee(committee_id, session_id)` → one committee, with `MeetingNote` and `EffectiveBeginDate`
- `get_committee_by_number(committee_number)` → the same by number, e.g. `"H14"`

The list ignores the session it is sent. See
[Committees and seats](#committees-and-seats).

#### MembersByCommittee (`/MembersByCommittee/api/`)
- `get_committee_members(committee_id, session_code)` → who sits on a committee, with roles; session required
- `get_committee_roles()` → 8 roles, with chamber specific IDs

#### CommitteeLegislationReferral (`/CommitteeLegislationReferral/api/`)
- `get_committee_actions()` → 41 committee actions

Despite the name, this service does not list the bills referred to a
committee. The action vocabulary is its only data operation.

#### Schedule (`/Schedule/api/`)
- `get_schedules(start_date, end_date, owner_id=None, schedule_type_id=None, vote_room_id=None)` → meetings in a date range; always pass dates
- `get_schedule_types()` → 6 types (1 Committee, 2 Chamber, 3 Conference, 4 Caucus, 5 Other, 6 Docket)
- `get_meeting_rooms(chamber_code=None)` → room reference, 18 for the House

Not wired: `previewvcalfileasync`, a vCal export.

#### Calendar (`/Calendar/api/`)
- `get_calendars(chamber_code, session_code)` → a chamber's floor calendars, 56 for the 2026 House
- `get_calendar(calendar_id)` → one calendar with categories, agendas, and per-member vote rows
- `get_dockets(committee_id, session_code)` → a Senate committee's dockets; a House committee returns nothing
- `get_dockets_by_committee_number(committee_number, session_code)` → the same by number, e.g. `"S13"`
- `get_docket(docket_id)` → one docket with its bills, members, staff, and linked schedule
- `get_calendar_types()` → 2 types (1 Chamber, 2 Committee)
- `get_calendar_category_types(chamber_code=None)` → 98 category codes, e.g. `CSGEN` = Senate Bill in Committee

Not wired: `getcalendaractionsreferenceasync` returns 4,952 rows and 2 MB, and
`getcategorytypesreferenceasync` is unprobed. See
[How meetings and dockets work](#how-meetings-and-dockets-work).

### Not yet explored

These services exist in the portal but haven't been investigated, except
where noted:

- AdvancedLegislationSearch (spec read 2026-09-11: a POST keyword search,
  `getmostfrequentlegislationsasync`, introduction date lists; not wired)
- CommunicationFileGeneration
- Contact
- LegislationCollections
- LegislationCommunications
- LegislationFileGeneration
- LegislationSubject (spec read 2026-09-11: `getsubjectreferencesasync`, a
  subject vocabulary per session; not wired)
- MemberVoteSearch (only `getmembervotelistasync` is wired)
- MinutesBook
- Organization
- Person
- Personnel

## Members (the roster behind a roll call)

A ballot names a member by `MemberID` and a display name. Party and district
live in the `Member` service, and `MemberID` joins the two directly: all 100
ballots on House vote 294006 resolve against the 2026 roster.

`LISService.roll_call` does that join. `members_by_id(session_code)` exposes
the cached roster if you want to do it yourself.

### The roster is session scoped, and larger than the chamber

`getmembersasync` **requires** `sessionCode` and returns HTTP 400 without it.
The rosters genuinely differ: 148 rows for 20261, 140 for 20251, 142 for
20241, with only 123 members shared between 2026 and 2024.

Session 20261 returns 148 rows for 140 seats, 106 House against 100 and 42
Senate against 40. That is correct, not a bug. The extras are members who left
or arrived mid-session, plus one member who moved from the House to the Senate
and so appears under two member numbers.

**Do not filter on `MemberStatus`.** Delegate Barry Knight (`MemberID` 217)
voted on HB1 on 3 February 2026 and his service ended on 17 February with
reason `"Deceased"`. Drop the non-active rows and that vote loses its voter.

### Other traps

**`MemberStatusID` on the roster is the member's *previous* status.** The
code itself is sound, and every `getmemberbyidasync` response agrees on it:
`1` Active, `2` Inactive, `3` Outgoing. The roster list breaks it two ways.
It omits the ID on 62 of 148 rows. And on a member who has left it keeps the
status they held before leaving while refreshing the name:

| Member | `MemberStatus` | list `MemberStatusID` | by-id | `ServiceEndDate` |
|---|---|---|---|---|
| Barry D. Knight | Inactive | 1 (Active) | 2 | 2026-02-17 |
| Ghazala F. Hashmi | Inactive | 1 (Active) | 2 | 2026-01-17 |
| Mark D. Sickles | Outgoing | 1 (Active) | 3 | 2026-01-17 |
| Adam P. Ebbin | Outgoing | 1 (Active) | 3 | 2026-02-18 |

All six rows whose list ID contradicts their own name look like this. Read
the name, exactly as
[events join their status on the name](#legislationevent-legislationeventapi).

### Which member endpoint to use

There are three, and they are incomplete in **different dimensions**. Neither
of the two useful ones is simply the better one.

| | `getmembersasync` | `getmemberbyidasync` | `getmemberlistasync` |
|---|---|---|---|
| Rows | **all 148** | 1, and **9 of 148 return nothing** | all 148 |
| Fields | 28, nine always null | 29, five null | 16 |
| Wired as | `get_members` | `get_member` | not wired |

**The list is complete in rows and incomplete in fields.** It returns every
member every time, but nine fields come back null on all 148 rows:
`ChamberName`, `SeatNumber`, `VotingSequence`, `SessionID`, `SessionCode`,
`Salutation`, `Seniority`, `StatusReason`, and `LastElectionDate`. Note
`SessionCode` is null even though the query is session scoped, the same shape
as the carry-over trap.

**By-id is complete in fields and incomplete in rows.** It fills four of those
nine — `ChamberName`, `SeatNumber`, `VotingSequence`, `SessionID` — and adds
`MemberDetailID`. The other five are null there too. But it answered 204 for 9
of the 148 members on the 2026 roster, including sitting ones.

**Build on the list, because the two failures are not equally bad.** A missing
row is unrecoverable: no name, no party, no district, and no error telling you
so, which leaves that member's votes unattributable. A null field costs almost
nothing, because the list still carries `MemberID`, name, `PartyCode`,
`DistrictName`, `ChamberCode`, and the service dates — everything a voting
record needs. What by-id adds is thin: `ChamberName` restates `ChamberCode`,
`SessionID` is what you passed in, and `SeatNumber` and `VotingSequence` are
seating-chart trivia. `LISService.roll_call` therefore reads the roster once
and never calls by-id.

**The one field where the list is wrong and by-id is right** is
`MemberStatusID`, above. So: the list is authoritative for which members exist
and for every field it populates except that one; by-id is authoritative for
`MemberStatusID` and the four extra fields, when it answers.

**The 204s are not a key problem.** Passing `identityID` 204s as well, and
passing an `IdentityID` as `memberID` returns a *different* member, so the
parameter is right. Every person holding two member records fails, which
accounts for the four who moved from the House to the Senate mid-term and so
appear under two member numbers. The remaining five have a single record and
share no trait found so far. All nine 204 in every session tried, so it is a
property of the member rather than the query.

**Whitespace dirt, worse than anywhere else in the API — the client strips
it for you.** Padded strings appear across unrelated fields, and which
fields carry the padding moves between sessions:

| Field | 20241 | 20251 | 20261 | 20271 |
|---|---|---|---|---|
| `GABEmailAddress` | 57 | 57 | 50 | 45 |
| `ListDisplayName` | 1 | 1 | 4 | 4 |
| `MemberDisplayName` | 0 | 0 | 3 | 3 |
| `RoomNumber` | 1 | 0 | 0 | 0 |

Chasing that with a per-field accessor loses to the next field LIS pads, so
`LISModel` strips every string at validation. `MemberNumber == "H0386"` and
`ResponseCode == "Y"` hold whatever LIS sends. `Member.name`, `.list_name`,
and `.email` remain as shorthand that never returns `None`.
Bill detail pads patron names too: `getlegislationbyidasync` returned
`" Charlie Schmidt"` as chief patron of HB1408 in 20261. Every response
model therefore inherits `LISModel` now, not only the roster and vote models.

**`getmemberlistasync` is strictly worse than both.** It returns the same 148
rows under `ShallowMembers` with only 16 fields, dropping `DistrictID`,
`DistrictName`, `RoomNumber`, and `ServiceEndDate`, so it can neither back a
voting record nor tell you who left. It has no advantage over
`getmembersasync`, which accepts the same undocumented `chamberCode` filter,
so this client does not wire it.

**A district labels itself `Title`; a member calls the same value
`DistrictName`.** Both read `"71st"`.

## Bills by member

`/LegislationByMember` answers "what does this member patron", in every role.
The bill-first list names only the chief patron, so this is the only route to
co-patronage short of one detail call per bill. `find_members` turns a name
into a `MemberID` first; it searches the cached roster, so it costs nothing
after the first call for a session.

```python
schmidt = service.find_members("schmidt", 20261)[0]
for b in service.member_bills(schmidt.MemberID, 20261):
    print(b.LegislationNumber, b.LegislationStatus, b.SummaryVersion)
chief = service.member_bills(schmidt.MemberID, 20261, role=CHIEF_PATRON)
```

Delegate Schmidt's 2026 list: 229 bills, 11 of them as chief patron.

### Send `sessionID`, never `sessionCode`

The endpoint accepts both, and `sessionCode` is a trap. Every response
carries a `CacheKeyName`, and it reveals the server's cache key:

| Call | Cache key |
|---|---|
| `memberID=544&sessionCode=20261` | `{MEMBERID=544}` |
| `memberID=544&sessionID=59` | `{MEMBERID=544}{SESSIONID=59}` |

A `sessionCode` call is honored on a cache miss, and the answer is then
stored under the member alone. Every later `sessionCode` call for that
member, from any partner, gets the first session's rows back. Member 186
queried with `sessionCode=20251` and then `20271` returned the same 225 rows
of session 57 both times, while `sessionID=61` returned 25 rows of session 61
(verified 2026-09-11). `LISClient.get_member_legislation` therefore takes a
`session_id`, and `LISService.session_id(session_code)` resolves the code
from the session reference. Do not omit the session either: with no
parameters at all the server tries to list every bill for every member, and
the request hangs past 60 seconds.

### One row per summary version

A bill appears once per published summary. HB18 came back three times, as
introduced, as passed chamber, and as passed, so 236 rows described 229
bills. `member_bills` keeps the last row per `LegislationID`, which carries
the newest summary, and preserves the order of first appearance.
`LISClient.get_member_legislation` returns the rows as sent.

### The nested patron is not the patron list

`Patrons` on these rows is empty on 177 of 236 and holds one entry on the
rest: the chief patron, in a thin shape with no `LegislationID`,
`ChamberCode`, `MemberNumber`, or `Name`, and a `PatronDisplayName` of
`"Cole, J.G."` rather than `"Cole"`. The member being listed never appears in
it. Those four fields are therefore optional on `Patron`, and `Patron.role`
names the role on every shape. For the real list call
`bill_patrons("HB1408", 20261)`, which returns 12 rows for that bill.

## Committees and seats

`/Committee` lists the committees, and `/MembersByCommittee` says who sits on
one in a session. Neither is member-first, so `member_committees` walks a
chamber's committees and reads every seat list, all cached for an hour.

```python
courts = service.resolve_committee("Courts of Justice", "H")
for s in service.committee_members(courts.CommitteeID, 20261):
    print(s.name, s.role, s.party, s.district)
# Patrick A. Hope   Chair   D   1st
# ...
for s in service.member_committees(544, 20261, include_subcommittees=True):
    print(s.committee.name, s.role)
# Courts of Justice                           Member
# Communications, Technology and Innovation   Member
# HCJ Sub: Criminal                           Member
```

### The committee list is not session scoped

`getcommitteelistasync` accepts a session and ignores it: the cache key reads
`{SESSIONID=0}` for every call, and 20251 and 20261 return the same 14 House
committees. The seat list is the session scoped half, and it requires a
session; omitting it returns HTTP 400. Courts of Justice seats 23 in 20261
and 22 in 20251.

### Role IDs differ by chamber

`getcommitteerolesasync` returns eight rows, and the same title carries a
different ID in each chamber: a House chair is `3`, a Senate chair is `1`, a
House member is `6`, a Senate member is `5`. Compare `CommitteeRoleTitle`,
not the ID. The titles are Chair, Co-Chair (Senate), Vice-Chair (House),
Member, and Ex-Officio; a full committee's chair sits on its subcommittees as
Ex-Officio.

### Two seat shapes, and padded subcommittee names

The seat list names the role in `CommitteeRoleTitle` and carries no party. The
seat nested in docket detail names it in `Title` and does carry `PartyCode`.
`CommitteeMember.role` reads either. `committee_members` joins the session
roster for party and district instead of trusting either row.

A subcommittee number is its parent's number plus a three-digit sequence
(`H08001`), and subcommittee names arrive padded inside: `"HAPP     Sub:
Commerce Agriculture & Natural Resources"`. The model strips only the ends, so
`Committee.name` collapses the middle.

### There is no "bills in committee" endpoint

`CommitteeLegislationReferral` sounds like one, but its only data operation is
the 41-row action vocabulary. The bill list says "In Committee" without naming
the committee, and bill detail nulls `CommitteeName`. The events carry it:
read `LegislationEvent.CommitteeName` on a bill's history for the committee it
sits in.

## The member-first axis

`/Vote` answers "who voted on this bill". `/MemberVoteSearch` answers the
other direction: "how did this member vote on everything". The two agree —
`member_votes_on(503, "HB1", 20261)` and `roll_call("HB1", 20261)` return the
same `VoteID` values with the same responses, and a live test asserts it.

```python
for v in service.member_votes(503, 20261):
    print(v.bill_number, v.response, "BLOCK" if v.is_block else "")
```

Delegate Anthony's 2026 record: **2,867 bill positions over 2,250 distinct
votes**, 2,179 floor, 437 committee, 251 subcommittee.

### A row is a (vote, bill) pair, not a vote

A block vote repeats, once per bill it disposed of. One House vote in that
record — `VoteID` 297750, "Read third time and passed House (97-Y 0-N 0-A)" —
appears **105 times**, once for each bill it passed. So `len(rows)` counts
bill positions and overstates how often a member voted. Count distinct
`VoteID` for votes cast.

**This endpoint carries no `IsBlock` flag**, unlike a `Vote` record. The
service recovers it by counting the rows that share a `VoteID`, and the count
is exact: for vote 297750 it derives 105, and `Vote.vote_legislation` also
holds 105. Read `MemberVote.is_block` before treating a row as the member's
verdict on that one bill.

### `ClassificationName` is not the legislation filter

It takes three values, and only one of them means "not a bill":

| `ClassificationName` | Rows | Has a bill? | What it is |
|---|---|---|---|
| `Legislation` | 2,179 | yes | floor votes on bills |
| `null` | 688 | **yes** | committee and subcommittee votes on bills |
| `Attendance` | 43 | no | quorum roll calls |

**Filter on `LegislationNumber`.** Filtering on
`ClassificationName == "Legislation"` silently drops every committee and
subcommittee vote the member cast — 688 of them here, nearly a quarter of the
record. `member_votes` filters correctly and takes `legislation_only=False`
if you want the attendance rows too.

### Other notes

**It is the heaviest response in the API**, roughly 1.9 MB per member, and you
cannot ask for several members at once. Both `memberID` and `sessionCode` are
required, and omitting either returns HTTP 400. `chamberCode` is accepted and
silently ignored — it returns a byte-identical response. `LISService` caches
the payload per member and session for `roster_ttl`.

**`VoteStatement` the field collides with `VoteStatement` the model**, so the
scalar is aliased to `vote_statement` on the Python side. It is always null in
practice; the real corrections are in `VoteStatements`, whose `VoteMemberID`
holds a `MemberID` like everywhere else.

## Votes (per-member roll calls)

The `/Vote` service is the only route to who voted how. It covers **both
chambers**, and **committee, subcommittee, and floor** votes alike.

**There is no bill-first endpoint.** Every `getvotesby...legislation...` name
returns 404. Reach a vote through an event:

```
Legislation ──→ LegislationEvent.VoteID ──→ /Vote/api/getvotebyidasync
                                              └── VoteMember[]  (the roll call)
                                              └── VoteLegislation[] (bills covered)
                                              └── VoteStatements[] (corrections)
```

`LISService.bill_votes(bill_number, session_code)` does that walk. It costs
one request per vote.

### A VoteID on a bill event is often not that bill's roll call

Three flags decide whether you may attribute the members to the bill:

| Flag | Meaning | Example |
|---|---|---|
| `IsVoice` | **No members recorded at all.** `VoteMember` is empty and `VoteTally` reads `(Voice Vote)` | vote 300174 |
| `IsBlock` | One roll call disposing of **many bills at once**. Read `vote_legislation` for the count | vote 300173: 40 members, **50 bills** |
| `IsPublic` | False on some block and voice votes | vote 300174 |

A voice vote is not always a block: SB1 has a single-bill voice vote. Check
both flags, not one.

`BillVote.is_roll_call` applies this test, and
`bill_votes(..., roll_calls_only=True)` filters on it. HB1 in 20261 has eight
votes; six survive the filter.

#### What a block vote actually is

A chamber does not vote on every bill separately. It bundles the
uncontroversial ones and disposes of them in a single motion. Members vote
once, and that one vote passes the whole bundle.

Vote 297750 is the extreme case in the 2026 session:

```
Date         2026-02-17
Chamber      H, Floor
Description  Read third time and passed House (97-Y 0-N 0-A)
IsBlock      True
members       99 recorded  (97 Y, 2 X)
bills        105 disposed
```

97 delegates said yes and nobody said no. **That lopsidedness is the
signature.** Anything contested gets pulled out and voted on by itself, so
blocks and single-bill votes sit side by side on the same day:

```
voteID   block  bills  tally
294005    True     27  (98-Y  0-N 0-A)    the bundle
294007   False      1  (93-Y  5-N 0-A)    pulled out
294021   False      1  (63-Y 35-N 0-A)    pulled out, genuinely fought
294054    True     19  (39-Y  0-N 0-A)    a Senate bundle
```

**Every bill still gets its own event off the shared vote.** LIS writes a
separate `LegislationEventID` per bill, all pointing at the one `VoteID`. So a
bill's history looks like an ordinary passage, and the `VoteID` on it is
shared with 104 other bills.

**Do not try to spot this in the description text.** Only 9 of vote 297750's
105 bill events say "Block Vote"; the other 96 read like an ordinary
individual passage:

```
  96x  'Read third time and passed House (97-Y 0-N 0-A)'
   9x  'Read third time and passed House  Block Vote (97-Y 0-N 0-A)'
```

Vote 294005 labels 1 of 27, and vote 294054 labels 2 of 19. Test `IsBlock`.

**Why this matters for reporting.** "Delegate Anthony voted Yes on HB150" is
true and misleading. She voted yes to a bundle of 105 bills that nobody in the
chamber opposed, which is not a considered position on HB150. That is why
`roll_call` excludes block votes and `MemberVote.is_block` exists.

### Who voted which way

`BillVote.responses()` groups the members by `ResponseCode`:

| Code | Meaning |
|---|---|
| `Y` | yea |
| `N` | nay |
| `A` | abstain |
| `X` | **not voting** |

`X` is confirmed by a vote statement that reads "Delegate Knight was recorded
as not voting" against Knight's `X` row on vote 294006.

Each member carries `MemberID`, `MemberNumber` (e.g. `H0206`),
`MemberDisplayName`, and `PatronDisplayName` (the surname alone). Use
`VoteMember.name` rather than `MemberDisplayName`: LIS ships leading-space
dirt on some rows, 2 of the 100 on vote 294006.

Party and district are **not** in the roll call. They live in the `Member`
service, which is probed but not yet wired.

```
HB1  House floor  2026-02-03  (64-Y 34-N 0-A)

  yea (64)
     H0386  Jessica L. Anderson
     H0353  Bonita G. Anthony
     H0253  Terry L. Austin      <-- recorded as yea. Intended to vote nay.
     ...
  nay (34)
     H0333  Jason S. Ballard
     ...
  not voting (2)
     H0370  Karen Keys-Gamarra
     H0206  Barry D. Knight      <-- recorded as not voting. Intended to vote nay.
```

### Other traps

**The tally string omits `X`.** House floor vote 294006 returns 100 member
rows — 64 `Y`, 34 `N`, 2 `X` — against a tally of `(64-Y 34-N 0-A)`. Summing
the members will not reproduce the tally, and the `X` members are neither yes,
no, nor the abstentions the tally counts.

**`VoteStatements` carries corrections the roll call does not reflect.** Vote
294006 holds two, including "Delegate Austin was recorded as yea. Intended to
vote nay." The member rows are **not** amended. Both facts are true, and only
the recorded one counts. Surface the statements alongside any voting record
you publish.

### The `VoteStatement.VoteMemberID` trap

**`VoteStatement.VoteMemberID` does not hold a `VoteMemberID`. It holds a
`MemberID`.** The obvious join matches nothing, returns an empty result, and
raises no error.

Every row in a vote's `VoteMember` list carries two different IDs:

| Column | Identifies | Stable across votes? |
|---|---|---|
| `MemberID` | The **person** | Yes. Barry Knight is `217` everywhere |
| `VoteMemberID` | The **ballot** — one person's response on one vote | No. New every vote |

The same delegate on two different votes:

```
bill  voteID   MemberID  VoteMemberID  response
HB1   294006   217       10988439      X
HB5   297524   217       11092143      X
```

Now the two statement rows on vote 294006:

```
VoteStatementID  VoteMemberID  VoteStatement
126              17            Delegate Austin was recorded as yea. Intended to vote nay.
224              217           Delegate Knight was recorded as not voting. Intended to vote nay.
```

And the member rows they concern:

```
MemberID  VoteMemberID  ResponseCode  name
17        10988428      Y             Terry L. Austin
217       10988439      X             Barry D. Knight
```

The statement says `217`. That is Knight's **`MemberID`**, not his
`VoteMemberID` of `10988439`.

```python
statement.VoteMemberID == member.VoteMemberID   # WRONG — never matches
statement.VoteMemberID == member.MemberID       # right
```

The ranges do not overlap at all, so the wrong join fails silently for every
statement on every vote: statement IDs on this vote are 17 and 217, while the
ballot IDs run from 10988428 to 10988537.

`BillVote.statements_by_member()` applies the correct join and keys the result
by `MemberID`. Without it, the only link from a correction to a member is the
surname inside the English sentence.

**The vote's `EventCode` can disagree with the event's.** Vote 294006 reports
`H9999`; the event pointing at it reports `H5000`. Join on `VoteID` alone.

**Smaller ones.** `VotingSequence` appears on committee votes and is absent on
floor votes. `VoteFile.TextFormatID` is a **string** (`"5"`) here, where
`CalendarFile.TextFormatID` is an int. `voteID=0` returns HTTP 400
`"Failed, Database Error (51000)"` rather than an empty result; an
out-of-range ID returns a clean 204.

## How meetings and dockets work

```
Committee ──→ Docket (Senate) or Calendar (House) ──→ Schedule ──→ Room
              │                                        │
              │ has DocketItems/Agendas               │ has ScheduleDate,
              │ (which bills are up)                   │ ScheduleTime (free-text!),
              │                                        │ RoomDescription,
              └────────────────────────────────────────│ IsCancelled
                                                       └──→ VoteRoom reference
```

- **Schedule** is the "when and where", but `ScheduleTime` is often free text
  like "15 minutes after adjournment"
- **Docket/Calendar** is the "what": which bills are on the agenda
- **Committee** is the "who": membership, chair, staff

```python
# Senate: every bill on every docket of a committee, one request per docket.
for e in service.docket_entries("Courts of Justice", 20261):
    print(e.date, e.bill_number, e.room)

# Both chambers: a week of committee meetings, cancelled ones included.
for m in client.get_schedules("2026-02-02", "2026-02-06", schedule_type_id=1):
    print(m.ScheduleDate, m.ScheduleTime, m.OwnerName, m.IsCancelled)

# House: a floor calendar's bills, and the votes taken on each.
calendar = client.get_calendar(client.get_calendars("H", 20261)[0].CalendarID)
for agenda in calendar.bills:
    print(agenda.LegislationNumber, [i.VoteID for i in agenda.AgendaItems])
```

**Always give the schedule a date range.** Unfiltered, `getschedulelistasync`
returns every meeting it holds: 3,631 rows and 2 MB from October 2022 to
December 2026. The week of 2026-02-02 is 147 rows, 8 of them cancelled and 35
with a blank `ScheduleTime`. Rows that are not committee meetings (caucuses,
press events) omit `OwnerID` and `CommitteeNumber` from the JSON entirely.

**Dockets are Senate only, and a docket and its schedule can disagree on the
hour.** A House committee answers 204 on the docket list, which the client
returns as an empty list. Senate Courts of Justice has 16 dockets in 20261.
Docket 21123 carries `DocketDate` `2026-03-09T16:30:00` while its linked
schedule reads `8:00 AM` for the same day, and the API does not say which is
right. The docket detail envelope also reports `Success: false` with a null
message on a complete response; the client ignores the flag.

**House committee agendas are not in the API.** The calendar list returns floor
calendars only (all 56 of the 2026 House list are type `Chamber`), and no
docket exists for a House committee, so "when will House committee X hear
bill Y" has no endpoint as probed on 2026-09-11. The schedule gives the
meeting, the bill's events give the referral, and the agenda itself lives on
the committee's web page.

**The `calendarDate` filter answers 204.** `get_calendars` fetches the whole
list, which is small, and leaves the date filter to you.

## How a bill becomes law (Virginia)

A bill's lifecycle through the General Assembly, mapped to LIS status IDs.
Virginia has a bicameral legislature (House of Delegates + Senate).  A bill
must pass both chambers in identical form before going to the Governor.

### The happy path

```
                      ORIGINATING CHAMBER
                      ───────────────────
┌─ Prefiled (Nov–Jan) ──→  Introduced (1)
│                              │
│                              ▼
│                        In Committee (2)
│                         ┌────┴────┐
│                         │         │
│                    In Subcommittee  Left In Committee (20)
│                       (19)         = dead, session ends
│                         │
│                         ▼
│                     Reported Out (3 or 52)
│                     Committee votes to send
│                     bill to the full chamber
│                              │
│                              ▼
│                      Floor vote in originating chamber
│                      Passed House (4) or Passed Senate (5)
│
│                       CROSSING OVER
│                       ─────────────
│                     Bill goes to the other chamber
│                     and repeats: Committee → Floor
│                              │
│                              ▼
│                      Passed Both Chambers (6, 39, 40)
│                      = "Enrolled" version created (ER)
│                              │
│                              ▼
│
│                        GOVERNOR
│                        ────────
│                     Communicated (13) → With Governor (7)
│                              │
│                              ▼
│                     Awaiting Governor's Action (38)
│                         ┌────┼────────────┐
│                         │    │             │
│                         ▼    ▼             ▼
│                    Approved  Gov's      Gov's Veto
│                      (8)   Recommend.   (24, 26)
│                       │    (23, 27)        │
│                       ▼       │            ▼
│                    Enacted    ▼         Override vote
│                     (36)   Chambers      or bill dies
│                       │    adopt? (33)
│                       ▼       │
│                    Acts of    ▼
│                    Assembly  Back to
│                    Chapter   Enrolled
│                     (25)
└────────────────────────────────────────────────
```

### Key status groups

**Filing & introduction:**

| ID | DisplayName | Internal Name | What happened |
|---|---|---|---|
| 11 | Preview | Preview | Prefiled but session hasn't started yet |
| 1 | Introduced | Introduced | Formally introduced on the chamber floor |

**Committee phase** — where most bills die:

| ID | DisplayName | Internal Name | What happened |
|---|---|---|---|
| 2 | In Committee | In Committee | Referred to a standing committee |
| 37 | Committee Referral Pending | Committee Referral Pending | Awaiting committee assignment |
| 19 | In Subcommittee | In Subcommittee | Referred to a subcommittee |
| 20 | Left In Committee | Left In Committee | Bill was never voted on — effectively killed |
| 41 | In Committee | Continued From | Carried over from a previous session into committee |
| 3 | In House | Reported Out-House | Committee voted to advance (House side) |
| 52 | In Senate | Reported Out-Senate | Committee voted to advance (Senate side) |
| 30 | Incorporated | Incorporated | Merged into another bill |

**Floor votes:**

| ID | DisplayName | Internal Name | What happened |
|---|---|---|---|
| 4 | Passed House | Passed House | Full House approved |
| 5 | Passed Senate | Passed Senate | Full Senate approved |
| 12 | Engrossed | Engrossed | Passed one chamber, snapshot before crossover |
| 28 | Engrossed | Engrossed with Amendment | Same but with amendments applied |
| 29 | Engrossed | Reengrossed with Amendment | Re-amended after engrossment |

**Conference** — when the two chambers passed different versions:

| ID | DisplayName | Internal Name | What happened |
|---|---|---|---|
| 34 | Conference Requested | Conference Requested | One chamber asks for a conference committee |
| 16 | In Conference | In Conference | Conference committee is negotiating |
| 31 | In Conference | Conference Report Agreed | Conferees reached agreement |
| 32 | In Conference | Conference Report Rejected | Conferees' report was rejected |
| 39 | Passed | Conference Report Adopted | Both chambers accepted the conference version |
| 45 | Failed | Failed in Conference | Conferees couldn't agree — bill dies |

**Passed both chambers:**

| ID | DisplayName | Internal Name | What happened |
|---|---|---|---|
| 6 | Passed | Passed Both | Both chambers approved identical text (→ version 3, Enrolled) |
| 40 | Passed | Passed | Generic "passed" status |
| 35 | Enrolled | Enrolled-House | Final enrolled text prepared (House origin) |
| 44 | Enrolled | Enrolled-Senate | Final enrolled text prepared (Senate origin) |

**Governor pipeline:**

| ID | DisplayName | Internal Name | What happened |
|---|---|---|---|
| 13 | Communicated | Communicated | Bill formally transmitted to the Governor |
| 7 | With Governor | With Governor | Governor has received the bill |
| 14 | Awaiting Signature | Awaiting Signature | Waiting for physical signature |
| 38 | Awaiting Governor's Action | Awaiting Governor's Action | Governor is reviewing |
| 8 | Approved | Approved | **Governor signed it** (→ version 4, Chaptered) |
| 36 | Enacted | Enacted | Becomes law (→ version 4, Chaptered) |
| 25 | Acts of Assembly Chapter | Acts of Assembly Chapter | Assigned a chapter number |
| 23 | Governor's Recommendation | Governor's Recommendation | Sent back with proposed amendments (→ version 10) |
| 27 | Governor's Recommendation | Governor's Recommendation | Same (no version link) |
| 33 | Governor's Recommendation Adopted | Gov Recommendation Adopted | Chambers accepted the amendments |
| 24 | Governor's Veto | Governor's Veto | **Governor vetoed** (→ version 9, Veto Explanation) |
| 26 | Governor's Veto | Governor's Veto | Same (no version link) |

**Terminal / carry-over:**

| ID | DisplayName | Internal Name | What happened |
|---|---|---|---|
| 9 | Failed | Failed | Bill defeated on a floor vote |
| 10 | Continued | Continued To | Carried over to next session |
| 46 | Continued | Continued to House | Carried over, sitting in House |
| 47 | Continued | Continued to Senate | Carried over, sitting in Senate |
| 48 | Continued | Continued to Conference | Carried over, in conference |

### Why there are 52 statuses for ~15 states

LIS uses separate status IDs to track *how* a bill entered a logical state.
For example, "In Conference" has four IDs distinguishing whether the conference
just started, the report was agreed, rejected, or continued.  The `Name`
(internal) captures the sub-reason; the `DisplayName` (user-facing) collapses
them.

### Version-linked statuses

Some statuses have a `LegislationVersionID` indicating which bill text version
corresponds to that state:

| Status | Version ID | Version Name |
|---|---|---|
| Passed Both (6) | 3 | Enrolled |
| Approved (8) | 4 | Chaptered |
| Enacted (36) | 4 | Chaptered |
| Engrossed with Amendment (28) | 2 | Engrossed |
| Reengrossed with Amendment (29) | 6 | Reengrossed |
| Governor's Recommendation (23) | 10 | Gov Recommendation |
| Governor's Veto (24) | 9 | Veto Explanation |
| Reenrolled-House (42) | 7 | Reenrolled |
| Reenrolled-Senate (43) | 7 | Reenrolled |

## Rate limiting (optional)

LIS enforces no rate limit, but DLAS tracks usage per key and has revoked keys
after sending warnings (stated September 2026). No threshold is published.
Treat self imposed rate limiting as required in production, even though the
extra is optional to install.

If you're running many workers and want to share a global rate limit across
them, install the `rate-limiting` extra and use `try_acquire_request_permit`:

```bash
pip install "va-lis-client[rate-limiting]"
```

```python
import redis
from va_lis_client.rate_limiter import try_acquire_request_permit

r = redis.from_url("redis://localhost:6379/0")

if try_acquire_request_permit(redis_client=r, rate_limit=100):
    client.get_session_bills(session_code=20261)
else:
    # back off
    ...
```

Or set `REDIS_URL` and `LIS_RATE_LIMIT` environment variables and call with
no arguments — the module picks them up automatically.

## Windows / government CA roots

On Windows, Python's OpenSSL doesn't automatically load system certificates,
which can cause SSL errors when connecting to hosts with enterprise or
government CA roots.  `va-lis-client` includes a `SystemCertSSLAdapter` that
loads the system certificate store automatically, so no manual cert bundling
is needed.

## Package structure

```
src/va_lis_client/
├── __init__.py         # re-exports the client, the service, and the errors
├── client.py           # HTTP client — returns Pydantic models
├── service.py          # LISService — resolution, reference joins, text helpers
├── exceptions.py       # LISError and its subclasses
├── http.py             # SystemCertSSLAdapter + shared requests session
├── rate_limiter.py     # optional Redis-based rate limiter
└── models/
    ├── __init__.py     # re-exports all models
    ├── common.py       # Heartbeat, Partner
    ├── session.py      # Session, SessionEvent
    ├── legislation.py  # Patron, Legislation, LegislationStatus, ...
    ├── text.py         # LegislationTextItem, LegislationTextDetail, ...
    ├── event.py        # LegislationEvent, LegislationEventType, ActorType
    ├── pagination.py   # Pagination, PagedList — the X-Pagination protocol
    ├── committee.py    # Committee, CommitteeMember, CommitteeAction
    ├── schedule.py     # Schedule, ScheduleType, MeetingRoom
    ├── calendar.py     # CalendarDetail, Agenda, ...
    ├── vote.py         # Vote, VoteMember, VoteLegislation, VoteStatement
    ├── member.py       # Member, Party, District
    ├── member_vote.py  # MemberVoteResult — the member-first axis
    └── docket.py       # DocketDetail, DocketItem, DocketCategory, ...
```

## License

MIT
