#!/usr/bin/env python3
"""Determine whether today is a working day in mainland China."""

import gzip
import re
import urllib.request
from datetime import datetime, timedelta, timezone

CN_ICS_URL = "https://calendars.icloud.com/holidays/cn_zh.ics/"
BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def fetch_ics():
    request = urllib.request.Request(
        CN_ICS_URL,
        headers={"Accept": "text/calendar,text/plain,*/*", "User-Agent": "public-holiday-tool/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
        if "gzip" in response.headers.get("Content-Encoding", "").lower() or raw.startswith(b"\x1f\x8b"):
            raw = gzip.decompress(raw)
        return raw.decode(response.headers.get_content_charset() or "utf-8")


def parse_date(value):
    if re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date()
    if re.fullmatch(r"\d{8}T\d{6}Z?", value):
        return datetime.strptime(value[:15], "%Y%m%dT%H%M%S").date()
    return datetime.strptime(value, "%Y-%m-%d").date()


def is_working_day(text, today):
    # Unfold iCalendar continuation lines before reading event properties.
    text = re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = text.splitlines()
    if "BEGIN:VCALENDAR" not in lines or "END:VCALENDAR" not in lines:
        raise ValueError("Invalid iCalendar feed")

    priorities = []
    event = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            event = {}
        elif line == "END:VEVENT" and event is not None:
            special = event.get("X-APPLE-SPECIAL-DAY", "").upper()
            summary = event.get("SUMMARY", "")
            if special == "ALTERNATE-WORKDAY":
                priority = 0
            elif special == "WORK-HOLIDAY":
                priority = 1
            elif any(marker in summary for marker in ("（班）", "(班)")):
                priority = 2
            elif any(marker in summary for marker in ("（休）", "(休)")):
                priority = 3
            else:
                event = None
                continue
            if any(key in event for key in ("RRULE", "RDATE", "EXDATE", "RECURRENCE-ID")):
                raise ValueError("Recurring holiday or workday event is not supported")
            start = parse_date(event["DTSTART"])
            end = parse_date(event["DTEND"]) if "DTEND" in event else start + timedelta(days=1)
            if start <= today < max(end, start + timedelta(days=1)):
                priorities.append(priority)
            event = None
        elif event is not None and ":" in line:
            name, value = line.split(":", 1)
            event.setdefault(name.split(";", 1)[0].upper(), value)

    return min(priorities) in (0, 2) if priorities else today.weekday() < 5


def main():
    today = datetime.now(BEIJING_TIMEZONE).date()
    return {"is_working_day": is_working_day(fetch_ics(), today)}
