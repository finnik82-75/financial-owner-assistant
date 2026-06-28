"""Detects business projects and directions from raw text."""
import re

# Canonical column order for known business groups.
# When 3+ projects from a group are found, the detected set is re-sorted to match
# this order so pnl_matrix_extractor maps values to the correct columns.
MEDIA_PROJECT_ORDER: list[str] = [
    "Европа+",
    "Авторадио",
    "Ретро FM",
    'Сайт "Забmedia.ru"',
    "ЗАБ ТВ 24",
    "Наружная реклама",
]

RETAIL_PROJECT_ORDER: list[str] = [
    "Intimissimi",
    "Calzedonia",
]

# Each entry: (compiled pattern, canonical name)
# More-specific patterns come before overlapping shorter ones.
_PROJECT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"европа\s*\+",                         re.IGNORECASE | re.UNICODE), "Европа+"),
    # Авторадио: handle "Авто-радио", "Авто-\nрадио", "Авторадио", "Авто радио"
    (re.compile(r"авто\s*[-–\xad]?\s*радио",            re.IGNORECASE | re.UNICODE), "Авторадио"),
    (re.compile(r"ретро\s*(?:fm|фм)",                   re.IGNORECASE | re.UNICODE), "Ретро FM"),
    # "Сайт" prefix comes before bare "забmedia.ru" to yield correct canonical name
    (re.compile(r'сайт\s+["\']?забmedia\.ru["\']?',     re.IGNORECASE | re.UNICODE), 'Сайт "Забmedia.ru"'),
    (re.compile(r"забmedia\.ru",                         re.IGNORECASE | re.UNICODE), 'Сайт "Забmedia.ru"'),
    (re.compile(r"заб\s*тв\s*24",                        re.IGNORECASE | re.UNICODE), "ЗАБ ТВ 24"),
    (re.compile(r"наружная\s+реклама",                   re.IGNORECASE | re.UNICODE), "Наружная реклама"),
    (re.compile(r"intimissimi",                          re.IGNORECASE),              "Intimissimi"),
    (re.compile(r"интимиссими",                          re.IGNORECASE | re.UNICODE), "Intimissimi"),
    (re.compile(r"calzedonia",                           re.IGNORECASE),              "Calzedonia"),
    (re.compile(r"кальцедония",                          re.IGNORECASE | re.UNICODE), "Calzedonia"),
]


def clean_text_for_project_detection(text: str) -> str:
    """
    Normalize OCR/PDF artifacts before project pattern matching.

    Handles:
    - Soft hyphen (U+00AD) → regular hyphen
    - Newlines within cell values (e.g. "Авто-\\nрадио") → space
    - Non-breaking space → regular space
    - Multiple whitespace → single space
    """
    text = text.replace("\xad", "-")     # soft hyphen → hyphen
    text = text.replace("�", " ")   # replacement char → space
    text = re.sub(r"[\n\r\t\xa0]+", " ", text)   # newlines / NBSP → space
    text = re.sub(r" {2,}", " ", text)            # collapse runs of spaces
    return text.strip()


def normalize_project_name(name: str) -> str:
    """Return canonical project name if name matches a known pattern, else return name unchanged."""
    cleaned = clean_text_for_project_detection(name.strip())
    for pattern, canonical in _PROJECT_PATTERNS:
        if pattern.fullmatch(cleaned):
            return canonical
    return name.strip()


def is_known_project(name: str) -> bool:
    """Return True if name contains a known project pattern."""
    cleaned = clean_text_for_project_detection(name)
    for pattern, _ in _PROJECT_PATTERNS:
        if pattern.search(cleaned):
            return True
    return False


def normalize_project_order(projects: list[str]) -> list[str]:
    """
    Re-sort detected projects to match the known column order of standard report layouts.

    Rules (first matching group wins):
    - If 3+ of the found projects belong to MEDIA_PROJECT_ORDER → sort by that order.
    - If any of the found projects belong to RETAIL_PROJECT_ORDER → sort by that order.
    - Projects not covered by any standard order are appended in their original position.
    - Duplicates are removed (first occurrence kept).
    """
    seen: set[str] = set()
    unique: list[str] = []
    for p in projects:
        if p not in seen:
            seen.add(p)
            unique.append(p)

    proj_set = set(unique)

    def _apply_order(ref_order: list[str]) -> list[str]:
        ordered   = [p for p in ref_order if p in proj_set]
        remainder = [p for p in unique    if p not in set(ref_order)]
        return ordered + remainder

    media_hits = sum(1 for p in MEDIA_PROJECT_ORDER if p in proj_set)
    if media_hits >= 3:
        return _apply_order(MEDIA_PROJECT_ORDER)

    retail_hits = sum(1 for p in RETAIL_PROJECT_ORDER if p in proj_set)
    if retail_hits >= 1:
        return _apply_order(RETAIL_PROJECT_ORDER)

    return unique


def detect_projects_from_text(text: str) -> list[str]:
    """
    Find all known project names in text.

    Returns projects in canonical column order (via normalize_project_order).
    The input is cleaned before matching so OCR artifacts are handled.
    """
    cleaned = clean_text_for_project_detection(text)

    seen: set[str] = set()
    matches: list[tuple[int, str]] = []

    for pattern, canonical in _PROJECT_PATTERNS:
        m = pattern.search(cleaned)
        if m and canonical not in seen:
            matches.append((m.start(), canonical))
            seen.add(canonical)

    matches.sort(key=lambda x: x[0])
    found = [canonical for _, canonical in matches]
    return normalize_project_order(found)


if __name__ == "__main__":
    cases = [
        (
            'Европа + Авто-радио Ретро FM САЙТ "Забmedia.ru" ЗАБ ТВ 24 Наружная реклама',
            ["Европа+", "Авторадио", "Ретро FM", 'Сайт "Забmedia.ru"', "ЗАБ ТВ 24", "Наружная реклама"],
        ),
        (
            "Европа + Авто-\nрадио Ретро FM САЙТ\n\"Забmedia.ru\" ЗАБ ТВ 24 Наружная\nреклама",
            ["Европа+", "Авторадио", "Ретро FM", 'Сайт "Забmedia.ru"', "ЗАБ ТВ 24", "Наружная реклама"],
        ),
    ]
    for text, expected in cases:
        result = detect_projects_from_text(text)
        ok = result == expected
        print(f"{'OK' if ok else 'FAIL'}: {result}")
        if not ok:
            print(f"  expected: {expected}")
