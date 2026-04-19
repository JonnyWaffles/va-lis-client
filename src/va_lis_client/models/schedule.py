"""Schedule (master meeting calendar) models."""

from datetime import datetime

from pydantic import BaseModel

from va_lis_client.models.text import TextFile


class Schedule(BaseModel):
    """A scheduled meeting from ``/Schedule/api/getschedulelistasync``.

    The master meeting calendar — all committee hearings, caucuses, floor
    sessions, conferences.

    Key: ``ScheduleID``.

    **Data quality warning:** ``ScheduleTime`` is frequently free-text
    (e.g. ``"15 minutes after the Senate adjourns"``, ``"TBD"``) rather
    than a parseable time.  ``IsCancelled`` flips frequently with
    last-minute changes.

    ``ScheduleType`` values: Committee, Chamber, Conference, Caucus,
    Docket, Other.

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
    ScheduleFiles: list[TextFile] = []


class ScheduleType(BaseModel):
    """Schedule type reference from ``/Schedule/api/getscheduletypesreferenceasync``.

    6 types observed::

        1=Committee, 2=Chamber, 3=Conference, 4=Caucus, 5=Other, 6=Docket
    """

    ScheduleTypeID: int
    ScheduleType: str


class MeetingRoom(BaseModel):
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
