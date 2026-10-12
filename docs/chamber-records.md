# Chamber records

Part of the [va-lis-client](../README.md) documentation. The README covers
setup and the traps that apply to every call.

## Minutes books

A minutes book is one body's proceedings on one day. It is the record a
clerk keeps, and it nests four levels deep:

```
MinutesBook            the body and the day
└── MinutesCategory    a section of business, e.g. "Senate Bills Vetoed by Governor"
    └── MinutesEntry   one bill, or one procedural note
        └── MinutesActivity   what happened, and the vote that decided it
            └── ActivityReference   a citation hanging off the activity
```

The bill sits on the **entry**. What happened to it sits on the
**activity**. `minutes_records` joins the three levels that carry meaning
and hands back one row per action:

```python
books = service.minutes_books(20261, chamber_code="S")
day = next(b for b in books if b.is_floor and b.MinutesStatus == "Closed")

for record in service.minutes_records(day.MinutesBookID):
    print(record.bill_number, record.event_code, record.tally, record.vote_id)
# SB2   S7300  (21-Y 18-N 0-A)  302328
# SB24  S7300  (38-Y 1-N 0-A)   302330
```

### This is the one `VoteID` in LIS you can trust

`LegislationEvent.VoteID` often points at a vote that disposed of some other
bill. That trap has its own section,
[A VoteID on a bill event is often not that bill's roll call](votes.md#a-voteid-on-a-bill-event-is-often-not-that-bills-roll-call),
and the client works around it by matching on the vote's own legislation
list.

**The minutes activity does not have that problem.** Sampled live on
2026-09-15 against Senate book 3861 — 3 recorded roll calls, 2 block votes
and 2 voice votes — every `VoteID` resolved to a vote whose
`VoteLegislation` listed the entry's own bill, and every `VoteNumber` matched
the vote record exactly. So a minutes record is a safe way in to a roll call:

```python
record = service.minutes_records(day.MinutesBookID)[0]
roll = service.roll_call(record.bill_number, 20261, vote_id=record.vote_id)
```

### The minutes tally keeps a tiebreak the vote record drops

The two services spell the same vote differently, and the minutes hold more.
Against the same book:

| Activity `VoteTally`                 | Vote service `VoteTally` |
| ------------------------------------ | ------------------------ |
| `(18-Y 18-N 0-A, Chair votes Yea)`   | `(18-Y 18-N 0-A)`        |
| `block vote (21-Y 18-N 0-A)`         | `(21-Y 18-N 0-A)`        |
| `(Voice Vote)`                       | `(Voice Vote)`           |

The presiding officer's tiebreaking vote survives in the minutes and nowhere
else. Read the activity for the human record, and the Vote service for the
counts and the members.

### `IsCommittee` is false on every row, and `SessionCode` is null

Two fields on the book list are dead. `IsCommittee` was `False` on all 388
rows of 20261, including the 342 that name a committee. `SessionCode` was
null on all 388, even though the request filtered by it.

The real discriminator is `CommitteeID`, which LIS **omits entirely** on a
floor book rather than sending null. `MinutesBookListItem.is_floor` tests
that, so use it instead of the flag:

```python
floor = [b for b in books if b.is_floor]
```

### Only the Senate publishes floor books

Both chambers publish committee books. Floor books are Senate only in every
session checked:

| Session | Books | Floor | Floor chambers | House | Senate |
| ------- | ----- | ----- | -------------- | ----- | ------ |
| 20241   | 45    | 45    | S              | 0     | 45     |
| 20251   | 599   | 33    | S              | 467   | 132    |
| 20261   | 388   | 46    | S              | 201   | 187    |
| 20271   | 204   | —     | —              | —     | —      |

20241 is the odd one: it holds Senate floor books and nothing else. 20271
answered 204 on 2026-09-15, so the upcoming session has no books yet. For
House floor proceedings, read the chamber's calendar and the bill's events
instead.

### A session is mandatory, and every other filter needs one

`get_minutes_books` answers 400 with "Must provide either a valid Session ID
or Session Code" when you pass no session, and it does the same for
`committeeID` or `minutesBookID` on their own. `sessionCode` and `sessionID`
agree: both returned the same 388 rows for 20261.

`startDate` and `endDate` do work, taking `YYYY-MM-DD`, and a `committeeID`
narrows to that committee's books — which excludes every floor book, since a
floor book has no committee.

### The interactive view is lossy

`getminutesbookinteractivecalendarasync` returns the same book shaped for the
LIS web calendar. Against Senate book 3861 it returned **168 entries against
195**, 8 categories against 11, and it dropped `EventCode`, `IsBlock` and
**all 171** `VoteTally` strings. It gains only narrative summaries, 39
against 18. It is wired as `get_minutes_book_interactive` for the prose.
Never reach for it when you want the votes.

### Three more shapes worth knowing

- **Two envelope keys for one service.** The list answers under `Minutes`
  and the detail under `MinutesBooks`. The OpenAPI spec says `ListItems` for
  both and is wrong twice. The reference list answers under
  `ActionReferenceTypeList`.
- **LIS omits keys rather than nulling them.** The 195 entries of book 3861
  arrived in 4 different key sets; `AgendaID` is absent on procedural rows,
  and the communication fields appear only on the 2 rows that carry one.
  Every field on every minutes model is optional for this reason.
- **`CategoryCode` is null on every category**, unlike its docket and
  calendar cousins. Read `CategoryType` (`General`, `Order`,
  `Communication`, `Miscellaneous`) and `CategoryDescription`, and order by
  `Sequence` — book 3861 described 3 of its 11 categories as "Order of
  Business".

### Resolving an activity to its action label

Every activity carries a `CalendarActionID` into the Calendar service's
action vocabulary. `action_for` resolves it:

```python
book = service.minutes_book(3861)
for activity in book.activities[:3]:
    print(activity.CalendarActionID, service.action_for(activity).Description)
# 3675 Call to Order
# 3678 Generic Comment
# 2485 ROLL CALL OF MEMBERS:
```

204 of book 3861's 205 activities resolve, and the `EventCode` agrees on
every one of the 204.

**The vocabulary repeats its own key.** `get_calendar_actions()` returns
4,952 rows holding only **490** distinct `CalendarActionID`, because LIS
emits one row per calendar category the action can appear under. Id 1016
arrives 84 times, identical except for `CategoryCode` and
`CalendarCategoryTypeID`, all 84 carrying `EventCode` `H4000`. This is the
same one to many shape as the event vocabulary, so
`calendar_actions_by_id()` maps an id to a **list**, and `action_for` breaks
the tie on `EventCode`.

It is also a 2 MB download with no filter, so fetch it through the service,
which keeps it for the life of the instance.

The activity's own `ActionDescription` is the filled-in version of the
vocabulary's clean `Description`. Action 3675 reads `"Call to Order"` in the
vocabulary and `"Called to Order at 12 m. by Lt. Governor Ghazala F.
Hashmi"` in the book.

A minutes category's `CategoryTypeID` resolves too, against the 8 rows of
`get_category_types()`. All 11 categories of book 3861 matched their own
`CategoryType` string.

### The reference vocabulary names the service that resolves a citation

`get_action_reference_types()` returns 15 rows, and `ObjectName` is the
useful column: it names the entity `ActivityReference.ReferenceID` points
at, so it tells you which service resolves the citation. A null `ObjectName`
means the reference is self-contained prose, which is most of them — 80 of
the 89 references in book 3861 were plain text. The ids run 1-9 and 11-16;
there is no 10.

## Communications between the chambers

A communication is a message the clerk of one chamber sends to the other.
"The House of Delegates has passed the following Senate bills." It nests
three levels: the communication holds categories, a category holds bills,
and a bill holds patrons.

```python
rows = client.get_communications(session_code=20261)  # 131, shallow
detail = client.get_communication(rows[0].CommunicationID)

for category in detail.categories:
    print(category.label)
    for bill in category.bills:
        print("   ", bill.LegislationNumber, bill.LegislationDescription)
```

### Pass a session, or you download everything

Every filter is optional and the endpoint has no paging. With no arguments
it streams all **4,383** communications LIS holds, about 2 MB, reaching back
to 1994-01-13. `session_code` and `session_id` agree; both gave the same 131
rows for 20261. An unknown `chamber_code` answers 204 rather than 400.

### The two description fields disagree

A category carries `CategoryDescription` always and `Description` only
sometimes. On all 13 rows that carried both (of 56 checked), they
contradicted each other, and `MinutesSummary` was built from the wrong one.
Communication 4520, category code `HN2300`:

| Field | Value |
| --- | --- |
| `CategoryDescription` | THE HOUSE OF DELEGATES HAS STRICKEN FROM ITS CALENDAR THE FOLLOWING SENATE BILL: |
| `Description` | THE HOUSE OF DELEGATES HAS PASSED BY FOR THE DAY THE FOLLOWING SENATE BILL: |

Striking a bill from the calendar and passing it by for the day are
different acts. `CommunicationCategory.label` prefers `Description` when LIS
sends it, so read that rather than either field.

### There is no vote join here

`VoteNumber` is null on all 440 legislation rows checked, `EventCode` and
`CommitteeReferredName` likewise, and the `VoteID` the spec promises is
never sent. The `voteID` and `voteNumber` query parameters both answer 204
for every value tried. The bill and text ids **are** honest: `LegislationID`
98536 resolves to SB2 in 20261, and `LegislationTextID` 267622 to that
bill's Enrolled text with `DocumentCode` `SB2ER`.

### Structured content starts in May 2024

Ask for an older communication and `CommunicationCategories` comes back
empty with a single HTML file attached. Verified on communication 3625
(20191) and 4192 to 4194 (20241). Before SessionID 56, the file is the
record and the API will not parse it for you.

### Three more shapes

- **Three fields are constant.** `IsPublic` is `True`,
  `CommunicationTypeID` is `1`, and `CommunicationType` is `"Floor"` on all
  4,383 rows. There is no second type, which is also why the documented
  `communicationType` filter does nothing. The `textFormat` filter on the
  blob endpoint is ignored the same way, so neither is exposed.
- **`ReferenceNumber` repeats across sessions.** It is unique within one,
  but `SN10422` appears in three. Key on `(SessionID, ReferenceNumber)`.
- **A category type repeats within one communication**, so the type id is
  not the identity. Communication 4519 carried type 1467 four times and 4520
  carried type 1823 seven times. Key on `CommunicationCategoryID`.

## Session statistics

```python
for row in client.get_chamber_statistics(session_code=20251):
    print(row.ChamberCode, row.LegislationTypeCode, row.total, row.Passed, row.Failed)
# H B 1463 537 895
# ...

senate = client.get_committee_statistics("S", session_code=20261)
```

Every count below was measured on 2026-09-17.

### The chamber totals, checked against the lists

`ChamberStatistic.total` is `Introduced + ContinuedFromLastSession`, the
session's count for one chamber and one legislation type. Checked against
`get_legislative_numbers` for 20241 and 20251, and against the session bill
list for 20261:

| Session | Rows | Sum of `total` | Bills listed | Where they differ |
| --- | --- | --- | --- | --- |
| 20241 | 6 | 3,595 | 3,596 | House resolutions: 452 against 453 |
| 20251 | 6 | 3,512 | 3,512 | Nowhere |
| 20261 | 6 | 3,646 | 3,645 | House resolutions: 495 against 494 |
| 20271 | 5 | 443 | not counted | No House resolution row, because none carried over |

**An empty type is omitted, not zeroed**, which is why 20271 has five rows.

### The outcome columns add up differently each session

- **20251:** `Passed + Failed + Incorporated` equals `total` on every row.
  `Failed` includes the carried over bills that died.
- **20241:** `Passed + Failed + ContinuedToNextSession` equals it instead.
  `Incorporated` is 0 on every row, so incorporated bills sit inside `Failed`.
- **20261:** `Passed + Failed + Incorporated + Pending` overshoots. House
  bills sum to 1,531 against 1,530, Senate bills to 838 against 836. The bill
  list's statuses count 493 and 201 failed where the statistics say 494 and
  203.

`Passed` is chaptered plus vetoed: 712 plus 16 makes the 728 House bills of
20261. `Pending` matched the bills the list shows as introduced or in
committee.

### Other chamber traps

- **`ContinuedToNextSession` overcounts.** 20261 says 257 House bills and 175
  Senate bills were continued. 20271's `ContinuedFromLastSession` says 255 and
  174, the same as 20261's `Pending`. Use those.
- **`PassedHouse` and `PassedSenate` are not a funnel.** Senate joint
  resolutions in 20261 read `PassedSenate` 281, `PassedHouse` 289 and
  `Passed` 289, though none can reach the House before it passes the Senate.
- **The chamber endpoint ignores `chamberCode`** and always answers for both.
- **`AwaitingGovAction` was 0** on every row of all four sessions.

### Committee statistics count referrals, not bills

- **One row per standing committee**, never a subcommittee. The `H` and `S`
  column prefixes name the **bill's** chamber, so `SReferred` on a House
  committee counts Senate bills sent to it after crossover.
- **The committee fields are honest.** `CommitteeID`, `CommitteeNumber` and
  `CommitteeName` matched the committee list on all 98 rows checked.
- **The columns do not add up.** In 20261,
  `Reported + Failed + Incorporated + InCommittee` equalled `Referred` for
  only 16 of 50 committee and prefix pairs. `Failed` is only what a committee
  killed: House committees failed 31 House bills, against the 494 House bills
  the chamber row counts as failed.
- **`Continued` matches nothing exactly.** 20261 House committees continued
  182 House bills, and 20271 says 181 came in. H07 continued 21 while holding
  20 `InCommittee`.
- **The committee set moves.** 20251 has no row for H18 Privileges and
  Elections or H24 Health and Human Services, which 20241 and 20261 both list.
  And 20251's Senate committees still held 5 Senate bills `InCommittee` after
  the session closed.
- **A chamber and a session are both required.** Without the chamber LIS
  answers 400 "The chamberCode field is required." Without a session it
  answers 400 "Must provide either a valid Session ID or Session Code". An
  unknown chamber answers 204.
