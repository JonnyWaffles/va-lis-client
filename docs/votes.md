# Voting records

Part of the [va-lis-client](../README.md) documentation. The README covers
setup and the traps that apply to every call.

## Votes (per-member roll calls)

The `/Vote` service is the only route to who voted how. It covers **both
chambers**, and **committee, subcommittee, and floor** votes alike.

**One vote endpoint takes a bill, and it leaves out the members.** That is
the shallow list, described under
[A bill's votes without the rosters](#a-bills-votes-without-the-rosters).
Every `getvotesby...legislation...` name returns 404. Reach a roll call
through an event:

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
service: join `MemberID` to the roster from `get_members`, which is what
`LISService.roll_call` does.

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

### A bill's votes without the rosters

`get_shallow_votes` returns a vote's tally, type, committee and date, and no
members. It is the one vote endpoint that filters by bill, by member and by
date. `LISService.bill_shallow_votes(bill_number, session_code)` asks both
chambers and merges them, oldest first.

Measured on five 20261 bills on 2026-10-12, against the `VoteID` values on
each bill's events:

| Bill | Votes on its events | Shallow rows | Missing |
|---|---|---|---|
| HB1 | 8 | 7 | 1 voice vote |
| HB1503 | 7 | 7 | none |
| SB1 | 8 | 6 | 2 voice votes |
| HB361 | 16 | 13 | 3 voice votes |
| HB9 | 2 | 0 | 2 voice votes |

Every roll call and every block vote was there, and nothing was there that
the events lacked. Four traps:

- **Most voice votes are missing.** Eight of the nine across those bills.
  A bill decided by voice alone, like HB9, answers 204 from both chambers.
- **`IsBlock` is false on every row.** Vote 300173 is a 50 bill block. Its
  full record says `IsBlock: true` and its shallow row says `false`. The
  shallow row puts the literal `"BLOCK"` where the bill number belongs.
  Read `ShallowVote.is_block`, which tests both.
- **`member_id` and `legislation_id` together are an OR.** The answer is
  every vote on the bill plus every vote the member took part in, 1,911
  rows and 1.5 MB for one delegate's session. It is not that member's
  votes on the bill. Send one or the other.
- **`end_date` is exclusive.** A start and an end on the same day answer
  204.

A chamber and a session are both required, so a bill costs one request per
chamber. `voteNumber` is in the spec and not in the method: LIS answers 500
to it for a committee number and a floor number alike, and this client
retries a 500.

`get_vote_links(legislation_event_id)` is the smallest piece: the `VoteID`,
`VoteNumber` and `VoteFileID` behind one event, or an empty list for an
event with no vote.

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
statement.VoteMemberID == member.VoteMemberID  # WRONG — never matches
statement.VoteMemberID == member.MemberID  # right
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
