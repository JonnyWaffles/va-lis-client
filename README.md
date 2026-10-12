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

## Documentation

This README covers setup and the traps that apply to every call. Each domain
has its own guide in [`docs/`](https://github.com/JonnyWaffles/va-lis-client/tree/main/docs).

| Guide | What it covers |
|---|---|
| [API services](https://github.com/JonnyWaffles/va-lis-client/blob/main/docs/api-services.md) | Every wired endpoint, grouped by LIS service, the event vocabulary join, and the paths left unwired and why |
| [Bills](https://github.com/JonnyWaffles/va-lis-client/blob/main/docs/bills.md) | Keyword search and its duplicate rows, the versions of a bill, amendments and conference bills |
| [Voting records](https://github.com/JonnyWaffles/va-lis-client/blob/main/docs/votes.md) | Roll calls, block votes and voice votes, why a vote id on a bill event can belong to another bill, and the full voting record of a member |
| [Members and committees](https://github.com/JonnyWaffles/va-lis-client/blob/main/docs/members-and-committees.md) | The session roster, the bills a member patrons, committee seats, clerks and offices |
| [Chamber records](https://github.com/JonnyWaffles/va-lis-client/blob/main/docs/chamber-records.md) | Minutes books, communications between the chambers, and session statistics |
| [The Virginia legislative process](https://github.com/JonnyWaffles/va-lis-client/blob/main/docs/virginia-process.md) | How a bill becomes law, the status groups, and how meetings and dockets work |

## Service layer

`LISClient` is transport: one method per endpoint, and no interpretation.
`LISService` sits above it. It does the work every caller would otherwise
repeat: resolving human bill numbers to IDs, joining the reference
vocabularies, caching the large lists, and flattening bill text HTML.

```python
from va_lis_client import LISClient, LISService, strip_html
from va_lis_client.models import CHIEF_PATRON

service = LISService(LISClient())  # or LISService() to build the client for you

# Bill numbers, not surrogate keys. Case and padding are normalized.
bill = service.get_bill("hb0001", 20261)
bill_id = service.resolve_bill_id("HB1", 20261)  # ~1.3 KB, via the text list
item = service.resolve_bill("HB1", 20261)  # the session row; needs the 3 MB list

# Text: the newest version by default, or name one by DocumentCode.
item, detail = service.bill_text("HB1", 20261)
item, detail = service.bill_text("HB1", 20261, "HB1ER")
print(strip_html(detail.DraftText))

# The versions of many bills in one request, grouped back by bill.
versions = service.bill_versions(["HB1", "HB1503"], 20261)

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
schmidt = service.find_members("schmidt", 20261)[0]  # MemberID 544
for b in service.member_bills(schmidt.MemberID, 20261):
    print(b.LegislationNumber, b.LegislationStatus, b.SummaryVersion)
chief = service.member_bills(schmidt.MemberID, 20261, role=CHIEF_PATRON)
patrons = service.bill_patrons("HB1408", 20261)  # every role

# Committees: who sits where, with party and district, and the reverse.
courts = service.resolve_committee("Courts of Justice", "H")  # H08
for s in service.committee_members(courts.CommitteeID, 20261):
    print(s.name, s.role, s.party, s.district)
seats = service.member_committees(schmidt.MemberID, 20261)  # 14 cached requests

# Senate dockets: every bill a committee has scheduled, with the date and room.
for e in service.docket_entries("Courts of Justice", 20261):
    print(e.date, e.bill_number, e.room)

# Minutes: a day's proceedings, flattened. The vote id here really is the
# bill's, which is not true of a bill event.
day = service.minutes_books(20261, chamber_code="S")[0]
for r in service.minutes_records(day.MinutesBookID):
    print(r.bill_number, r.event_code, r.tally, r.vote_id)

# Who clerks a committee, and where to write to a member.
staff = service.committee_staff("Courts of Justice", chamber_code="H")
office = service.member_office(503)  # the district address; None for many members

# Keyword search, in two grains. Both drop the duplicate rows LIS emits.
hits = service.search_bills(session_code=20261, keyword="firearm")  # one row per bill
every = service.search_bill_summaries(session_code=20261, keyword="firearm")  # one row per summary

# Every vote including the ones no member can be credited with.
for record in service.bill_votes("HB1", 20261):
    notes = record.statements_by_member()
    for code, members in record.responses().items():  # "Y" "N" "A" "X"
        for m in members:
            print(code, m.MemberNumber, m.name, notes.get(m.MemberID, []))

# Reference joins. Events carry a null type ID and a null status ID, so these
# index the vocabularies on the keys that events actually carry.
types = service.event_types_by_code()  # EventCode -> [LegislationEventType]
kind = service.event_type_for(event)  # the one row describing an event
statuses = service.statuses_by_name()  # Name      -> LegislationStatus
label = service.bill_status_label(bill, item)  # best available status label
```

**Voting records.** `bill_votes` walks a bill's events, takes each `VoteID`,
and fetches the roll call behind it, one request per vote. HB1 in 20261
returns eight votes: two House committee, one House floor, two Senate
committee, three Senate floor. Six of the eight are attributable to HB1
alone. See [Votes](https://github.com/JonnyWaffles/va-lis-client/blob/main/docs/votes.md#votes-per-member-roll-calls) for why the other two are
not.

**`bill_shallow_votes` lists the same votes without the members**, in two
requests however often the bill was voted on. It returns seven for HB1,
because it leaves out most voice votes. Read
[A bill's votes without the rosters](https://github.com/JonnyWaffles/va-lis-client/blob/main/docs/votes.md#a-bills-votes-without-the-rosters)
before you use it.

**`roll_call` is `bill_votes` with the roster joined on.** A ballot from LIS
names a member by `MemberID` and nothing else, so party and district take a
second lookup against the session roster, cached for an hour. `roll_call`
returns only the attributable votes, since naming a member on a block or voice
vote misstates the record. HB1 gives 214 named ballots across its six roll
calls. Each entry carries its own `vote`, because one bill has many.

**Minutes are the reliable way into a roll call.** `minutes_records` flattens
one day's book into a row per action, each carrying the bill, the event code,
the tally, and the `VoteID`. That vote id belongs to the bill beside it,
which is not true of a bill event's vote id, so it needs no repair. The
tally string also keeps a presiding officer's tiebreak that the Vote service
drops. See [Minutes books](https://github.com/JonnyWaffles/va-lis-client/blob/main/docs/chamber-records.md#minutes-books).

**`EventCode` is not unique.** The event-type vocabulary holds 3,912 rows under
only 1,502 codes, and `H1405` alone returns four. Two of those four read
"Reported from Labor and Commerce" and two read "Failed to report (defeated)
in Labor and Commerce". `event_types_by_code` therefore returns **every** row
per code, and `event_type_for` picks the one that describes a given event. See
[Joining the event vocabulary](https://github.com/JonnyWaffles/va-lis-client/blob/main/docs/api-services.md#joining-the-event-vocabulary).

**Searching returns a bill more than once, twice over.** Advanced search
emits one row per published summary version *and* a pile of exact duplicate
rows, so 20261 comes back as 3,007 rows over 2,827 bills. The two grains are
two methods: `search_bills` collapses to one row per bill by ranking the
summary label, and `search_bill_summaries` keeps every version and drops only
the exact duplicates. Both take `dedupe=False` to get LIS's own answer. The
raw client never touches the rows. See
[Searching bills](https://github.com/JonnyWaffles/va-lis-client/blob/main/docs/bills.md#searching-bills), which also covers why the endpoint
cannot be paged.

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
- **Minutes books start at 20241 and are thin at the edges.** 20241 holds 45
  books, all of them Senate floor; 20251 holds 599 and 20261 holds 388;
  20271 answered 204 on 2026-09-15. Floor books are Senate only in every
  session. See [Minutes books](https://github.com/JonnyWaffles/va-lis-client/blob/main/docs/chamber-records.md#minutes-books).
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
| `DocumentCode` | `"HB1ER"` | Bill number + version code, e.g. `HB1503HC1`. A chaptered version carries its chapter number instead, e.g. `CHAP0350` |
| `LegislationTextID` | `257719` | PK for one immutable text snapshot — follows the bill across carry-over (see below) |
| `CommitteeID` | `14` | Surrogate PK for a committee |
| `MinutesBookID` | `3861` | Surrogate PK for one body's proceedings on one day |
| `CommunicationID` | `4519` | Surrogate PK for one message between the chambers |
| `AffiliationID` | `8` | On a personnel row this **is** a `CommitteeID` |
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
trap](https://github.com/JonnyWaffles/va-lis-client/blob/main/docs/votes.md#the-votestatementvotememberid-trap).

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
| Minutes book list | `ListItems` | `Minutes` |
| Minutes book detail | `ListItems` | `MinutesBooks` |
| Action reference types | `ListItems` | `ActionReferenceTypeList` |
| Calendar actions | `ListItems` | `CalendarActions` |
| Communication list | `ListItems` | `CommunicationsList` |
| Communication detail | `ListItems` | `CommunicationResponse` |
| Communication files | `ListItems` | `CommunicationFileResponse` |
| Personnel | `ListItems` | `Personnels` |
| Contacts | `ListItems` | `Contacts` |
| Agencies | `ListItems` | `Agencies` |
| Category types | `ListItems` | `CategoryTypes` |
| Chamber patron index | `ListItems` | `PatronLegislation` |
| Bill versions (both calls) | `ListItems` | `LegislationsVersion` |
| Chamber statistics | `ListItems` | `ChamberStatistics` |
| Committee statistics | `ListItems` | `CommitteeStatistics` |
| Session event types | `ListItems` | `SessionEventTypes` |
| Amended legislation | `ListItems` | `LegislationAmendmentList` |
| Conference legislation | `ListItems` | `ConferenceLegislationList` |
| Legislation id list | `LegislationIds` | `LegislationIds` |
| Member patron types | `ListItems` | `MemberPatronTypes` |
| Subject index | `ListItems` | `SubjectsReference` |
| Summary versions | `ListItems` | `SummaryVersions` |
| Text dispositions | `ListItems` | `TextDispositionList` |
| Text actions | `ListItems` | `LegislationTextActions` |
| Sponsor types | `ListItems` | `SponsorTypes` |
| Text hit counts | `HitCounts` | `HitCounts` |
| Member statuses | `ListItems` | `MemberStatuses` |
| Member contacts | `ListItems` | `MemberContactInformationList` |
| Shallow member list | `ListItems` | `ShallowMembers` |
| Organizations | `ListItems` | `Organizations` |
| Shallow votes | `ListItems` | `ShallowVotes` |
| Vote links | `ListItems` | `VoteLinks` |

MinutesBook and LegislationCommunications each answer a list and a detail
under *different* keys, so the spec is wrong twice for both.

`LegislationsVersion` is not a typo in this table. LIS pluralizes the first
word, and sends a `SearchCriteria` key beside it that was null on every call.

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

len(page)  # 5. PagedList subclasses list.
page[0].LegislationNumber  # "HB71"
page.pagination.TotalCount  # 443
page.pagination.HasNext  # True

# TotalCount without downloading the list: one row, about a kilobyte.
client.get_session_bill_count(session_code=20271)  # 443
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

`AdvancedLegislationSearch` cannot be paged at all. It ignores `pageNumber`
and `pageSize` on the query string, its request body has no paging fields, and
the `X-Pagination` it sends back reports a `TotalCount` that matches neither
the row count nor the unique bill count. See
[Searching bills](https://github.com/JonnyWaffles/va-lis-client/blob/main/docs/bills.md#searching-bills).

## Text that is not JSON

Four paths answer with something other than a JSON envelope. The client
returns each as a `str`, and `None` on a 204.

| Method | LIS sends | What it is |
|---|---|---|
| `get_bill_html` | `text/html` | One text as the printed page, a fragment with inline styles |
| `get_committee_members_text` | `text/plain` | `"Aird (Chair), Stanley, McPike, ..."` |
| `get_schedule_vcal` | `text/calendar` | A `VCALENDAR` with one `VEVENT` per meeting |
| `get_bill_pdf_url` | a JSON string | The address of the PDF |

**`getpdfasync` does not return a PDF.** It returns the blob address, such
as `https://lis.blob.core.windows.net/files/1081188.PDF`, the same address
a bill version fetched by single id carries in `PdfFile`.

**The HTML path calls its parameter `documentNumber` and wants a document
code.** `"HB1"` is the introduced text and `"HB1ER"` the enrolled one. A
code the session does not hold answers 400, not 204.

**`hilite` marks changes to the law, not search hits.** It adds
`color:red` to struck text and `background-color:yellow` to new text.

**The roster text answers 406 without a session**, where every JSON path
answers 400.

`get_draft_text` is JSON, and belongs beside these: it returns one text's
HTML body from its `LegislationTextID` alone, with no session, starting at
the enacting clause. `get_bill_html` returns the same bill with its session
line, number and patrons on top.

`get_text_hit_counts` counts a term in texts you name. Several terms add
up: `"minimum"` counts 12 and `"wage"` 11 in HB1's introduced text, and
`"minimum AND wage"` counts 23.

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
    ├── bill_version.py # BillVersion: one bill's versions, not the LegislationVersion vocabulary
    ├── statistics.py   # ChamberStatistic, CommitteeStatistic
    ├── search.py       # LegislationSearchResult, SUMMARY_VERSION_RANK, ...
    ├── event.py        # LegislationEvent, LegislationEventType, ActorType
    ├── pagination.py   # Pagination, PagedList — the X-Pagination protocol
    ├── committee.py    # Committee, CommitteeMember, CommitteeAction
    ├── schedule.py     # Schedule, ScheduleType, MeetingRoom
    ├── calendar.py     # CalendarDetail, Agenda, ...
    ├── vote.py         # Vote, VoteMember, VoteLegislation, VoteStatement
    ├── member.py       # Member, Party, District
    ├── member_vote.py  # MemberVoteResult — the member-first axis
    ├── docket.py       # DocketDetail, DocketItem, DocketCategory, ...
    ├── minutes.py      # MinutesBook, MinutesEntry, MinutesActivity, ...
    ├── communication.py # Communication, CommunicationCategory, ...
    └── directory.py    # Personnel, Contact, Agency, StaffRoleType, ...
```

## License

MIT
