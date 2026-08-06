# -*- coding: utf-8 -*-
"""The Persian date the report is stamped with.

No date library: the conversion is one well-known integer routine, and adding a
dependency for it would be the larger change. This is the server-side twin of the
client's `src/utils/jalali.js`, which reaches the same calendar through `Intl` —
the two are independent on purpose, since neither runtime can use the other's route.

The clock is fixed to Iran's +03:30 rather than read from the container, which is UTC
in deployment and would date an evening report to the following day. Iran abolished
DST in 2022, so the offset is a constant and not a table.
"""

from datetime import datetime, timedelta, timezone

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
          "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]

_LATIN_TO_PERSIAN = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

# Days elapsed before the first of each Gregorian month, in a non-leap year
_G_DAYS_BEFORE_MONTH = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def fa_digits(value) -> str:
    """Latin digits to Persian ones. The dataset's own numbers are Persian and the
    client renders its counts that way too, so a report full of Latin digits would be
    the only place in the system that is not."""
    return str(value).translate(_LATIN_TO_PERSIAN)


def to_jalali(year: int, month: int, day: int) -> tuple[int, int, int]:
    """Gregorian (year, month, day) -> Jalali (year, month, day)."""
    if year > 1600:
        jy = 979
        year -= 1600
    else:
        jy = 0
        year -= 621
    leap_base = year + 1 if month > 2 else year
    days = (365 * year + (leap_base + 3) // 4 - (leap_base + 99) // 100
            + (leap_base + 399) // 400 - 80 + day + _G_DAYS_BEFORE_MONTH[month - 1])
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:                      # the first six months are 31 days
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def now() -> datetime:
    return datetime.now(IRAN_TZ)


def format_datetime(moment: datetime) -> str:
    """«۱۵ مرداد ۱۴۰۵ — ساعت ۱۳:۲۸», for the report header."""
    moment = moment.astimezone(IRAN_TZ)
    jy, jm, jd = to_jalali(moment.year, moment.month, moment.day)
    clock = f"{moment.hour:02d}:{moment.minute:02d}"
    return f"{fa_digits(jd)} {MONTHS[jm - 1]} {fa_digits(jy)} — ساعت {fa_digits(clock)}"


def date_slug(moment: datetime) -> str:
    """`1405-05-15`, for the downloaded file's name — Latin digits and no spaces,
    because this one is read by a filesystem rather than by a person."""
    moment = moment.astimezone(IRAN_TZ)
    jy, jm, jd = to_jalali(moment.year, moment.month, moment.day)
    return f"{jy:04d}-{jm:02d}-{jd:02d}"
