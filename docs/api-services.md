# API services

Part of the [va-lis-client](../README.md) documentation. The README covers
setup and the traps that apply to every call.

## Currently implemented

### Session (`/Session/api/`)
- `get_sessions(year)` → list of sessions for a year
- `get_default_session()` → the current/active session
- `get_sessions_by_year(year)` → the same rows with the year in the path
- `get_session(session_id)` and `get_session_by_code(session_code)` → one session with its events; `None` if unknown
- `get_sessions_shallow(session_type_id=None, session_year=None, is_active=None)` → every session back to 1994 without its events, 59 rows in 9 KB
- `get_session_event_types()` → 11 milestone types
- `get_session_types()` → 2 types (1 Regular, 2 Special)

`get_session_by_code` is the one request that turns a session code into the
`SessionID` that a few paths insist on. Two traps: `is_active=False` filters
nothing and returns all 59 sessions, and the event type vocabulary calls 8
`Adjournment` and 11 `Closing` while a session files its sine die under 11
with the label `Adjournment`.

### Legislation (`/Legislation/api/`)
- `get_session_bills(session_code)` → lightweight bill list for a session
- `get_bill(legislation_id)` → full bill detail with all patrons
- `get_legislation_statuses()` → all 52 status reference entries
- `get_amended_legislation(session_code=None, session_id=None, chamber_code=None)` → every bill with an amendment, and the amendments; a session is required
- `get_conference_legislation(session_id=None, chamber_code=None, member_id=None)` → bills that went to conference, 170 for 20261; **`session_id` only**
- `get_legislation_list(**criteria)` → the advanced search from this service, same body and same rows
- `get_legislation_ids(**criteria)` → the same search reduced to ids, 30 KB against 440 KB
- `get_legislation_by_ids(legislation_ids, session_id=None)` → search rows for ids you already hold; every entry needs a `SessionID`

Read [Amendments and conference bills](bills.md#amendments-and-conference-bills)
before you count either list. The three search paths repeat bills exactly
as [the advanced search](bills.md#searching-bills) does.

### LegislationText (`/LegislationText/api/`)
- `get_legislation_versions()` → 13 version type references (Introduced, Enrolled, etc.), the vocabulary and not one bill's versions
- `get_bill_texts(legislation_number, session_code)` → text version list
- `get_bill_text_detail(legislation_id, session_code)` → full HTML bill text
- `get_draft_text(legislation_text_id)` → one text's HTML body by text id alone, no session needed
- `get_bill_html(session_code, document_code, hilite=False)` → the printed page as an HTML string
- `get_bill_pdf_url(session_code, document_code)` → the address of the PDF, as a string
- `get_text_hit_counts(legislation_text_ids, keyword=None, words=None)` → how often a term occurs in each text
- `get_text_dispositions()` → 13 dispositions (6 Adopted, 5 Not Adopted, ...)
- `get_text_actions()` → 31 actions that produce a text
- `get_sponsor_types()` → 6 sponsor types

Two of these do not answer JSON, and one is not what its name says. Read
[Text that is not JSON](../README.md#text-that-is-not-json).

### LegislationVersion (`/LegislationVersion/api/`)
- `get_bill_versions(legislation_number, session_code=None, session_id=None)` → one bill's published versions, oldest first; a session is required
- `get_bill_versions_by_id(legislation_id=None, legislation_ids=None)` → the same by id, for one bill or many in one request; raises `ValueError` with no id

The rows are `BillVersion`, not `LegislationVersion`. That name belongs to the
version type vocabulary above. They are the text list's records in another
shape, and the service earns its place twice. The bulk form fetches many
bills in one request, and `LISService.bill_versions` turns bill numbers into
that call. The single id form is the one that returns the PDF, HTML and
impact statement links. Read [Bill versions](bills.md#bill-versions).

Not wired: `clearpubliccache`, which changes server state.

### LegislationSummary (`/LegislationSummary/api/`)
- `get_bill_summaries(legislation_number, session_code)` → bill summaries (HTML)
- `get_summary_versions()` → the 4 summary stages in progression order

The stage ids run 1 `SUMMARY AS INTRODUCED`, 2 `SUMMARY AS PASSED CHAMBER`,
3 `SUMMARY AS PASSED`, 4 `SUMMARY AS ENACTED WITH GOVERNOR'S
RECOMMENDATION`. That is the official order behind `SUMMARY_VERSION_RANK`.
It has no `PASSED HOUSE` or `PASSED SENATE`, the spellings the advanced
search sends.

### LegislationEvent (`/LegislationEvent/api/`)
- `get_bill_events(legislation_id)` → chronological action history for a bill
- `get_event(legislation_event_id)` → one event by its id; `None` if unknown
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

### Joining the event vocabulary

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

### Vote (`/Vote/api/`)
- `get_vote(vote_id)` → one vote with its per-member roll call
- `get_vote_types()` → 3 vote types (1=Committee, 2=Subcommittee, 3=Floor)
- `get_shallow_votes(chamber_code, session_code=None, session_id=None, legislation_id=None, member_id=None, start_date=None, end_date=None)` → votes without their members, by bill, member, or date; a chamber and a session are both required
- `get_vote_links(legislation_event_id)` → the vote behind one event, 290 bytes

**This service is absent from the LIS developer portal service list.** It is
the only route to per-member votes. See [Votes](votes.md#votes-per-member-roll-calls).

### Member (`/Member/api/`)
- `get_members(session_code, chamber_code=None)` → the session roster, with party and district
- `get_member(member_id, session_code)` → one member, **204s for some sitting members**
- `get_parties()` → 3 parties (D, I, R)
- `get_districts()` → 140 districts (100 House, 40 Senate)
- `get_member_list(session_code=None, session_id=None, chamber_code=None, member_number=None, party_code=None)` → the roster with 16 fields, and the only one that filters on party
- `get_active_members(session_code=None, session_id=None)` → 146 of the 148, with the fields the roster leaves null
- `get_member_contacts(session_code=None, session_id=None, chamber_code=None, member_number=None, party_code=None)` → members with their Capitol and district addresses, and the name split into parts
- `get_member_status_types()` → 4 statuses (1 Active, 2 Inactive, 3 Outgoing, 4 Elect)
- `get_chamber_types()` → 2 chambers
- `get_member_owner_types()` → the 6 owner types of `get_owner_types`, with the id sent as a string

`MemberID` is the join key from a ballot. See
[Members](members-and-committees.md#members-the-roster-behind-a-roll-call).

### MemberVoteSearch (`/MemberVoteSearch/api/`)
- `get_member_votes(member_id, session_code)` → every vote one member cast

The member-first axis, where `/Vote` is bill-first. See
[The member-first axis](votes.md#the-member-first-axis).

### LegislationByMember (`/LegislationByMember/api/`)
- `get_member_legislation(member_id, session_id, patron_type_id=None)` → every bill a member patrons, in any role

**Takes a `session_id`, not a session code**, because the endpoint caches
session codes wrongly. See [Bills by member](members-and-committees.md#bills-by-member).

### LegislationPatron (`/LegislationPatron/api/`)
- `get_bill_patrons(legislation_id)` → every patron of a bill, in every role
- `get_patron_roles()` → 5 roles (1 Chief Patron, 2 Chief Co-Patron, 3 Incorporated Chief Co-Patron, 4 Co-Patron, 5 Offered)

- `get_patron_legislation(session_code, chamber_code)` → every chief patron in a chamber with their bills, in one request

**`get_patron_legislation` is chief patron only, despite the name.** Every
row of both chambers came back as `Chief Patron` on 2026-09-15, and the row
count equals the distinct member count: 102 rows over 2,342 bills for the
20261 House, 42 rows over 1,304 bills for its Senate. It is a cheap
chamber-wide index, not a route to co-patronage. Both arguments are
required; without the chamber LIS answers 400 with "The chamberCode field is
required."

- `get_member_patron_types(member_id, session_code=None, session_id=None)` → the roles one member holds in a session; a session is required

**`PatronTypeID` 0 is not a patron role.** `get_member_patron_types` returns
it for budget amendment requests, and `get_patron_roles` does not list it.

### Committee (`/Committee/api/`)
- `get_committees(chamber_code=None, include_subcommittees=False)` → 14 House and 11 Senate standing committees; 67 House rows with subcommittees
- `get_committee(committee_id, session_id)` → one committee, with `MeetingNote` and `EffectiveBeginDate`
- `get_committee_by_number(committee_number)` → the same by number, e.g. `"H14"`

The list ignores the session it is sent. See
[Committees and seats](members-and-committees.md#committees-and-seats).

### MembersByCommittee (`/MembersByCommittee/api/`)
- `get_committee_members(committee_id, session_code)` → who sits on a committee, with roles; session required
- `get_committee_roles()` → 8 roles, with chamber specific IDs
- `get_committee_members_text(committee_id, session_code=None, session_id=None)` → the roster as one line of surnames, plain text; **406** without a session

### CommitteeLegislationReferral (`/CommitteeLegislationReferral/api/`)
- `get_committee_actions()` → 41 committee actions

Despite the name, this service does not list the bills referred to a
committee. The action vocabulary is its only data operation.

### Schedule (`/Schedule/api/`)
- `get_schedules(start_date, end_date, owner_id=None, schedule_type_id=None, vote_room_id=None)` → meetings in a date range; always pass dates
- `get_schedule_types()` → 6 types (1 Committee, 2 Chamber, 3 Conference, 4 Caucus, 5 Other, 6 Docket)
- `get_meeting_rooms(chamber_code=None)` → room reference, 18 for the House

- `get_schedule_vcal(start_date=None, end_date=None, owner_id=None, schedule_type_id=None, vote_room_id=None, schedule_ids=None)` → the same meetings as an iCalendar feed, a string

The feed answers `text/calendar`, not JSON: 32 events in 13 KB for
2026-02-02 to 2026-02-03. Its id filter is `scheduleIDs`, plural. An unknown
parameter such as `scheduleID` is silently ignored and returns a default
feed, so a misspelled filter looks like it worked. The spec also offers
`writeToAzureCDN`, which publishes a file; this client never sends it.

### Calendar (`/Calendar/api/`)
- `get_calendars(chamber_code, session_code)` → a chamber's floor calendars, 56 for the 2026 House
- `get_calendar(calendar_id)` → one calendar with categories, agendas, and per-member vote rows
- `get_dockets(committee_id, session_code)` → a Senate committee's dockets; a House committee returns nothing
- `get_dockets_by_committee_number(committee_number, session_code)` → the same by number, e.g. `"S13"`
- `get_docket(docket_id)` → one docket with its bills, members, staff, and linked schedule
- `get_calendar_types()` → 2 types (1 Chamber, 2 Committee)
- `get_calendar_category_types(chamber_code=None)` → 98 category codes, e.g. `CSGEN` = Senate Bill in Committee
- `get_category_types()` → the 8 coarse types behind `CategoryTypeID`, e.g. 1 `Order`, 5 `General`
- `get_calendar_actions()` → the 4,952 row action vocabulary that resolves a minutes activity; 2 MB, no filter

`get_calendar_actions` is large and its id repeats, so reach for
`LISService.calendar_actions_by_id()` and `action_for()`, which fetch it once
and index it. See [How meetings and dockets work](virginia-process.md#how-meetings-and-dockets-work)
and [Minutes books](chamber-records.md#minutes-books).

### LegislationCommunications (`/LegislationCommunications/api/`)
- `get_communications(session_code=None, session_id=None, chamber_code=None)` → floor communications, 131 for 20261; **pass a session or you get all 4,383**
- `get_communication(communication_id)` → one message with its categories, bills, and patrons
- `get_communication_files(communication_id)` → the HTML and JSON blobs
- `get_communication_category_types(chamber_code=None)` → 231 clerk sentences, 87 House and 144 Senate

This is what one chamber formally told the other. It resolves to bills and
members and **not at all to votes**. Read
[Communications](chamber-records.md#communications-between-the-chambers).

### Personnel (`/Personnel/api/`)
- `get_personnel(staff_role_type_id=None, affiliation_id=None, effective_begin_date=None)` → clerks and staff; **filter, or four fields are omitted**
- `get_staff_role_types()` → 7 roles (1 Committee Clerk, 2 staff, and five more)

`AffiliationID` is a `CommitteeID`, so this is the only route to "who clerks
this committee". Not session scoped. See
[Clerks, offices, and agencies](members-and-committees.md#clerks-offices-and-agencies).

- `get_clerk_personnel(affiliation_id=None, effective_begin_date=None)` → the 54 clerks; the same rows as `staff_role_type_id=1`
- `get_staff_personnel(affiliation_id=None, effective_begin_date=None)` → the 59 DLS staff; the same rows as `staff_role_type_id=2`
- `get_personnel_references(effective_begin_date=None)` → byte for byte the unfiltered `get_personnel()`

The clerk and staff paths send all 11 fields with no argument at all, where
the general list drops four until you filter it.

### Contact (`/Contact/api/`)
- `get_contacts(owner_id=None, owner_type_id=None)` → contact records for one owner, or every contact with neither; for a member pass `owner_type_id=3`
- `get_contact_types()` → 10 types, each with its owner type
- `get_owner_types()` → 6 owner types (3 is Member)

A member's row is their district office address and phone, which LIS
publishes nowhere else. Not session scoped.

Not wired: `getcontactlistasync` answers **401** with a public key.
`getcontactsbystatusasync`, `getcontactbyidasync`, and
`getcontactsbycontacttypeidasync` all answer **404** for every argument
tried. See [Clerks, offices, and agencies](members-and-committees.md#clerks-offices-and-agencies).

### Organization (`/Organization/api/`)
- `get_agencies()` → 295 state budget agencies, grouped by branch
- `get_organizations()` → the same identities as an id and a name, plus `749` for the General Assembly
- `get_organization(organization_id)` → one name by `IdentityID`; `None` if unknown

Executive budget reference data that joins to nothing legislative. Wired for
completeness. `getorganizationbyidasync` answers 204 with an empty body for
an unknown id rather than 404.

### MinutesBook (`/MinutesBook/api/`)
- `get_minutes_books(session_code=..., committee_id=None, chamber_code=None, start_date=None, end_date=None)` → the published books for a session, 388 for 20261; a session is mandatory
- `get_minutes_book(minutes_book_id=None, calendar_id=None)` → one book with its categories, entries, activities, and votes
- `get_minutes_book_interactive(...)` → the same book shaped for the web calendar, and lossy; prefer the plain call
- `get_action_reference_types()` → 15 reference kinds, and the service that resolves each

This is the daily record of what a chamber or committee did, and its
activities carry the one `VoteID` in LIS that reliably belongs to the bill
beside it. Read [Minutes books](chamber-records.md#minutes-books).

### AdvancedLegislationSearch (`/AdvancedLegislationSearch/api/`)
- `search_legislation(**criteria)` → bills by keyword, status, category, committee, chamber, patron, chapter number, or bill number; rows exactly as LIS sent them, chief patron only
- `get_most_frequent_legislation(session_id)` → the 100 most viewed bills; `sessionID` only, no `sessionCode`
- `get_legislative_numbers(session_code)` → 3,646 rows of bill number to `LegislationID` for 20261, 268 KB against roughly 3 MB for the bill list
- `get_introduction_dates(session_code)` → the 91 distinct introduction dates in 20261
- `get_legislation_categories()` → 30 categories, e.g. 1 `Introduced`
- `get_search_statuses()` → the 52 rows of `get_legislation_statuses`, from this service

This is the only keyword capable bill endpoint, and the only one that filters
on status, category, committee, chapter number, and patron in one call. It
has no paging and it repeats bills. A keyword finds nothing without
`keyword_location`, a criterion beside the keyword drops the failed bills,
and the committee filter does not pick the bills a committee holds. Read
[Searching bills](bills.md#searching-bills) before you use it.

Not wired: `clearpubliccache` and `clearintroductiondatelistcacheasync`, which
are cache management, not data.

### LegislationSubject (`/LegislationSubject/api/`)
- `get_subjects(session_code=None, session_id=None)` → 506 subject index headings for 20261; a session is required

**The headings join to nothing a public key can reach.** No bill row carries
a `SubjectIndexID`, and passing one to the advanced search answers 204 every
time, with either session parameter.

### Statistics (`/Statistics/api/`)
- `get_chamber_statistics(session_code=None, session_id=None)` → bill counts per chamber and legislation type, 6 rows for 20261; a session is required
- `get_committee_statistics(chamber_code, session_code=None, session_id=None)` → referral counts per standing committee, 14 House and 11 Senate for 20261; a chamber and a session are both required

LIS aggregates these itself and names no bill, and the numbers do not always
agree with the lists they summarize. Read
[Session statistics](chamber-records.md#session-statistics) before you report one.

Not wired: `clearpubliccache`, which changes server state.

## Investigated and deliberately not wired

**Every read path a public key can call has a method.** The list below is
what is left, and why. Each service was read from its OpenAPI spec and
probed live, on 2026-09-15 or 2026-10-12. Fetch any spec with
`GET /ReportFileGeneration/api/getapidocumentationasync?ApiName=<Service>`.
There is no per service `getapidocumentationasync`; that path returns 404 for
every service. The `Documentation` field holds the OpenAPI document as a JSON
string, so it needs a second parse.

**Nothing to wire.** These publish only heartbeat paths, so they have no
public data operation at all:

- CommunicationFileGeneration
- LegislationCollections
- LegislationFileGeneration

**Documented but unreachable.** Person returns 404 on every path, including
`/Person/api/getpersonlistasync` and `/Person/api/getpersonbyidasync/{id}`.
Its spec omits the service prefix on every path, but that is not the cause:
Personnel has the same spec defect and answers fine at `/Personnel/api/...`.

**Already covered.** MemberVoteSearch holds only `getmembervotelistasync`,
which `get_member_votes` already wires.

**Answers nothing.** `Member`'s `getcommitteeslistasync`, "committees by
member", answered 204 on every form tried on 2026-10-12: two members, the
`MemberID` key in both spellings, three shapes of `effectiveDate`, an
`IdentityID` and a `MemberDetailID` in place of the member id, and an added
session. A method for it would return an empty list every time. The route
to a member's committees is still `LISService.member_committees`.

**Privileged, or gone.** `Contact`'s `getcontactlistasync` answers **401**
with an empty body for a public key, bare and filtered.
`getcontactsbystatusasync`, `getcontactbyidasync` and
`getcontactsbycontacttypeidasync` answer **404** for every argument tried.

**Out of scope: anything a public API key cannot use on its own.** This
client covers only what a holder of a public `WebAPIKey` can call. So it
does not wire:

- **Authentication** and **User**. These manage LIS website accounts: log
  in, create an account, change or reset a password. They need a user's
  password, not an API key.
- **PartnerAuthentication** registration (`registerpartnerasync`,
  `completepartnerregistrationasync`). This is the key signup flow. Only
  `checkpartnerkeyasync` is wired, as `check_api_key`.
- **Services with no published spec.** `GetApiListAsync?IsPartner=true`
  names Groups, GroupsRoles, Notification, PrintDocuments,
  CalendarFileGeneration and ReportFileGeneration, and the LIS web app also
  calls JournalFileGeneration, MinuteEntries, SenateVote and
  VoteFileGeneration. Every one of them answers 400 when you ask for its
  spec. The web app sends a signed-in user's bearer token with its calls to
  at least three: CalendarFileGeneration, JournalFileGeneration and
  ReportFileGeneration.

ReportFileGeneration does have two public paths, and they are how you
survey the API: `GetApiListAsync?IsPartner=false` returns the 31 public
service names, and `getapidocumentationasync?ApiName=<Service>` returns one
service's OpenAPI document.
