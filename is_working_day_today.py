#!/usr/bin/env python3
"""Return whether today in UTC+8 is a working day in mainland China."""

import gzip
import urllib.request
from datetime import date, datetime, timedelta, timezone

CN_ICS_URL = "https://calendars.icloud.com/holidays/cn_zh.ics/"
UTC_PLUS_8 = timezone(timedelta(hours=8))


def fetch_calendar() -> str:
    request = urllib.request.Request(
        CN_ICS_URL,
        headers={"Accept": "text/calendar,text/plain,*/*", "User-Agent": "public-holiday-tool/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        content = response.read()
        if "gzip" in response.headers.get("Content-Encoding", "").lower() or content.startswith(b"\x1f\x8b"):
            content = gzip.decompress(content)
        return content.decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def calendar_events(text: str) -> list[dict[str, list[str]]]:
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        elif line:
            lines.append(line)

    events: list[dict[str, list[str]]] = []
    current = None
    for line in lines:
        if line.upper() == "BEGIN:VEVENT":
            current = {}
        elif line.upper() == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            name, value = line.split(":", 1)
            key = name.split(";", 1)[0].upper()
            current.setdefault(key, []).append(value)
    return events


def event_covers_today(event: dict[str, list[str]], today: date) -> bool:
    starts = event.get("DTSTART", [])
    if not starts:
        return False
    try:
        start = datetime.strptime(starts[0][:8], "%Y%m%d").date()
        ends = event.get("DTEND", [])
        end = datetime.strptime(ends[0][:8], "%Y%m%d").date() if ends else start + timedelta(days=1)
    except ValueError:
        return False

    if start <= today < end:
        return True

    # Apple uses fixed-date yearly rules for recurring calendar entries.
    rules = dict(part.split("=", 1) for part in (event.get("RRULE", [""])[0]).split(";") if "=" in part)
    if rules.get("FREQ") != "YEARLY" or today.year < start.year:
        return False
    interval = int(rules.get("INTERVAL", "1"))
    year_offset = today.year - start.year
    if interval < 1 or year_offset % interval:
        return False
    if "COUNT" in rules and year_offset // interval >= int(rules["COUNT"]):
        return False
    try:
        occurrence = date(today.year, start.month, start.day)
    except ValueError:
        return False
    if "UNTIL" in rules and occurrence > datetime.strptime(rules["UNTIL"][:8], "%Y%m%d").date():
        return False
    return occurrence <= today < occurrence + (end - start)


def is_today_working_day() -> bool:
    today = datetime.now(UTC_PLUS_8).date()
    alternate_workday = work_holiday = marked_workday = marked_holiday = False

    for event in calendar_events(fetch_calendar()):
        if not event_covers_today(event, today):
            continue
        special_day = (event.get("X-APPLE-SPECIAL-DAY", [""])[0]).upper()
        summary = event.get("SUMMARY", [""])[0]
        alternate_workday |= special_day == "ALTERNATE-WORKDAY"
        work_holiday |= special_day == "WORK-HOLIDAY"
        marked_workday |= "（班）" in summary or "(班)" in summary
        marked_holiday |= "（休）" in summary or "(休)" in summary

    if alternate_workday:
        return True
    if work_holiday:
        return False
    if marked_workday:
        return True
    if marked_holiday:
        return False
    return today.weekday() < 5


if __name__ == "__main__":
    print(is_today_working_day())
