#!/usr/bin/env python3
"""Check whether a date is a public holiday using an iCalendar feed."""

import calendar
import gzip
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

CN_ICS_URL = "https://calendars.icloud.com/holidays/cn_zh.ics/"
BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Beijing")
CHINA_WEEKEND_DAYS = {5, 6}

APPLE_WORK_HOLIDAY = "WORK-HOLIDAY"
APPLE_ALTERNATE_WORKDAY = "ALTERNATE-WORKDAY"

REST_MARKERS = ("（休）", "(休)")
WORK_MARKERS = ("（班）", "(班)")

WEEKDAY_INDEX = {
    "MO": 0,
    "TU": 1,
    "WE": 2,
    "TH": 3,
    "FR": 4,
    "SA": 5,
    "SU": 6,
}

Property = Dict[str, object]
Event = Dict[str, List[Property]]


def fetch_ics(url: str, timeout_seconds: int = 20) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/calendar,text/plain,*/*",
            "User-Agent": "public-holiday-tool/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read()
        encoding = response.headers.get("Content-Encoding", "").lower()
        if "gzip" in encoding or raw.startswith(b"\x1f\x8b"):
            raw = gzip.decompress(raw)
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def unfold_ics_lines(text: str) -> List[str]:
    lines: List[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw_line:
            continue
        if raw_line[0] in (" ", "\t") and lines:
            lines[-1] += raw_line[1:]
        else:
            lines.append(raw_line)
    return lines


def unescape_ical_text(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def parse_property(line: str) -> Optional[Tuple[str, Property]]:
    if ":" not in line:
        return None

    name_and_params, value = line.split(":", 1)
    parts = name_and_params.split(";")
    name = parts[0].upper()
    params: Dict[str, str] = {}

    for raw_param in parts[1:]:
        if "=" not in raw_param:
            params[raw_param.upper()] = ""
            continue
        key, raw_value = raw_param.split("=", 1)
        params[key.upper()] = raw_value.strip('"')

    return name, {"value": unescape_ical_text(value), "params": params}


def parse_ics(text: str) -> List[Event]:
    events: List[Event] = []
    current: Optional[Event] = None

    for line in unfold_ics_lines(text):
        upper_line = line.upper()
        if upper_line == "BEGIN:VEVENT":
            current = {}
            continue
        if upper_line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None:
            continue

        parsed = parse_property(line)
        if parsed is None:
            continue
        name, prop = parsed
        current.setdefault(name, []).append(prop)

    return events


def first_property(event: Event, name: str) -> Optional[Property]:
    values = event.get(name.upper()) or []
    return values[0] if values else None


def property_value(event: Event, name: str) -> Optional[str]:
    prop = first_property(event, name)
    if prop is None:
        return None
    return str(prop.get("value", ""))


def parse_ical_date(prop: Property) -> date:
    value = str(prop.get("value", "")).strip()
    if not value:
        raise ValueError("empty date value")

    if re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date()

    if re.fullmatch(r"\d{8}T\d{6}Z?", value):
        return datetime.strptime(value[:15], "%Y%m%dT%H%M%S").date()

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)

    raise ValueError("unsupported iCalendar date value: {0}".format(value))


def parse_until(value: str) -> date:
    return parse_ical_date({"value": value, "params": {}})


def parse_rrule(value: str) -> Dict[str, List[str]]:
    rule: Dict[str, List[str]] = {}
    for part in value.split(";"):
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        rule[key.upper()] = [item for item in raw_value.split(",") if item]
    return rule


def parse_int_list(rule: Dict[str, List[str]], key: str) -> List[int]:
    return [int(item) for item in rule.get(key, [])]


def safe_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def month_days(year: int, month: int) -> List[date]:
    _, last_day = calendar.monthrange(year, month)
    return [date(year, month, day) for day in range(1, last_day + 1)]


def parse_byday_token(token: str) -> Tuple[Optional[int], int]:
    match = re.fullmatch(r"([+-]?\d+)?([A-Z]{2})", token)
    if match is None or match.group(2) not in WEEKDAY_INDEX:
        raise ValueError("unsupported BYDAY token: {0}".format(token))
    ordinal = int(match.group(1)) if match.group(1) else None
    return ordinal, WEEKDAY_INDEX[match.group(2)]


def candidates_for_month(year: int, month: int, rule: Dict[str, List[str]], dtstart: date) -> List[date]:
    bymonthday = parse_int_list(rule, "BYMONTHDAY")
    byday = rule.get("BYDAY", [])

    if bymonthday:
        candidates: List[date] = []
        _, last_day = calendar.monthrange(year, month)
        for raw_day in bymonthday:
            day = raw_day if raw_day > 0 else last_day + raw_day + 1
            candidate = safe_date(year, month, day)
            if candidate is not None:
                candidates.append(candidate)
    else:
        candidate = safe_date(year, month, dtstart.day)
        candidates = [candidate] if candidate is not None else []

    if byday:
        all_days = month_days(year, month)
        byday_candidates: Set[date] = set()
        for token in byday:
            ordinal, weekday = parse_byday_token(token)
            matches = [item for item in all_days if item.weekday() == weekday]
            if ordinal is None:
                byday_candidates.update(matches)
            elif ordinal > 0 and ordinal <= len(matches):
                byday_candidates.add(matches[ordinal - 1])
            elif ordinal < 0 and abs(ordinal) <= len(matches):
                byday_candidates.add(matches[ordinal])

        if bymonthday:
            candidates = [item for item in candidates if item in byday_candidates]
        else:
            candidates = sorted(byday_candidates)

    return apply_bysetpos(sorted(set(candidates)), rule)


def apply_bysetpos(candidates: List[date], rule: Dict[str, List[str]]) -> List[date]:
    positions = parse_int_list(rule, "BYSETPOS")
    if not positions:
        return candidates

    selected: List[date] = []
    for position in positions:
        if position > 0 and position <= len(candidates):
            selected.append(candidates[position - 1])
        elif position < 0 and abs(position) <= len(candidates):
            selected.append(candidates[position])
    return sorted(set(selected))


def yearly_candidates(year: int, rule: Dict[str, List[str]], dtstart: date) -> List[date]:
    months = parse_int_list(rule, "BYMONTH") or [dtstart.month]
    candidates: List[date] = []
    for month in months:
        candidates.extend(candidates_for_month(year, month, rule, dtstart))
    return sorted(set(candidates))


def monthly_candidates(year: int, month: int, rule: Dict[str, List[str]], dtstart: date) -> List[date]:
    bymonth = parse_int_list(rule, "BYMONTH")
    if bymonth and month not in bymonth:
        return []
    return candidates_for_month(year, month, rule, dtstart)


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    _, last_day = calendar.monthrange(year, month)
    return date(year, month, min(value.day, last_day))


def iter_rrule_dates(dtstart: date, rule_text: str, lower: date, upper: date) -> Iterable[date]:
    rule = parse_rrule(rule_text)
    freq = (rule.get("FREQ") or [""])[0].upper()
    interval = int((rule.get("INTERVAL") or ["1"])[0])
    count_limit = int(rule["COUNT"][0]) if "COUNT" in rule else None
    until = parse_until(rule["UNTIL"][0]) if "UNTIL" in rule else None
    emitted = 0

    def can_emit(candidate: date) -> bool:
        if candidate < dtstart:
            return False
        if until is not None and candidate > until:
            return False
        return True

    if freq == "YEARLY":
        year = dtstart.year
        while year <= upper.year:
            if (year - dtstart.year) % interval == 0:
                for candidate in yearly_candidates(year, rule, dtstart):
                    if not can_emit(candidate):
                        continue
                    emitted += 1
                    if count_limit is not None and emitted > count_limit:
                        return
                    if lower <= candidate <= upper:
                        yield candidate
            year += 1
        return

    if freq == "MONTHLY":
        current = date(dtstart.year, dtstart.month, 1)
        end_month = date(upper.year, upper.month, 1)
        month_index = 0
        while current <= end_month:
            if month_index % interval == 0:
                for candidate in monthly_candidates(current.year, current.month, rule, dtstart):
                    if not can_emit(candidate):
                        continue
                    emitted += 1
                    if count_limit is not None and emitted > count_limit:
                        return
                    if lower <= candidate <= upper:
                        yield candidate
            current = add_months(current, 1)
            month_index += 1
        return

    if freq == "WEEKLY":
        byday_values = rule.get("BYDAY") or []
        weekdays = [parse_byday_token(item)[1] for item in byday_values] or [dtstart.weekday()]
        current = dtstart
        while current <= upper:
            weeks_since_start = (current - dtstart).days // 7
            if weeks_since_start % interval == 0 and current.weekday() in weekdays and can_emit(current):
                emitted += 1
                if count_limit is not None and emitted > count_limit:
                    return
                if lower <= current <= upper:
                    yield current
            current += timedelta(days=1)
        return

    if freq == "DAILY":
        current = dtstart
        while current <= upper:
            if can_emit(current):
                emitted += 1
                if count_limit is not None and emitted > count_limit:
                    return
                if lower <= current <= upper:
                    yield current
            current += timedelta(days=interval)
        return

    if lower <= dtstart <= upper:
        yield dtstart


def event_dates(event: Event) -> Tuple[date, int]:
    dtstart_prop = first_property(event, "DTSTART")
    if dtstart_prop is None:
        raise ValueError("VEVENT is missing DTSTART")

    dtstart = parse_ical_date(dtstart_prop)
    dtend_prop = first_property(event, "DTEND")
    if dtend_prop is None:
        return dtstart, 1

    dtend = parse_ical_date(dtend_prop)
    duration_days = max((dtend - dtstart).days, 1)
    return dtstart, duration_days


def matching_occurrences(event: Event, target_date: date) -> List[Dict[str, str]]:
    dtstart, duration_days = event_dates(event)
    lower = target_date - timedelta(days=duration_days - 1)
    upper = target_date
    rrule = property_value(event, "RRULE")
    starts = (
        iter_rrule_dates(dtstart, rrule, lower, upper)
        if rrule
        else [dtstart]
    )

    matches: List[Dict[str, str]] = []
    for start in starts:
        end = start + timedelta(days=duration_days)
        if start <= target_date < end:
            matches.append(
                {
                    "summary": property_value(event, "SUMMARY") or "",
                    "special_day": property_value(event, "X-APPLE-SPECIAL-DAY") or "",
                    "start": start.isoformat(),
                    "end_exclusive": end.isoformat(),
                }
            )

    return matches


def clean_summary(summary: str) -> str:
    cleaned = summary.strip()
    for marker in REST_MARKERS + WORK_MARKERS:
        cleaned = cleaned.replace(marker, "")
    return cleaned.strip()


def today_in_beijing() -> date:
    return datetime.now(BEIJING_TIMEZONE).date()


def select_classification(
        matches: List[Dict[str, str]],
        target_date: date,
) -> Dict[str, object]:
    is_weekend = target_date.weekday() in CHINA_WEEKEND_DAYS
    base_working_day = not is_weekend

    alternate_workday = [
        item for item in matches if item["special_day"].upper() == APPLE_ALTERNATE_WORKDAY
    ]
    if alternate_workday:
        selected = alternate_workday[0]
        return {
            "is_public_holiday": False,
            "is_working_day": True,
            "holiday_name": None,
            "reason": APPLE_ALTERNATE_WORKDAY,
            "matched_event": selected,
        }

    work_holiday = [
        item for item in matches if item["special_day"].upper() == APPLE_WORK_HOLIDAY
    ]
    if work_holiday:
        selected = work_holiday[0]
        return {
            "is_public_holiday": True,
            "is_working_day": False,
            "holiday_name": clean_summary(selected["summary"]),
            "reason": APPLE_WORK_HOLIDAY,
            "matched_event": selected,
        }

    marker_workday = [
        item for item in matches if any(marker in item["summary"] for marker in WORK_MARKERS)
    ]
    if marker_workday:
        selected = marker_workday[0]
        return {
            "is_public_holiday": False,
            "is_working_day": True,
            "holiday_name": None,
            "reason": "SUMMARY_WORK_MARKER",
            "matched_event": selected,
        }

    marker_holiday = [
        item for item in matches if any(marker in item["summary"] for marker in REST_MARKERS)
    ]
    if marker_holiday:
        selected = marker_holiday[0]
        return {
            "is_public_holiday": True,
            "is_working_day": False,
            "holiday_name": clean_summary(selected["summary"]),
            "reason": "SUMMARY_REST_MARKER",
            "matched_event": selected,
        }

    return {
        "is_public_holiday": False,
        "is_working_day": base_working_day,
        "holiday_name": None,
        "reason": "WEEKEND" if is_weekend else "WEEKDAY",
        "matched_event": None,
    }


def check_public_holiday(target_date: Optional[str] = None) -> Dict[str, object]:
    lookup_date = (
        date.fromisoformat(target_date)
        if target_date
        else today_in_beijing()
    )

    ics_text = fetch_ics(CN_ICS_URL)
    events = parse_ics(ics_text)
    matches: List[Dict[str, str]] = []
    for event in events:
        try:
            matches.extend(matching_occurrences(event, lookup_date))
        except ValueError:
            continue

    classification = select_classification(matches, lookup_date)
    return {
        "date": lookup_date.isoformat(),
        "is_working_day": classification["is_working_day"],
        "is_public_holiday": classification["is_public_holiday"],
        "holiday_name": classification["holiday_name"],
    }


def main(input_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = input_data or {}
    raw_date = data.get("date") or data.get("target_date")
    target_date = str(raw_date) if raw_date else None
    return check_public_holiday(target_date=target_date)
