# va-lis-client

Python client for the **Virginia Legislative Information System (LIS) REST API**.

Built from live API responses — the official OpenAPI specs are unreliable
(response envelope keys, field casing, and parameter behavior all differ from
the docs).

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

# Current session
session = client.get_default_session()

# All bills in that session
bills = client.get_session_bills(session_code=int(session.SessionCode))

# Full detail for a bill
bill = client.get_bill(legislation_id=98525)

# Bill text (HTML) and summaries
texts = client.get_bill_text_detail(legislation_id=98525, session_code=20261)
summaries = client.get_bill_summaries(legislation_number="HB1", session_code=20261)
```

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

## Data restrictions

- Only **2025 and 2026 session data** is authorized via the API.
- Pre-2025 data must come from `legacylis.virginia.gov` CSV downloads.
- No rate limits are documented anywhere.

## Identifier cheat-sheet

Bills are uniquely identified by **session + bill number**, but the API uses
several overlapping identifiers:

| Identifier | Example | Notes |
|---|---|---|
| `SessionCode` | `20261` | Year + sequence. `20261` = 2026 Regular, `20262` = Special I |
| `SessionID` | `59` | Surrogate PK. Either code or ID works as query param |
| `LegislationNumber` | `"HB1"` | **Must be unpadded** — `HB0001` returns 204 |
| `LegislationID` | `98525` | Surrogate PK, globally unique across sessions |
| `DocumentCode` | `"HB1ER"` | Bill number + version suffix |
| `LegislationTextID` | `257719` | PK for a specific text version |
| `CommitteeID` | `14` | Surrogate PK for a committee |
| `CommitteeNumber` | `"H14"` | Chamber prefix + number, e.g. `H14`, `S02` |
| `CalendarID` | `21146` | PK for a House floor calendar |
| `DocketID` | `21114` | PK for a Senate committee docket |
| `ScheduleID` | `3626` | PK for a scheduled meeting |
| `LegislationEventID` | `1561089` | PK for a bill history event |

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
- `get_event_types()` → ~3,900 event type references
- `get_actor_types()` → 5 actor types (House, Senate, Committee, etc.)

### Modeled but not yet wired to client methods

#### Schedule (`/Schedule/api/`)
The master meeting calendar. All committee hearings, caucuses, floor sessions.

| Endpoint | Description |
|---|---|
| `getschedulelistasync` | List meetings by date range, committee, type, room |
| `previewvcalfileasync` | Generate vCal file for calendar integration |
| `getmeetingroomsreferenceasync` | Room reference (by chamber) |
| `getscheduletypesreferenceasync` | Schedule type reference |

Key fields: `ScheduleDate`, `ScheduleTime` (often free-text like "15 minutes
after the Senate adjourns"), `RoomDescription`, `OwnerName` (committee),
`IsCancelled`, `ScheduleType` (Committee/Chamber/Conference/Caucus/Docket/Other).

**Data quality warning:** `ScheduleTime` is frequently free-text, not a
parseable time. The API faithfully reflects whatever the LIS clerks entered.

#### Calendar (`/Calendar/api/`)
Floor calendars (both chambers) and committee dockets (Senate only).

| Endpoint | Description |
|---|---|
| `getcalendarlistasync` | List calendars by session + chamber |
| `getcalendarsbyidasync` | Full calendar with categories, agendas, votes |
| `getdocketlistasync` | List dockets by committee (**Senate only**) |
| `getdocketlistbycommitteenumberasync` | Dockets by committee number (**Senate only**) |
| `getdocketsbyidasync` | Full docket with agenda items, bills, patrons |
| `getcalendaractionsreferenceasync` | Calendar action reference |
| `getcalendarcategorytypesreferenceasync` | Category type reference |
| `getcalendartypesreferenceasync` | Calendar type reference |

**Important:** Dockets are **Senate-only**. The House uses Calendars. Requesting
a docket with `chamberCode=H` returns HTTP 400 with the message
"Dockets can only be for ChamberCode = S".

Docket detail includes:
- `DocketCategories` → `DocketItems` → bills with summaries and patrons
- `CommitteeMember` roster with roles (Chair, etc.)
- `Staff` assignments
- `Schedules` (linked back to the Schedule service for when/where)
- PDF/JSON file downloads

Calendar detail includes:
- `CalendarCategories` → `Agendas` → bills with vote tallies
- `AgendaItems` → `VoteMember` with per-member vote responses (Y/N)
- Order of business (Call to Order, Invocation, etc.)

#### Committee (`/Committee/api/`)

| Endpoint | Description |
|---|---|
| `getcommitteesasync` | Full committee detail (by number or date) |
| `getcommitteelistasync` | Shallow list by session + chamber |
| `getcommitteebyidasync` | Single committee by ID + session |

Key fields: `CommitteeID`, `CommitteeNumber` (e.g. `H14`, `S02`), `Name`,
`ChamberCode`, `Abbreviation`, `MeetingNote`, `ParentCommitteeID` (for
subcommittees).

### Not yet explored

These services exist in the portal but haven't been investigated:

- AdvancedLegislationSearch
- CommunicationFileGeneration
- Contact
- LegislationByMember
- LegislationCollections
- LegislationCommunications
- LegislationFileGeneration
- LegislationPatron
- LegislationSubject
- Member
- MemberVoteSearch
- MembersByCommittee
- MinutesBook
- Organization
- Person
- Personnel

## How meetings/dockets work

```
Committee ──→ Docket (Senate) or Calendar (House) ──→ Schedule ──→ Room
              │                                        │
              │ has DocketItems/Agendas               │ has ScheduleDate,
              │ (which bills are up)                   │ ScheduleTime (free-text!),
              │                                        │ RoomDescription,
              └────────────────────────────────────────│ IsCancelled
                                                       └──→ VoteRoom reference
```

- **Schedule** is the "when and where" — but `ScheduleTime` is often free-text
  like "15 minutes after adjournment"
- **Docket/Calendar** is the "what" — which bills are on the agenda
- **Committee** is the "who" — membership, chair, staff

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
├── __init__.py         # re-exports LISClient, LISClientError
├── client.py           # HTTP client — returns Pydantic models
├── exceptions.py       # LISClientError
├── http.py             # SystemCertSSLAdapter + shared requests session
├── rate_limiter.py     # optional Redis-based rate limiter
└── models/
    ├── __init__.py     # re-exports all models
    ├── common.py       # Heartbeat, Partner
    ├── session.py      # Session, SessionEvent
    ├── legislation.py  # Patron, Legislation, LegislationStatus, ...
    ├── text.py         # LegislationTextItem, LegislationTextDetail, ...
    ├── event.py        # LegislationEvent, LegislationEventType, ActorType
    ├── committee.py    # Committee, CommitteeMember, CommitteeAction
    ├── schedule.py     # Schedule, ScheduleType, MeetingRoom
    ├── calendar.py     # CalendarDetail, Agenda, VoteMember, ...
    └── docket.py       # DocketDetail, DocketItem, DocketCategory, ...
```

## License

MIT
