"""Statistics models: the counts LIS publishes for a session.

Two grains: one row per chamber and legislation type, and one row per
standing committee.  LIS aggregates both on its side and names no bill in
either, so a count cannot be audited from here.

Checked against bill lists counted by hand on 2026-09-17, the chamber totals
agree exactly for 20251 and are off by a bill or two in 20261 and 20241.  The
committee counts are referrals, not bills, and do not add up to anything.
See each class for the measurements.
"""

from va_lis_client.models.common import LISModel


class ChamberStatistic(LISModel):
    """A row of ``getchamberstatisticsasync``: one chamber, one legislation type.

    ``LegislationTypeCode`` is ``"B"`` bill, ``"J"`` joint resolution or
    ``"R"`` resolution, so a session has up to six rows.  20241, 20251 and
    20261 had six.  20271 had five, because no House resolution carried over:
    **a type with nothing in it is omitted, not zeroed.**

    :attr:`total` is the session's count for the row, and it matched the bill
    number list on every row of 20251.  It missed by one on House resolutions
    twice: 495 against 494 listed in 20261, and 452 against 453 in 20241.

    **The outcome columns add up differently in each session.**

    - 20251: ``Passed + Failed + Incorporated`` equals :attr:`total` on every
      row, so ``Failed`` includes the carried over bills that died.
    - 20241: ``Passed + Failed + ContinuedToNextSession`` equals it instead.
      ``Incorporated`` is 0 on every row, so incorporated bills sit in
      ``Failed``.
    - 20261: ``Passed + Failed + Incorporated + Pending`` overshoots.  House
      bills sum to 1,531 against 1,530, Senate bills to 838 against 836.  The
      bill list shows 493 and 201 failed where these rows say 494 and 203.

    ``Passed`` is chaptered plus vetoed: 712 plus 16 is the 728 House bills
    of 20261.  ``Approved`` said 711 for those 712 chapters.

    **``ContinuedToNextSession`` disagrees with the next session.**  20261
    says 257 House bills and 175 Senate bills were continued.  20271's
    ``ContinuedFromLastSession`` says 255 and 174, which is what 20261's
    ``Pending`` says too.  Trust those.

    **``PassedHouse`` and ``PassedSenate`` are not a funnel.**  Senate joint
    resolutions in 20261 read ``PassedSenate`` 281 and ``PassedHouse`` 289,
    though one cannot reach the House without passing the Senate.  A simple
    resolution passes one chamber only, so the other chamber's column is 0.

    ``AwaitingGovAction`` was 0 on every row of all four sessions measured on
    2026-09-17.  Before 20271 convened, its rows held nothing but carry over:
    ``Introduced`` 0 and ``Pending`` equal to ``ContinuedFromLastSession``.

    Example::

        {"ChamberCode": "H", "LegislationTypeCode": "B", "Introduced": 1530,
         "PassedHouse": 852, "PassedSenate": 735, "Passed": 728,
         "Pending": 255, "Incorporated": 54, "Failed": 494, "Approved": 711,
         "Veto": 16, "AwaitingGovAction": 0, "ContinuedFromLastSession": 0,
         "ContinuedToNextSession": 257}
    """

    ChamberCode: str  # the originating chamber, "H" or "S"
    LegislationTypeCode: str  # "B", "J" or "R"
    Introduced: int | None = None  # new this session; carry over is separate
    PassedHouse: int | None = None  # not a funnel with PassedSenate
    PassedSenate: int | None = None
    Passed: int | None = None  # chaptered plus vetoed
    Pending: int | None = None
    Incorporated: int | None = None  # 0 on every 20241 row
    Failed: int | None = None
    Approved: int | None = None
    Veto: int | None = None
    AwaitingGovAction: int | None = None  # 0 in every session measured
    ContinuedFromLastSession: int | None = None
    ContinuedToNextSession: int | None = None  # overcounts; see above

    @property
    def total(self) -> int:
        """Every bill of this chamber and type in the session, carry over included.

        ``Introduced`` plus ``ContinuedFromLastSession``.  In an odd year the
        session's bill list holds its carried over bills, so this is the
        number to compare with a count of that list.
        """
        return (self.Introduced or 0) + (self.ContinuedFromLastSession or 0)


class CommitteeStatistic(LISModel):
    """A row of ``getcommitteestatisticsasync``: one standing committee.

    The endpoint answers for one chamber's committees at a time and never
    lists a subcommittee.  20241, 20261 and 20271 had 14 House and 11 Senate
    rows.  20251 had 12 House rows: it lacks H18 Privileges and Elections and
    H24 Health and Human Services.

    **The ``H`` and ``S`` prefixes name the bill's chamber, not the
    committee's.**  ``SReferred`` on a House committee counts Senate bills
    referred to it after crossover.

    The committee fields are honest.  ``CommitteeID``, ``CommitteeNumber``
    and ``CommitteeName`` matched the committee list on all 98 rows checked
    on 2026-09-17.

    **The counts are referrals, and the columns do not add up.**  In 20261,
    ``Reported + Failed + Incorporated + InCommittee`` equalled ``Referred``
    for only 16 of the 50 committee and prefix pairs.  ``Failed`` counts only
    what a committee killed: House committees failed 31 House bills, while
    the House bill row of the chamber statistics says 494 failed in all.

    ``InSubCommittee`` never exceeded ``InCommittee``.  ``Continued`` sits
    just under ``InCommittee``, but not always: H07 held 20 House bills and
    continued 21.  Nor does ``Continued`` match the next session: 20261 House
    committees continued 182 House bills, and 20271 says 181 came in.

    Which columns are zero follows the biennium.  An odd year has no
    ``Continued``, an even year no ``ContinuedFrom``, and a closed session
    nothing ``InCommittee``, except the 5 Senate bills 20251's Senate
    committees still held.  20271, before it convened, had only the
    ``InCommittee``, ``InSubCommittee`` and ``ContinuedFrom`` columns filled.

    Example (truncated)::

        {"CommitteeID": 1, "CommitteeNumber": "H01",
         "CommitteeName": "Agriculture, Chesapeake and Natural Resources",
         "HReferred": 90, "SReferred": 18, "HReported": 60, "SReported": 17,
         "HFailed": 2, "SFailed": 1, "HContinued": 6, "SContinued": 0, ...}
    """

    CommitteeID: int  # a standing committee; never a subcommittee
    CommitteeNumber: str | None = None  # e.g. "H01"
    CommitteeName: str | None = None
    HReferred: int | None = None  # House bills referred here
    SReferred: int | None = None  # Senate bills referred here
    HInCommittee: int | None = None
    SInCommittee: int | None = None
    HInSubCommittee: int | None = None  # a subset of HInCommittee
    SInSubCommittee: int | None = None
    HReported: int | None = None
    SReported: int | None = None
    HIncorporated: int | None = None
    SIncorporated: int | None = None
    HContinued: int | None = None  # to the next session
    SContinued: int | None = None
    HContinuedFrom: int | None = None  # from the last session
    SContinuedFrom: int | None = None
    HFailed: int | None = None  # killed in this committee only
    SFailed: int | None = None
