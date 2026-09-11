"""Schedule (master meeting calendar) models."""

from datetime import datetime

from va_lis_client.models.common import LISModel


class ScheduleFile(LISModel):
    """An attachment on a meeting, from ``Schedule.ScheduleFiles``.

    Usually an agenda PDF.  415 of the 3,631 meetings in the unfiltered
    schedule list carry one, and a few carry up to five.  This is not the
    bill text file shape: there is no ``LegislationTextID`` or
    ``TextFormatID``, and ``FileFormat`` is null on every observed row.

    Example::

        {"FileID": 1005518,
         "FileURL": "https://lis.blob.core.windows.net/files/1005518.PDF",
         "ModificationDate": "2024-06-18T15:08:10.727",
         "IsGenerated": true, "IsActive": true, "IsPublic": true,
         "IsDeleted": null, "BlobURL": null, "FileFormat": null,
         "FileContents": null, "Success": false, "FailureMessage": null}
    """

    FileID: int | None = None
    FileURL: str | None = None
    BlobURL: str | None = None
    FileFormat: str | None = None  # null on every observed row
    FileContents: str | None = None
    ModificationDate: datetime | None = None
    IsGenerated: bool = False
    IsActive: bool = True
    IsPublic: bool = True
    IsDeleted: bool | None = None


class Schedule(LISModel):
    """A scheduled meeting from ``/Schedule/api/getschedulelistasync``.

    The master meeting calendar — all committee hearings, caucuses, floor
    sessions, conferences.

    Key: ``ScheduleID``.

    **Data quality warning:** ``ScheduleTime`` is frequently free-text
    (e.g. ``"15 minutes after the Senate adjourns"``, ``"TBD"``) rather
    than a parseable time, and blank on 35 of the 147 meetings in the week
    of 2026-02-02.  ``IsCancelled`` flips frequently with last-minute
    changes; 8 of those 147 were cancelled.

    ``ScheduleType`` values: Committee, Chamber, Conference, Caucus,
    Docket, Other.  Rows that are not committee meetings (caucuses, press
    events, ``"Other"``) omit ``OwnerID`` and ``CommitteeNumber`` from the
    JSON rather than sending null, so both are optional here.  Committee
    rows name the committee: ``OwnerID`` 8, ``OwnerName`` "House Courts of
    Justice", ``CommitteeNumber`` "H08"; a subcommittee reads "H10001".

    Example::

        {"ScheduleID": 3626, "OwnerID": 23,
         "OwnerName": "Senate Commerce and Labor",
         "CommitteeNumber": "S02",
         "ScheduleTypeID": 6, "ScheduleType": "Docket",
         "VoteRoomID": 66,
         "RoomDescription": "Senate Room A, Room 305, General Assembly Building",
         "ScheduleDate": "2026-03-09T16:30:00",
         "ScheduleTime": "15 minutes after the Senate adjourns",
         "IsCancelled": false, "IsPublic": true}
    """

    ScheduleID: int  # surrogate PK
    VersionSequence: int | None = None
    DisplaySequence: int | None = None  # display ordering
    OwnerID: int | None = None  # CommitteeID for committee meetings
    OwnerName: str | None = None  # e.g. "Senate Commerce and Labor"
    CommitteeNumber: str | None = None  # e.g. "S02" (when applicable)
    Description: str | None = None  # may contain HTML with agenda/video links
    ScheduleTypeID: int | None = None  # 1=Committee, 2=Chamber, 4=Caucus, 6=Docket
    ScheduleType: str | None = None  # "Committee", "Chamber", "Docket", etc.
    VoteRoomID: int | None = None  # FK to MeetingRoom
    RoomDescription: str | None = None  # e.g. "Senate Room A, Room 305, ..."
    ScheduleDate: datetime | None = None  # the date (time may be in ScheduleTime)
    ScheduleTime: str | None = None  # OFTEN FREE-TEXT, not always parseable
    Comments: str | None = None
    IsCancelled: bool = False
    IsPublic: bool = True
    LinkURL: str | None = None  # link to LIS page for this meeting
    OnCalendar: bool = False
    ScheduleFiles: list[ScheduleFile] = []  # agenda attachments; absent on most rows


class ScheduleType(LISModel):
    """Schedule type reference from ``/Schedule/api/getscheduletypesreferenceasync``.

    6 types observed::

        1=Committee, 2=Chamber, 3=Conference, 4=Caucus, 5=Other, 6=Docket
    """

    ScheduleTypeID: int
    ScheduleType: str


class MeetingRoom(LISModel):
    """Meeting room reference from ``/Schedule/api/getmeetingroomsreferenceasync``.

    Query by ``chamberCode`` (H or S).  Includes both GAB rooms and Capitol rooms.

    Example::

        {"VoteRoomID": 66,
         "Description": "Senate Room A, Room 305, General Assembly Building",
         "SeatCount": 0, "ChamberCode": "S"}
    """

    VoteRoomID: int  # PK, referenced by Schedule.VoteRoomID
    Description: str  # full room description
    RoomNumber: str | None = None
    SeatCount: int = 0
    ChamberCode: str  # "H" or "S"
