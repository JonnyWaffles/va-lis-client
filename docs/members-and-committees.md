# Members and committees

Part of the [va-lis-client](../README.md) documentation. The README covers
setup and the traps that apply to every call.

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
[events join their status on the name](api-services.md#legislationevent-legislationeventapi).

### Which member endpoint to use

There are three, and they are incomplete in **different dimensions**. Neither
of the two useful ones is simply the better one.

| | `getmembersasync` | `getmemberbyidasync` | `getmemberlistasync` |
|---|---|---|---|
| Rows | **all 148** | 1, and **9 of 148 return nothing** | all 148 |
| Fields | 28, nine always null | 29, five null | 16 |
| Wired as | `get_members` | `get_member` | `get_member_list` |

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

**`getmemberlistasync` cannot back a voting record.** It returns the same 148
members under `ShallowMembers` with only 16 fields, dropping `DistrictID`,
`DistrictName` and `RoomNumber`. Its one advantage is its filters:
`get_member_list` takes a `party_code` and a `member_number`. It ignores the
`statuses` parameter its spec lists, in every form tried.

**Two more lists exist, and neither replaces the roster.**

- `get_active_members` fills what the roster leaves null: `MemberDetailID`,
  `ChamberName` and `SessionID` on every row, `SeatNumber` and
  `VotingSequence` on most. But it returned 146 of the 148 for 20261, and
  "active" is loose: 5 of the 146 were `Outgoing` and 3 `Inactive`.
- `get_member_contacts` is the only list with `FirstName`, `MiddleName` and
  `LastName`, and it nests each member's addresses. Its `PrefiledCount`,
  `NotPrefiledCount` and `LegislationTotal` are **session totals, the same
  on every row**, unless the request names one `member_number`. Its
  `MemberStatusID` said `0` beside `"Active"` on 50 of 106 House rows.

Every member has a `"GA"` address in the contact list, and it is a
placeholder: `OwnerID` 0, `ContactTypeID` 0, no id, no phone. The
`"Mailing"` address beside it is the real district office record, with
`OwnerID` equal to the `MemberID`.

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

### One row per summary version, and exact duplicates too

A bill appears once per published summary. HB18 came back three times, as
introduced, as passed chamber, and as passed, so 236 rows described 229
bills. `member_bills` keeps the row whose `SummaryVersion` ranks furthest
along and preserves the order of first appearance.
`LISClient.get_member_legislation` returns the rows as sent.

This endpoint repeats a bill for the same two reasons
[advanced search does](bills.md#a-bill-arrives-more-than-once-for-two-unrelated-reasons).
Across a 40-member sample of 20261, measured 2026-09-15:

| | Count |
|---|---|
| Bills arriving on more than one row | 198 |
| ...carrying more than one distinct summary version | 57 |
| ...whose rows are byte identical, with nothing to tell them apart | 141 |

So most of the repetition here is the bad-row defect, not the version split.
Collapsing on `LegislationID` absorbs both.

### Rank the label here too, and mind the different spelling

**LIS does not reliably order the versions, and the same bill proves it on
both services.** In the 40-member sample, 1 of the 57 multi-version groups
arrived out of order: **HB1503 sent `SUMMARY AS PASSED` ahead of `SUMMARY AS
PASSED CHAMBER`**, so taking the last row kept the older label. HB1503 is one
of the two bills that breaks the ordering on advanced search as well. 35 of
those 57 groups carry genuinely different summary text, so the pick matters.

**`/LegislationByMember` spells the chamber summary its own way.** It sends
four labels and uses `SUMMARY AS PASSED CHAMBER`. It never sends the
`SUMMARY AS PASSED HOUSE` / `SUMMARY AS PASSED SENATE` pair that advanced
search sends, and advanced search never sends `CHAMBER`. `SUMMARY_VERSION_RANK`
in `va_lis_client.models.search` carries all six spellings, so one call to
`summary_version_rank` ranks a row from either service.

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

## Clerks, offices, and agencies

Three small services, none of them session scoped. No session parameter
exists on any of them, and `sessionCode` is silently ignored, so the client
treats them as static reference data.

### Personnel is the only route to a committee's clerk

`AffiliationID` on a personnel row **is** a `CommitteeID`. All 34 distinct
values resolve, and ten of them are subcommittees that
`get_committees()` does not return, because that list holds only the 25
standing committees.

```python
for person in service.committee_staff("Courts of Justice", chamber_code="H"):
    print(person.FullName, person.StaffRoleTypeID)
# Annie Johnson 1
# Britt Olwine 2
```

**Filter, or you lose the link.** An unfiltered call returns 33 rows that
omit `StaffID`, `AffiliationID`, `StaffRoleTypeID` and `EffectiveBeginDate`
*entirely*, so it cannot tell you who clerks what. Any parameter at all
makes LIS send all 11 fields. `staff_role_type_id=1` gives 54 committee
clerks and `2` gives 59 DLS staff; the two sets share no `StaffID`.

The grain is one row per assignment, not per person: `StaffID` is the row
and `IdentityID` the person, and identity 679 serves two committees.
`PhoneNumber` and `EmailAddress` are null on all 146 rows, and
`EffectiveEndDate` is in the spec and never sent.

**The unprefixed path is a trap.** Personnel's spec omits the service
prefix, and `/api/getpersonnellistasync` answers **200 with the LIS web
app's HTML**, not a 404. It will fail as a parse error rather than an HTTP
error. Always use `/Personnel/api/...`.

### A member's district office

```python
office = service.member_office(503)
print(office.Address1, office.City, office.StateCode, office.PhoneNumber)
# P.O. Box 1853 Norfolk VA (757) 204-5492
```

A `Member` already carries `GABEmailAddress` and `GABPhoneNumber`, and the
contact's email agrees on 115 of 124 rows. The **postal address and district
phone** are what this adds, and they live nowhere else in LIS.

**Many members have no row.** 390 member contacts exist against the 148
members sitting in 20261, so the set is part historical and part incomplete.
Member 503 answers and member 527 does not; `member_office` returns `None`
rather than raising.

**`OwnerTypeID` is declared required and never sent.** No contact row
carries it, so `get_contact_types()` is the only way to learn that contact
type 9 belongs to owner type 3.

### An unfiltered contact call returns everything, in three populations

`getcontactsbyownerasync` takes `ownerID` and `ownerTypeID` **together or
not at all**. One alone gives 400, "OwnerID and OwnerTypeID must be used in
conjunction with each other". Neither returns every contact LIS holds in one
response with no paging: 6,882 rows on 2026-09-16, and growing, because most
of them are website accounts.

```python
contacts = client.get_contacts()
offices = [c for c in contacts if c.ContactTypeID == 9]  # 390 member offices
```

| `ContactTypeID` | Type | Rows | What it is |
| --- | --- | --- | --- |
| 9 | Mailing | 390 | Member district offices. `OwnerID` is a `MemberID`; 384 carry an address, 367 a phone |
| 10 | Main | 2 | The two General Assembly buildings |
| 3 | User | 6,490 | LIS website accounts. An email address and nothing else |

**`OwnerID` on a type 3 row joins to nothing.** Its range covers every
member and personnel identity, so a join on it succeeds and returns nonsense.
It is a separate account namespace: matched against member identities, 0 of
145 emails carried the member's surname. Filter to type 9 before you join
`OwnerID` to a member.

The 390 member rows outnumber the 148 sitting members because the set holds
former members too, and some sitting members have no row at all. The bulk
call is still the cheap way to get every office at once: one request instead
of one per member.

### Organization is budget data

`get_agencies()` returns the 295 executive budget agencies grouped by branch
(Education, Public Safety, Central Appropriations, and 16 more). Its
`IdentityID` shares **no value** with a `MemberID`, a `CommitteeID`, or a
personnel identity. It is wired for completeness, and it will not join to
anything you are working on.
