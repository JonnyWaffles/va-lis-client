# The Virginia legislative process

Part of the [va-lis-client](../README.md) documentation. The README covers
setup and the traps that apply to every call.

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

#### Which event carries a chamber's final vote

A chamber's final vote on the text that goes to the Governor is a status
question, not an event code question. Measured 2026-09-16 over the 1,156
HB and SB bills of 20261 that reached status 6 or 39:

| Route to enrollment | Bills | The final vote event | `Status` on it |
|---|---|---|---|
| The second chamber passed the text unchanged | 551 | each chamber's own passage, `H5000`/`H5100`/`S5000`/`S5100` | `Passed House` / `Passed Senate` |
| The second chamber amended or substituted, the first agreed | 449 | the second chamber's "Passed Senate with substitute" (`S5022`, status `Passed Senate`), then "Senate substitute agreed to by House" (`H5430`–`H5434`) | `Passed Both` on the concurrence |
| Conference | 152 | "Conference report agreed to by House" and "… by Senate" (`H6015`/`S6015`) in each chamber | `Conference Report Agreed` (display name In Conference) |
| The amending chamber receded | 4 | "Senate receded from substitute (22-Y 18-N 0-A)" (`S5700`–`S5740`, `H5720`) | `Passed Both` |

The event's `ActorType` names the chamber that voted, and `Passed Both`
and `Conference Report Agreed` name none, so read both. Three traps:

- **The same code carries opposite outcomes.** `H5432` is "Senate
  substitute agreed to by House" (status `Passed Both`) and "Senate
  substitute rejected by House" (no status). `H6015` is the report agreed
  to (`Conference Report Agreed`) and rejected (`Conference Report
  Rejected`). The status tells them apart; the code does not.
- **`IsPassage` on the vocabulary misses half the final votes.** It marks
  the chamber passages and the recessions, marks `H5430` and
  `H5432`–`H5434` but not `H5431`, and marks no conference report row.
- **`Passed Both` is also stamped on a text event.** "Reenrolled bill text
  (HB77ER2)" (`H5602`/`S5602`), published after the reconvened session,
  carries `Passed Both` and no tally. A reader that wants the vote must
  require a `VoteTally`. That test also absorbs a duplicate: SB661's
  final "Conference report agreed to by Senate" arrived twice on the same
  day, once with `(23-Y 15-N 0-A)` and a `VoteID`, once with the status
  alone and neither.

The reconvened session's "House concurred in Governor's recommendation"
(`H7300`/`S7300`) and a chamber "insisting" on its own text (`H5500`
family) carry a tally and no status. `Legislation.HousePassageDate` is
the first `Passed House` event, never the final vote.

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
