# Bills

Part of the [va-lis-client](../README.md) documentation. The README covers
setup and the traps that apply to every call.

## Searching bills

`AdvancedLegislationSearch` is the only endpoint that searches bill text and
summaries, and the only one that filters on status, category, committee,
chapter number, and patron at once. Criteria ride a JSON body, not the query
string. It is also the roughest endpoint in LIS, in four separate ways.

Every count below comes from session 20261, measured 2026-09-15.

### A bill arrives more than once, for two unrelated reasons

An unfiltered search of 20261 returns **3,007 rows holding 2,827 unique
`LegislationID` values**. 175 IDs repeat. Split those 175 groups and they
fall cleanly in two:

| Cause | Groups | What differs | Real? |
|---|---|---|---|
| One row per summary version | 52 | `SummaryVersion`, usually `LegislationSummary` | Yes, structural |
| Exact duplicate rows | 123 | Nothing. Byte identical | No, bad data |

**The summary version rows are structural.** A bill publishes a new summary
as it advances, and every one of them comes back as its own row. Five labels
exist: `SUMMARY AS INTRODUCED`, `SUMMARY AS PASSED HOUSE`, `SUMMARY AS PASSED
SENATE`, `SUMMARY AS PASSED`, and `SUMMARY AS ENACTED WITH GOVERNOR'S
RECOMMENDATION`. 47 bills arrived twice and 5 arrived three times. Note that
17 of the 52 carry *identical* summary text under two different labels, so a
version change does not promise a text change.

**The exact duplicates are bad rows.** 123 bills came back twice with every
field matching, so nothing in the response tells the copies apart. 121 of the
123 held status `Continued` and the other two were HJ29 and HJ30 (`Passed`).
It is not every continued bill: 444 bills hold that status and only 121
doubled up. Carry over is the obvious suspect and it cannot be confirmed from
this response, because the endpoint returns `Sessions: []` on every row.

### Rank the summary label, never the arrival order

LIS usually returns the versions in progression order. Twice in 20261 it did
not: **HB1503 and SB605 both put `SUMMARY AS PASSED` ahead of the chamber
passage row.** Keeping the last row LIS sends therefore picks the wrong
summary about 4% of the time. `SUMMARY_VERSION_RANK` in
`va_lis_client.models.search` ranks the labels instead, and a label missing
from that table ranks below every known one on purpose.

The same fault shows on `/LegislationByMember`, and on the same bill. See
[Rank the label here too](members-and-committees.md#rank-the-label-here-too-and-mind-the-different-spelling),
which also covers the one thing that differs: **the two services spell the
chamber passage label differently.** Advanced search sends `SUMMARY AS PASSED
HOUSE` and `SUMMARY AS PASSED SENATE`; the member list sends `SUMMARY AS
PASSED CHAMBER` and never the other two. `SUMMARY_VERSION_RANK` carries all
six spellings so either service ranks with one call.

**LIS's own numbering agrees with the ranks.** A third service,
`/LegislationText`'s summary endpoint, sends a numeric `SummaryVersionID`
next to the label, and it lines up exactly:

| `SummaryVersionID` | Label | Rank |
|---|---|---|
| 1 | `SUMMARY AS INTRODUCED` | 0 |
| 2 | `SUMMARY AS PASSED HOUSE` / `SENATE` | 1 |
| 3 | `SUMMARY AS PASSED` | 2 |

Checked on HB1, HB18, HB220, HB1115, HB1503 and SB605 in 20261. Neither the
search row nor the member row carries that ID, which is why the table has to
work from the label. That endpoint also returns its rows newest first, the
reverse of the other two.

### The two grains are two methods

The raw client hands the rows back untouched, so a caller who wants LIS's own
answer can have it. The service layer picks a grain, and both methods take
`dedupe=True` by default:

```python
service = LISService(LISClient())

# One row per bill.  The furthest-along summary wins.
bills = service.search_bills(session_code=20261, keyword="firearm")

# One row per published summary version.  Only the exact duplicates go.
versions = service.search_bill_summaries(session_code=20261, keyword="firearm")

# Exactly what LIS sent, on either method.
raw = service.search_bills(dedupe=False, session_code=20261, keyword="firearm")
```

`search_bills` groups on `LegislationID`, which absorbs both duplication
causes at once, and keeps the order each bill first appeared in.
`search_bill_summaries` keys on `(LegislationID, SummaryVersion)`, so it
removes only the exact duplicates and never collapses two genuine versions.
On 20261 unfiltered: 3,007 rows in, 2,827 out of `search_bills` and 2,884 out
of `search_bill_summaries`.

### There is no paging, and `TotalCount` matches nothing

`pageNumber` and `pageSize` on the query string change nothing, and the body
schema carries no paging fields. **Every search returns its whole result set
in one response**, so a filter is the only way to bound it. An unfiltered
20261 search is 5.7 MB.

`X-Pagination.TotalCount` reported **2,836** against 3,007 rows and 2,827
unique IDs. Every single field and field pair in the payload was checked for
a distinct count equal to 2,836 and none matches. Count the rows yourself.

### Criteria traps

- **`Chambercode` has a lowercase `c`**, alone among the body keys. The
  client spells it correctly; only a raw caller or the `extra=` escape hatch
  can get it wrong.
- **A common word finds nothing.** `KeywordExpression: "the"` returned 204
  while `"firearm"` returned 50 bills. LIS drops stop words. This is not a
  result cap.
- **`SubjectIndexID` always returns 204**, with `SessionCode` or `SessionID`.
  The `/LegislationSubject` vocabulary is not searchable.

### Two body fields need their entries wrapped

`LegislationNumbers` and `LegislationIDs` reject a flat list and answer 400.
Each entry has to be its own object, and the key inside the ID wrapper is
`LegislationId` with a lowercase `d`, unlike the `LegislationIDs` list that
holds it. `PatronTypes` is a flat list of integers, so the rule is not
uniform. `search_legislation` wraps the scalars for you:

```python
client.search_legislation(session_code=20261, legislation_numbers=["HB1", "SB2"])
# body: {"LegislationNumbers": [{"LegislationNumber": "HB1"}, {"LegislationNumber": "SB2"}]}
```

A compound keyword search rides the `extra=` escape hatch. `Keywords` takes
`{"Keyword": ..., "Operator": "AND" | "OR"}` objects, and an object may nest
its own `Keywords` list to act as parentheses:

```python
client.search_legislation(
    session_code=20261,
    keyword_location="Summary",
    extra={
        "Keywords": [
            {"Keyword": "firearm", "Operator": "AND"},
            {"Keyword": "ammunition", "Operator": "AND"},
        ]
    },
)  # 8 bills in 20261
```

### Seven fields are always null here

`CandidateDate`, `VersionDate`, `IntroductionDate`, `HousePassageDate`,
`SenatePassageDate`, `Sessions`, and `LegislationStatusID` were null on all
3,007 rows. So a search result carries **no date at all** and no carry over
lineage; fetch those from `/Legislation`. `LegislationTextID` is present but
always `0`, and `CommitteeID` is `0` rather than null when no committee holds
the bill.

The row does carry things the session bill list does not: the summary HTML,
the summary version, the chapter number, and the committee of reference.

**It does not carry co-patrons.** `Patrons` holds exactly one entry, the
chief patron, on all 3,007 rows, the same as the session bill list. Reach
co-patronage through `get_bill_patrons` or `member_bills`.

## Bill versions

The LegislationVersion service lists a bill's published versions: introduced,
each substitute and amendment, engrossed, enrolled, chaptered. Every row is a
record the text list already has, with the same `LegislationTextID` (checked
on HB1 in 20261 and 20241, and on HB1503 in 20261). It adds the chamber that
made the version, its sponsor, and its disposition, and drops `IsComplete`,
`GovernorsRequest`, `IsEmergency` and `IsReprint`.

```python
for v in client.get_bill_versions("HB1503", session_code=20261):
    print(v.DocumentCode, v.Version, v.Description, v.TextDisposition)
# HB1503     Introduced  Introduced            Posted
# HB1503HC1  Substitute  Education Substitute  Offered
# ...
# CHAP0869   Chaptered   Chaptered             Posted

# Many bills at once, grouped back by bill. No files on this path.
by_bill = service.bill_versions(["HB1", "HB1503", "SB2"], 20261)

# One request per bill, and the files come back.
for v in client.get_bill_versions_by_id(98525):
    print(v.DocumentCode, v.PdfFile[0].FileURL)
# HB1       https://lis.blob.core.windows.net/files/1081188.PDF
```

Every count below was measured on 2026-09-17.

### Two things are named LegislationVersion

`LegislationVersion` in `va_lis_client.models` is the 13 row vocabulary of
version *types* that `get_legislation_versions()` returns. The
LegislationVersion *service* returns one bill's versions, modeled as
`BillVersion`. `BillVersion.LegislationVersionID` points into the vocabulary,
and `Version` equalled the vocabulary's `Name` on all 551 rows checked.

### Only a request by one id returns the files

`get_bill_versions_by_id(98525)` sends `legislationID`, and LIS fills
`PdfFile`, `HtmlFile` and `ImpactFile`: a PDF and an HTML link on each of
HB1's three versions and HB1503's six, plus impact statement PDFs. Every
other route sends the arrays empty for the same versions:

- the bill number form,
- the bulk `legislationIDs` form, **even with a single id in the list**,
- the text list's `PDFFile` and `HTMLFile`.

So the links cost one request per bill. `LISService.bill_versions` takes
`with_files=True` to pay that. `LinkFile` was empty everywhere.

### The bulk form mixes the bills together

`get_bill_versions_by_id(legislation_ids=[...])` sends the ids as repeated
`legislationIDs` keys. LIS answers with one list sorted by `DraftDate` across
every bill, nulls first. Rows from different bills interleave, and the order
has nothing to do with the order you asked in. Group on `LegislationID`, or
call `LISService.bill_versions`. 102 ids in one request answered 404 rows and
228 KB.

| Request | LIS answers |
| --- | --- |
| No id at all | Nothing within 60 seconds. The client raises `ValueError` instead of sending it |
| `legislationID` and `legislationIDs` together | 204. The client folds the single id into the list |
| `legislationIDs=98631,98525` | 400, "The value '98631,98525' is not valid." |

### The number form wants `BillNumber` and a session

The key is `BillNumber`. `legislationNumber`, which the other bill endpoints
take, answers 400 "The BillNumber field is required." Without a session LIS
answers 400 "SessionID or SessionCode not provided." The value is case
insensitive, and a padded `HB0001` answers 204.

**A carried bill answers for the odd year with its even year rows.** HB1013
in 20251 returns the same single row as in 20241, with `SessionID` 55.

### Row traps

- **`ChamberCode` is the version's chamber, not the bill's.** A Senate
  committee substitute for a House bill says `S`. 44 of 404 rows sampled from
  20261 differ from the bill number's prefix, and all of them are substitutes
  or amendments.
- **`IsActive` does not mark the current version.** SB633 in 20241 has two
  active rows and HB241 has none, and 6 of 102 bills sampled from 20261 had
  two. Order by `DraftDate` instead.
- **`DocumentCode` is not the bill number plus the vocabulary's suffix.**
  Substitutes read `HB1503HC1` or `HB1503H1`, never the `S` the vocabulary
  gives, and a chaptered version carries its chapter number, `CHAP0350`.
- **`Description` is the specific name and `Version` the type.** "Education
  Substitute" against "Substitute". They differed on 169 of 404 rows.
- **2024 rows are thinner.** They pad `DocumentCode` to ten characters, which
  the model strips. They omit the `TextDisposition` and `TextDispositionID`
  keys (145 of 147 rows sampled). Some send `DraftDate` null, and their dates
  can run backwards: SJ76 was enrolled on 2024-02-06 and introduced on
  2024-02-15.

## Amendments and conference bills

Two lists in `/Legislation` answer questions no other path does: which
bills were amended, and which went to conference.

### The chamber filter picks the amendments, not the bills

`get_amended_legislation(session_code=20261, chamber_code="H")` returns the
bills that carry a **House amendment**. That list held 207 House bills and
71 Senate bills, and all 379 of its amendments were House amendments
(measured 2026-10-12). `ChamberCode` on a row is the bill's chamber;
`ChamberCode` on an amendment is the chamber that offered it.

| `chamber_code` | Rows | Unique bills | Amendments |
|---|---|---|---|
| `"H"` | 278 | 263 | 379 |
| `"S"` | 229 | 221 | 362 |
| omitted | 462 | | |

### A bill repeats once per summary version

The rows outnumber the bills because each published summary version gets
its own row, with the same amendments on every copy. HB18 arrived three
times. `LegislationSummary` itself is null on every row, so the repeat
carries a label and no text. Collapse on `LegislationID` before you count.

An amendment's `TextDisposition` says what became of it: 268 `Adopted`, 66
`Engrossed`, 21 `Not Adopted`, 13 `Removed` and 11 `Posted` in the House
list. Its `LegislationTextID` resolves through `get_draft_text`.

### The conference list takes a session id, and only that

`get_conference_legislation(59)` returns the 170 bills of the 2026 Regular
Session that went to conference, 85 from each chamber. **A `sessionCode`
is silently ignored**, and a request with no session LIS understands
returns every session at once: 2,826 rows and 1.5 MB, back to 1994. That is
why the method has no `session_code` argument. Turn a code into an id with
`get_session_by_code`.

`member_id` narrows the list to a conferee. Member 544 returned five bills,
two of them Senate bills, and the events of HB361 name that member among
the House conferees. The row itself names no conferee, no patron and no
committee.
