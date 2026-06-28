"""Determines reporting period (months and human-readable label) from raw text."""
import re

# All Russian month forms → (month_num, canonical nominative)
_MONTHS: dict[str, tuple[int, str]] = {
    "январь":   (1,  "январь"),
    "января":   (1,  "январь"),
    "январе":   (1,  "январь"),
    "февраль":  (2,  "февраль"),
    "февраля":  (2,  "февраль"),
    "феврале":  (2,  "февраль"),
    "март":     (3,  "март"),
    "марта":    (3,  "март"),
    "марте":    (3,  "март"),
    "апрель":   (4,  "апрель"),
    "апреля":   (4,  "апрель"),
    "апреле":   (4,  "апрель"),
    "май":      (5,  "май"),
    "мая":      (5,  "май"),
    "мае":      (5,  "май"),
    "июнь":     (6,  "июнь"),
    "июня":     (6,  "июнь"),
    "июне":     (6,  "июнь"),
    "июль":     (7,  "июль"),
    "июля":     (7,  "июль"),
    "июле":     (7,  "июль"),
    "август":   (8,  "август"),
    "августа":  (8,  "август"),
    "августе":  (8,  "август"),
    "сентябрь": (9,  "сентябрь"),
    "сентября": (9,  "сентябрь"),
    "сентябре": (9,  "сентябрь"),
    "октябрь":  (10, "октябрь"),
    "октября":  (10, "октябрь"),
    "октябре":  (10, "октябрь"),
    "ноябрь":   (11, "ноябрь"),
    "ноября":   (11, "ноябрь"),
    "ноябре":   (11, "ноябрь"),
    "декабрь":  (12, "декабрь"),
    "декабря":  (12, "декабрь"),
    "декабре":  (12, "декабрь"),
}

# Longest-first so the regex prefers "сентября" over "сентябр" hypothetically
_MONTH_KEYS = "|".join(sorted(_MONTHS.keys(), key=len, reverse=True))
_MONTH_RE = re.compile(rf"(?i)({_MONTH_KEYS})\s*(\d{{4}})?", re.UNICODE)
_YEAR_RE  = re.compile(r"\b(20\d{2})\b")

# Special labels for periods starting in January
_FROM_JANUARY: dict[int, tuple[str, str]] = {
    1:  ("за январь",          "month"),
    2:  ("за январь-февраль",  "custom"),
    3:  ("за 1 квартал",       "quarter"),
    4:  ("за январь-апрель",   "custom"),
    5:  ("за январь-май",      "custom"),
    6:  ("за 1 полугодие",     "half_year"),
    7:  ("за январь-июль",     "custom"),
    8:  ("за январь-август",   "custom"),
    9:  ("за 9 месяцев",       "nine_months"),
    10: ("за январь-октябрь",  "custom"),
    11: ("за январь-ноябрь",   "custom"),
    12: ("за год",             "year"),
}


def normalize_month_name(month_name: str) -> str | None:
    """Return canonical nominative form for a Russian month name/form, or None."""
    entry = _MONTHS.get(month_name.lower().strip())
    return entry[1] if entry else None


def extract_year_from_text(text: str) -> int | None:
    """Return the first 20xx year found in text, or None."""
    m = _YEAR_RE.search(text)
    return int(m.group(1)) if m else None


def detect_months_from_text(text: str) -> list[dict]:
    """
    Find all Russian month names in text, deduplicate, sort by month_num.

    Returns list of dicts: {month, month_num, year, period_key}.
    """
    year_default = extract_year_from_text(text)
    seen: dict[int, dict] = {}

    for m in _MONTH_RE.finditer(text):
        raw_name = m.group(1).lower()
        entry = _MONTHS.get(raw_name)
        if entry is None:
            continue
        month_num, canonical = entry

        year_raw = m.group(2)
        year = int(year_raw) if year_raw else year_default

        if month_num not in seen:
            period_key = f"{year}-{month_num:02d}" if year else f"????-{month_num:02d}"
            seen[month_num] = {
                "month":      canonical,
                "month_num":  month_num,
                "year":       year,
                "period_key": period_key,
            }

    return sorted(seen.values(), key=lambda x: x["month_num"])


def detect_period_label(months: list[dict]) -> dict:
    """
    Build human-readable period description from a sorted list of month dicts.

    Returns dict: {months, month_names, months_count, year,
                   period_short, period_label, period_type}.
    """
    if not months:
        return {
            "months":       [],
            "month_names":  [],
            "months_count": 0,
            "year":         None,
            "period_short": None,
            "period_label": None,
            "period_type":  None,
        }

    month_nums  = [m["month_num"] for m in months]
    month_names = [m["month"]     for m in months]
    period_keys = [m["period_key"] for m in months]

    year = next((m["year"] for m in months if m.get("year")), None)

    count        = len(months)
    starts_jan   = month_nums[0] == 1
    consecutive  = month_nums == list(range(month_nums[0], month_nums[0] + count))

    if starts_jan and consecutive and count in _FROM_JANUARY:
        period_short, period_type = _FROM_JANUARY[count]
    elif consecutive and count == 1:
        period_short = f"за {month_names[0]}"
        period_type  = "month"
    elif consecutive:
        period_short = f"за {month_names[0]}-{month_names[-1]}"
        period_type  = "custom"
    else:
        period_short = "за " + ", ".join(month_names)
        period_type  = "custom"

    period_label = f"{period_short} {year} года" if year else period_short

    return {
        "months":       period_keys,
        "month_names":  month_names,
        "months_count": count,
        "year":         year,
        "period_short": period_short,
        "period_label": period_label,
        "period_type":  period_type,
    }


if __name__ == "__main__":
    text = "Январь 2026 Февраль 2026 Март 2026 Итого 2026г."
    months_found = detect_months_from_text(text)
    result = detect_period_label(months_found)
    print("months:", months_found)
    print("period_label:", result["period_label"])
    expected = "за 1 квартал 2026 года"
    ok = result["period_label"] == expected
    print(f"self-check: {'OK' if ok else f'FAIL (expected: {expected!r})'}")
