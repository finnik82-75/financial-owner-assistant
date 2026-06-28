"""
Q&A service: answer user questions based on the pre-built analysis_context.

LLM only explains pre-calculated data — it does NOT compute any figures itself.
Exception: management benchmarks and scenario calculations are computed in Python
and passed labeled as "not from the report" so the LLM can use them accurately.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import settings

# ─── Label maps (local copies — no circular imports) ──────────────────────────

_PNL_LABELS_RU: dict[str, str] = {
    "revenue":                     "Выручка",
    "cogs":                        "Себестоимость",
    "gross_profit":                "Валовая прибыль",
    "operating_expenses":          "Операционные расходы",
    "payroll":                     "ФОТ (зарплата и взносы)",
    "payroll_related":             "ФОТ-связанные расходы",
    "rent":                        "Аренда",
    "marketing":                   "Маркетинг",
    "bank_fees":                   "Банковские комиссии",
    "communication":               "Связь",
    "legal_services":              "Юридические услуги",
    "it_expenses":                 "ИТ-расходы",
    "depreciation":                "Амортизация",
    "other_operating_expenses":    "Прочие операционные расходы",
    "taxes":                       "Налоги",
    "intercompany_turnover":       "ВГО (внутригрупповые)",
    "intercompany_services":       "ВГО-услуги",
    "intercompany_interest":       "ВГО-проценты по займу",
    "operating_profit":            "Прибыль от осн. деятельности",
    "profit_after_other_activity": "Прибыль с учётом прочей деятельности",
    "ebitda_proxy":                "EBITDA (proxy)",
    "net_profit":                  "Чистая прибыль",
    "net_profit_proxy":            "Чистая прибыль (proxy)",
}

_CF_LABELS_RU: dict[str, str] = {
    "cash_start":          "Остаток на начало",
    "operating_inflows":   "Поступления (операционные)",
    "operating_outflows":  "Выплаты (операционные)",
    "operating_cashflow":  "Операционный денежный поток",
    "investment_cashflow": "Инвестиционный поток",
    "financial_cashflow":  "Финансовый поток",
    "net_cashflow":        "Чистый денежный поток",
    "cash_end":            "Остаток на конец",
    "owner_withdrawals":   "Вывод прибыли / дивиденды",
    "advances":            "Авансы",
}

_CF_DETAIL_LABELS_RU: dict[str, str] = {
    "customer_inflows":               "Поступления от клиентов",
    "media_services_inflows":         "Поступления от медиауслуг",
    "intercompany_operating_inflows": "ВГО (операционная)",
    "intercompany_financing":         "ВГО (финансирование)",
    "it_communication_services":      "IT, связь, сервисы",
    "bank_fees":                      "Банковские комиссии",
    "payroll":                        "Заработная плата",
    "marketing":                      "Маркетинг и реклама",
    "employee_taxes":                 "Налоги за сотрудников",
    "social_contributions":           "Взносы в фонды",
    "personal_income_tax":            "НДФЛ",
    "income_tax":                     "Налоги на доходы",
    "materials":                      "Оплата за ТМЦ/материалы",
    "contractors":                    "Оплата подрядчикам",
    "other_operating_outflows":       "Прочие операционные выплаты",
}

# ─── System prompt ────────────────────────────────────────────────────────────

_QA_SYSTEM_PROMPT = """Ты — финансовый директор и консультант для собственника бизнеса.
Ты отвечаешь на уточняющие вопросы по уже сформированному управленческому отчёту.

Правила:
1. Отвечай на смысл вопроса, а не просто повторяй цифры из контекста.
   Если спрашивают «сколько нужно», «какой приемлем», «что является нормой» —
   сначала покажи факт, затем дай управленческий ориентир (если он передан в дополнительных расчётах).

2. Если вопрос содержит слова «приемлем», «нормально», «должен быть», «сколько нужно»,
   «норматив», «допустим», «ориентир», «оптимальн»:
   — сначала покажи фактические данные из отчёта;
   — затем используй управленческий ориентир из дополнительных расчётов (если они есть);
   — ЯВНО пометь ориентир: «Это управленческий ориентир, не данные из отчёта»;
   — не выдавай ориентир как точную норму или стандарт.

3. Используй только данные из финансового контекста ниже.
   Исключение: если ниже есть раздел «ДОПОЛНИТЕЛЬНЫЙ РАСЧЁТ» — используй его и явно отметь источник.

4. Не придумывай данные, которых нет в контексте.
   Если данных нет — скажи: «В загруженных отчётах этих данных нет.»

5. Не пересчитывай показатели, если они уже рассчитаны в KPI.

6. БДР показывает прибыль, расходы и начисления. ДДС показывает движение денег.
   Не делай выводы о прибыли по ДДС и о деньгах по БДР.

7. Не смешивай ФОТ из БДР и зарплатные выплаты из ДДС:
   — ФОТ/выручка (payroll_ratio) рассчитан от ФОТ по БДР;
   — «Заработная плата» в ДДС — это денежная выплата, а не начисленный ФОТ из БДР;
   — если ссылаешься на статью ДДС, пиши «денежная выплата зарплаты по ДДС», не называй её ФОТ/выручка;
   — если сравниваешь статью ДДС с выручкой, явно укажи: «это сопоставление денежной выплаты с выручкой, не показатель ФОТ/выручка из БДР».

8. Не называй налоги, ВГО, финансирование или дивиденды проблемой сами по себе.

9. Каждый вывод должен быть связан с конкретной цифрой.

10. Если вопрос про точку безубыточности — обязательно укажи:
    «Расчёт является proxy, потому что расходы не разделены полностью на постоянные и переменные.»

11. Если вопрос содержит сценарий («что будет если», «если увеличить», «если снизить», «при условии»):
    — определи, какой показатель меняется и за какой период (месяц или весь квартал);
    — если период неоднозначен, явно укажи своё допущение;
    — если есть раздел «ДОПОЛНИТЕЛЬНЫЙ РАСЧЁТ СЦЕНАРИЯ» — используй его цифры;
    — не выдавай сценарий как факт отчёта; всегда пиши «proxy-сценарий» или «при прочих равных»;
    — предупреди, что в реальности вместе с выручкой могут вырасти расходы.

12. Диалоговые follow-up вопросы:
    — если вопрос короткий или уточняющий, используй историю для понимания контекста;
    — отвечай компактно: 2–4 предложения или маркированный список без заголовков;
    — не повторяй полный предыдущий ответ.

13. Приоритет нового вопроса:
    — новый вопрос имеет приоритет над историей диалога;
    — если в запросе есть пометка [СМЕНА ТЕМЫ] — не продолжай предыдущую тему.

14. После ответа НЕ выводи список предлагаемых вопросов. Запрещено.

Формат ответа: Markdown.
— Для развёрнутого вопроса: заголовки ## Ответ / ## Расшифровка / ## Ограничения.
— Для короткого уточняющего: без заголовков, 2–4 предложения или список.
— Раздел ## Ограничения — только если есть proxy-расчёт, ориентир вместо нормы, отсутствующие данные."""


# ─── Security ─────────────────────────────────────────────────────────────────

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,120}$")


def _is_safe_analysis_id(analysis_id: str) -> bool:
    return bool(_SAFE_ID_RE.match(analysis_id))


# ─── Format helpers ───────────────────────────────────────────────────────────

def _rub(v) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}{abs(v):,.0f}".replace(",", " ")


def _signed(v) -> str:
    if v is None:
        return "—"
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return "—"
    prefix = "+" if fv > 0 else ""
    return f"{prefix}{_rub(v)}"


def _pct(ratio: float | None) -> str:
    if ratio is None:
        return "—"
    return f"{ratio * 100:.1f}%".replace(".", ",")


# ─── Topic detection ──────────────────────────────────────────────────────────

_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "payroll":    ["фот", "зарплат", "заработн", "фонд оплаты"],
    "month":      ["март", "феврал", "январ", "апрел", "май", "июн", "июл",
                   "август", "сентябр", "октябр", "ноябр", "декабр", "месяц", "динамик"],
    "project":    ["проект", "сайт", "авторадио", "европа+", "европа ", "ретро",
                   "заб тв", "наружн", "забmedia", "направлен"],
    "cashflow":   ["ддс", "деньг", "денег", "денежн", "остаток", "касс", "поток",
                   "выплат", "поступлен", "счет"],
    "pnl":        ["бдр", "прибыль", "расход", "выручк", "маржа"],
    "break_even": ["безубыточ", "нулев", "до нуля", "покрытие точк"],
    "quality":    ["качеств", "proxy", "ограничен"],
}


def detect_question_topic(question: str) -> str:
    """Return topic slug or 'unknown'."""
    q = question.lower()
    for topic, keywords in _TOPIC_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return topic
    return "unknown"


def detect_last_topic(history: list[dict]) -> str:
    """Return the topic of the most recent question in history, or 'unknown'."""
    if not history:
        return "unknown"
    for entry in reversed(history[-3:]):
        t = detect_question_topic(entry.get("question", ""))
        if t != "unknown":
            return t
    return "unknown"


# ─── Payroll benchmark helpers ────────────────────────────────────────────────

_PAYROLL_KW = frozenset(["фот", "фонд оплаты", "зарплат", "заработн"])
_BENCHMARK_KW = frozenset([
    "приемлем", "нормальн", "должен быть", "должно быть", "должна быть",
    "сколько должен", "сколько нужно", "сколько должно", "норматив",
    "допустим", "ориентир", "оптимальн", "рекомендован",
    "какой должен", "какой нужен",
])


def detect_payroll_benchmark_question(question: str) -> bool:
    """Return True if the question asks for a payroll norm/benchmark."""
    q = question.lower()
    return any(kw in q for kw in _PAYROLL_KW) and any(kw in q for kw in _BENCHMARK_KW)


def calculate_payroll_benchmark(analysis_context: dict) -> dict | None:
    """
    Compute payroll benchmark figures (35–40% of revenue).
    All monetary values are returned as positive numbers.
    Returns None if essential data is missing.
    """
    kpi    = analysis_context.get("kpi") or {}
    sk     = kpi.get("summary_kpi") or {}
    pnl    = analysis_context.get("pnl") or {}
    totals = pnl.get("totals") or {}

    revenue: float | None = None
    rev_entry = sk.get("revenue")
    if rev_entry and rev_entry.get("value") is not None:
        try:
            revenue = float(rev_entry["value"])
        except (TypeError, ValueError):
            pass
    if revenue is None:
        raw = totals.get("revenue")
        if raw is not None:
            try:
                revenue = float(raw)
            except (TypeError, ValueError):
                pass
    if not revenue or revenue <= 0:
        return None

    raw_payroll = totals.get("payroll")
    if raw_payroll is None:
        return None
    try:
        current_payroll = abs(float(raw_payroll))
    except (TypeError, ValueError):
        return None
    if current_payroll <= 0:
        return None

    current_ratio: float | None = None
    pr_entry = sk.get("payroll_ratio")
    if pr_entry and pr_entry.get("value") is not None:
        try:
            current_ratio = abs(float(pr_entry["value"]))
        except (TypeError, ValueError):
            pass
    if current_ratio is None:
        current_ratio = current_payroll / revenue

    low_ratio   = 0.35
    high_ratio  = 0.40
    low_amount  = revenue * low_ratio
    high_amount = revenue * high_ratio

    return {
        "revenue":               revenue,
        "current_payroll":       current_payroll,
        "current_payroll_ratio": current_ratio,
        "benchmark_low_ratio":   low_ratio,
        "benchmark_high_ratio":  high_ratio,
        "benchmark_low_amount":  low_amount,
        "benchmark_high_amount": high_amount,
        "excess_over_high":      current_payroll - high_amount,
        "excess_over_low":       current_payroll - low_amount,
    }


def calculate_custom_percent_benchmark(analysis_context: dict, percent: float) -> dict | None:
    """Compute payroll benchmark with a user-specified percentage."""
    bm = calculate_payroll_benchmark(analysis_context)
    if not bm:
        return None
    custom_amount = bm["revenue"] * percent
    return {
        **bm,
        "custom_percent": percent,
        "custom_amount":  custom_amount,
        "custom_excess":  bm["current_payroll"] - custom_amount,
    }


def _format_payroll_benchmark_block(bm: dict) -> str:
    rev       = bm["revenue"]
    payroll   = bm["current_payroll"]
    ratio     = bm["current_payroll_ratio"]
    low_r     = bm["benchmark_low_ratio"]
    high_r    = bm["benchmark_high_ratio"]
    low_a     = bm["benchmark_low_amount"]
    high_a    = bm["benchmark_high_amount"]
    excess_hi = bm["excess_over_high"]

    lines = [
        "\n=== ДОПОЛНИТЕЛЬНЫЙ РАСЧЁТ ДЛЯ ОТВЕТА О ПРИЕМЛЕМОМ ФОТ ===",
        "(Эти данные рассчитаны в Python специально для ответа на вопрос о нормативе ФОТ)",
        f"- Фактическая выручка: {_rub(rev)} ₽",
        f"- Фактический ФОТ (БДР): {_rub(payroll)} ₽",
        f"- Фактический ФОТ/выручка: {_pct(ratio)}",
        f"- Управленческий ориентир ФОТ: {low_r * 100:.0f}–{high_r * 100:.0f}% выручки",
        "  (ВАЖНО: это управленческий ориентир, НЕ данные из отчёта)",
        f"- Ориентировочный ФОТ при текущей выручке: {_rub(low_a)}–{_rub(high_a)} ₽",
    ]
    if excess_hi > 0:
        lines.append(f"- Превышение ФОТ над верхней границей ориентира: {_rub(excess_hi)} ₽")
    else:
        lines.append(f"- Текущий ФОТ в пределах ориентира (ниже верхней границы на {_rub(abs(excess_hi))} ₽)")

    if "custom_percent" in bm:
        pct    = bm["custom_percent"]
        amt    = bm["custom_amount"]
        excess = bm["custom_excess"]
        lines.append(f"\n[РАСЧЁТ ПО ЗАПРОШЕННОМУ ПРОЦЕНТУ {pct * 100:.0f}%]")
        lines.append(f"- При {pct * 100:.0f}% от выручки {_rub(rev)} ₽: целевой ФОТ = {_rub(amt)} ₽")
        if excess > 0:
            lines.append(f"- Текущий ФОТ выше этого значения на {_rub(excess)} ₽")
        else:
            lines.append(f"- Текущий ФОТ ниже этого значения на {_rub(abs(excess))} ₽")

    lines.append(
        "Используй эти расчёты при ответе. Явно укажи, что ориентир — управленческий, "
        "а не нормативный показатель."
    )
    return "\n".join(lines)


# ─── Percentage follow-up detection ──────────────────────────────────────────

_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


def detect_percentage_followup(question: str, history: list[dict]) -> float | None:
    """Return percent as fraction (0–1) if this is a payroll percentage follow-up, else None."""
    if not history:
        return None
    match = _PCT_RE.search(question)
    if not match:
        return None
    last_topics = [detect_question_topic(h.get("question", "")) for h in history[-3:]]
    if "payroll" not in last_topics:
        return None
    pct_str = match.group(1).replace(",", ".")
    try:
        pct = float(pct_str) / 100
    except ValueError:
        return None
    return pct if 0.01 <= pct <= 0.99 else None


# ─── Revenue growth scenario ──────────────────────────────────────────────────

_REVENUE_SCENARIO_KW = frozenset(["выручк", "продаж"])
_GROWTH_KW = frozenset(["вырас", "увеличи", "добавит", "прибавит", "прирост", "рост"])
_MONTH_PERIOD_KW = frozenset(["в месяц", "ежемесячно", "каждый месяц", "в мес."])
_QUARTER_PERIOD_KW = frozenset([
    "за квартал", "в квартал", "за период", "за 3 месяц", "за три месяц",
])

_AMOUNT_RE = re.compile(
    r"(?:на\s+|\+\s*)?(\d+(?:[.,]\d+)?)\s*"
    r"(млн|млрд|миллион(?:ов)?|тыс(?:яч[иа]?)?)",
    re.IGNORECASE | re.UNICODE,
)


def _parse_amount(match: re.Match) -> float | None:
    num_str = match.group(1).replace(",", ".")
    try:
        num = float(num_str)
    except ValueError:
        return None
    suffix = (match.group(2) or "").lower()
    if suffix.startswith("млрд"):
        mult = 1_000_000_000
    elif suffix.startswith("млн") or suffix.startswith("мил"):
        mult = 1_000_000
    elif suffix.startswith("тыс"):
        mult = 1_000
    else:
        mult = 1
    result = num * mult
    return result if result > 0 else None


def detect_revenue_growth_scenario(question: str) -> dict | None:
    """
    Detect a revenue growth scenario question.
    Returns {amount, period_basis, period_note} or None.
    """
    q = question.lower()

    if not any(kw in q for kw in _REVENUE_SCENARIO_KW):
        return None
    if not any(kw in q for kw in _GROWTH_KW):
        return None

    match = _AMOUNT_RE.search(q)
    if not match:
        return None

    amount = _parse_amount(match)
    if amount is None:
        return None

    if any(kw in q for kw in _MONTH_PERIOD_KW):
        period_basis = "month"
        period_note  = None
    elif any(kw in q for kw in _QUARTER_PERIOD_KW):
        period_basis = "period"
        period_note  = None
    else:
        period_basis = "period"
        period_note  = (
            "Период прироста не указан явно; расчёт ниже — как прирост за весь отчётный период."
        )

    return {"amount": amount, "period_basis": period_basis, "period_note": period_note}


def calculate_revenue_growth_scenario(analysis_context: dict, scenario: dict) -> dict | None:
    """
    Compute proxy revenue growth scenario figures.
    Returns None if essential data (revenue) is missing.
    """
    kpi    = analysis_context.get("kpi") or {}
    sk     = kpi.get("summary_kpi") or {}
    pnl    = analysis_context.get("pnl") or {}
    period = analysis_context.get("period") or {}

    # Current revenue
    current_revenue: float | None = None
    rev_entry = sk.get("revenue")
    if rev_entry and rev_entry.get("value") is not None:
        try:
            current_revenue = float(rev_entry["value"])
        except (TypeError, ValueError):
            pass
    if current_revenue is None:
        raw = (pnl.get("totals") or {}).get("revenue")
        if raw is not None:
            try:
                current_revenue = float(raw)
            except (TypeError, ValueError):
                pass
    if not current_revenue or current_revenue <= 0:
        return None

    months_count = len(period.get("months") or []) or 1

    # Break-even revenue
    be_revenue: float | None = None
    be_entry = sk.get("break_even_revenue")
    if be_entry and be_entry.get("value") is not None:
        try:
            be_revenue = float(be_entry["value"])
        except (TypeError, ValueError):
            pass

    # Operating profit
    current_op_profit: float | None = None
    op_entry = sk.get("operating_profit")
    if op_entry and op_entry.get("value") is not None:
        try:
            current_op_profit = float(op_entry["value"])
        except (TypeError, ValueError):
            pass

    # Gross margin ratio
    gm_ratio: float | None = None
    gm_entry = sk.get("gross_margin")
    if gm_entry and gm_entry.get("value") is not None:
        try:
            gm_ratio = float(gm_entry["value"])
        except (TypeError, ValueError):
            pass

    amount       = scenario["amount"]
    period_basis = scenario.get("period_basis", "period")
    total_increase = amount * months_count if period_basis == "month" else amount
    new_revenue    = current_revenue + total_increase

    new_be_gap: float | None = None
    if be_revenue is not None:
        new_be_gap = new_revenue - be_revenue

    additional_gp:      float | None = None
    proxy_new_op_profit: float | None = None
    if gm_ratio is not None:
        additional_gp = total_increase * gm_ratio
        if current_op_profit is not None:
            proxy_new_op_profit = current_op_profit + additional_gp

    return {
        "amount":               amount,
        "period_basis":         period_basis,
        "months_count":         months_count,
        "total_increase":       total_increase,
        "current_revenue":      current_revenue,
        "new_revenue":          new_revenue,
        "break_even_revenue":   be_revenue,
        "new_break_even_gap":   new_be_gap,
        "current_op_profit":    current_op_profit,
        "gross_margin_ratio":   gm_ratio,
        "additional_gp_proxy":  additional_gp,
        "proxy_new_op_profit":  proxy_new_op_profit,
        "period_note":          scenario.get("period_note"),
    }


def _format_revenue_scenario_block(sc: dict) -> str:
    amount = sc["amount"]
    pb     = sc["period_basis"]
    mc     = sc["months_count"]
    ti     = sc["total_increase"]
    cr     = sc["current_revenue"]
    nr     = sc["new_revenue"]

    lines = [
        "\n=== ДОПОЛНИТЕЛЬНЫЙ РАСЧЁТ СЦЕНАРИЯ РОСТА ВЫРУЧКИ ===",
        "(Proxy-сценарий, рассчитан в Python специально для ответа на вопрос о сценарии)",
    ]
    if sc.get("period_note"):
        lines.append(f"ПРИМЕЧАНИЕ: {sc['period_note']}")

    if pb == "month":
        lines.append(f"- Прирост выручки в месяц: {_rub(amount)} ₽")
        lines.append(f"- Количество месяцев в отчёте: {mc}")
        lines.append(f"- Общий прирост за период: {_rub(amount)} × {mc} = {_rub(ti)} ₽")
    else:
        lines.append(f"- Прирост выручки за период: {_rub(ti)} ₽")

    lines.append(f"- Текущая выручка за период: {_rub(cr)} ₽")
    lines.append(f"- Новая выручка за период: {_rub(nr)} ₽")

    if sc.get("break_even_revenue") is not None:
        be  = sc["break_even_revenue"]
        gap = sc.get("new_break_even_gap")
        lines.append(f"- Текущая proxy-точка безубыточности: {_rub(be)} ₽")
        if gap is not None:
            if gap >= 0:
                lines.append(f"- Новый разрыв: +{_rub(gap)} ₽ (выручка ВЫШЕ точки безубыточности)")
            else:
                lines.append(f"- Новый разрыв: {_rub(gap)} ₽ (выручка НИЖЕ точки безубыточности)")

    if sc.get("gross_margin_ratio") is not None:
        gm  = sc["gross_margin_ratio"]
        agp = sc.get("additional_gp_proxy")
        lines.append(f"- Текущая валовая маржа: {_pct(gm)}")
        if agp is not None:
            lines.append(
                f"- Дополнительная валовая прибыль proxy: "
                f"{_rub(ti)} × {_pct(gm)} = {_rub(agp)} ₽"
            )

    if sc.get("proxy_new_op_profit") is not None:
        cop = sc.get("current_op_profit")
        agp = sc.get("additional_gp_proxy")
        pop = sc["proxy_new_op_profit"]
        lines.append(f"- Текущая операционная прибыль: {_signed(cop)} ₽")
        lines.append(
            f"- Новая операционная прибыль proxy: "
            f"{_signed(cop)} + {_rub(agp)} = {_signed(pop)} ₽"
        )

    lines.append(
        "ВАЖНО: это proxy-сценарий при сохранении текущей валовой маржи "
        "и без роста операционных расходов. "
        "В реальности вместе с выручкой могут вырасти себестоимость, подрядчики, ФОТ, налоги."
    )
    return "\n".join(lines)


# ─── History formatter ────────────────────────────────────────────────────────

def _format_history_block(history: list[dict]) -> str:
    if not history:
        return ""
    lines = [
        "\n=== КРАТКАЯ ИСТОРИЯ ДИАЛОГА ===",
        "(Используй только для понимания уточнений. "
        "Финансовые данные — из analysis_context выше, не из истории.)",
    ]
    for i, entry in enumerate(history, 1):
        q = (entry.get("question") or "").strip()
        a = (entry.get("answer") or "").strip()
        if q:
            lines.append(f"Вопрос {i}: {q}")
        if a:
            a_short = (a[:400] + "…") if len(a) > 400 else a
            lines.append(f"Ответ {i}: {a_short}")
    return "\n".join(lines)


# ─── Compact context builder ──────────────────────────────────────────────────

def _build_compact_context(ctx: dict) -> str:
    """Format analysis_context as a compact text block for the LLM."""
    period       = ctx.get("period") or {}
    period_label = period.get("period_label") or "за период"
    months_list  = period.get("months") or []
    month_names  = period.get("month_names") or []
    name_map     = dict(zip(months_list, month_names))

    bs          = ctx.get("business_summary") or {}
    findings    = ctx.get("key_findings") or []
    constraints = ctx.get("report_constraints") or []
    pnl         = ctx.get("pnl") or {}
    cf          = ctx.get("cashflow") or {}
    kpi         = ctx.get("kpi") or {}
    quality     = ctx.get("data_quality") or {}

    lines: list[str] = []
    lines.append(f"Финансовые данные {period_label}.\n")

    if findings:
        lines.append("=== КЛЮЧЕВЫЕ ФАКТЫ ===")
        for i, f in enumerate(findings, 1):
            lines.append(f"{i}. {f}")
        lines.append(f"Главный итог: {bs.get('main_result', '—')}\n")

    sk = kpi.get("summary_kpi") or {}
    if sk:
        lines.append("=== KPI СОБСТВЕННИКА ===")
        for k in [
            "revenue", "gross_profit", "gross_margin",
            "operating_expenses", "operating_expense_ratio",
            "payroll_ratio", "rent_ratio", "taxes_ratio",
            "operating_profit", "operating_margin",
            "break_even_revenue", "break_even_gap", "break_even_gap_ratio",
            "net_profit_or_proxy",
            "operating_cashflow", "net_cashflow",
            "profit_to_operating_cashflow_gap", "profit_to_net_cashflow_gap",
            "profit_to_cash_conversion", "net_cashflow_ratio", "cash_reserve_months",
        ]:
            entry = sk.get(k)
            if entry and entry.get("formatted") not in (None, "—"):
                lines.append(f"  {entry['label']}: {entry['formatted']}")
                interp = entry.get("interpretation", "")
                if interp:
                    lines.append(f"    ({interp})")
        lines.append("")

    mk_data = kpi.get("monthly_kpi") or {}
    if mk_data:
        lines.append("=== ПОМЕСЯЧНЫЕ KPI ===")
        for mk in sorted(mk_data.keys()):
            m    = mk_data[mk]
            name = name_map.get(mk, mk).capitalize()
            rev  = (m.get("revenue") or {}).get("formatted", "—")
            gm   = (m.get("gross_margin") or {}).get("formatted", "—")
            op   = (m.get("operating_profit") or {}).get("formatted", "—")
            om   = (m.get("operating_margin") or {}).get("formatted", "—")
            opcf = (m.get("operating_cashflow") or {}).get("formatted", "—")
            ncf  = (m.get("net_cashflow") or {}).get("formatted", "—")
            bev  = (m.get("break_even_revenue") or {}).get("formatted", "—")
            beg  = (m.get("break_even_gap") or {}).get("formatted", "—")
            begr = (m.get("break_even_gap_ratio") or {}).get("formatted", "—")
            lines.append(
                f"  {name}: выручка {rev} | маржа {gm} | прибыль {op}"
                f" | рентаб. {om} | опер.ДДС {opcf} | чист.ДДС {ncf}"
                f" | точка безуб. {bev} | разрыв {beg} | покрытие {begr}"
            )
        lines.append("")

    pk_data = kpi.get("project_kpi") or {}
    if pk_data:
        lines.append("=== KPI ПО ПРОЕКТАМ ===")
        for proj, pd in pk_data.items():
            rev     = (pd.get("revenue") or {}).get("formatted", "—")
            gm      = (pd.get("gross_margin") or {}).get("formatted", "—")
            op      = (pd.get("operating_profit") or {}).get("formatted", "—")
            om      = (pd.get("operating_margin") or {}).get("formatted", "—")
            bev     = (pd.get("break_even_revenue") or {}).get("formatted", "—")
            beg     = (pd.get("break_even_gap") or {}).get("formatted", "—")
            begr    = (pd.get("break_even_gap_ratio") or {}).get("formatted", "—")
            contrib = (pd.get("contribution_to_total_profit") or {}).get("interpretation", "")
            lines.append(
                f"  {proj}: выручка {rev} | маржа {gm} | прибыль {op}"
                f" | рентаб. {om} | точка безуб. {bev} | разрыв {beg} | покрытие {begr}"
            )
            if contrib:
                lines.append(f"    Вклад: {contrib}")
        lines.append("")

    pnl_totals = pnl.get("totals") or {}
    if pnl_totals:
        lines.append("=== БДР — ИТОГО (начисления, не деньги) ===")
        for k, v in pnl_totals.items():
            if v is not None:
                lines.append(f"  {_PNL_LABELS_RU.get(k, k)}: {_signed(v)} ₽")
        lines.append("")

    pnl_monthly = pnl.get("monthly") or {}
    if pnl_monthly:
        lines.append("=== БДР — ПОМЕСЯЧНО ===")
        for mk in sorted(pnl_monthly.keys()):
            m    = pnl_monthly[mk]
            name = name_map.get(mk, mk).capitalize()
            parts = []
            for k in ["revenue", "gross_profit", "operating_expenses", "operating_profit", "payroll"]:
                if m.get(k) is not None:
                    parts.append(f"{_PNL_LABELS_RU.get(k, k)} {_signed(m[k])} ₽")
            lines.append(f"  {name}: " + " | ".join(parts))
        lines.append("")

    pnl_projects = pnl.get("projects") or {}
    if pnl_projects:
        lines.append("=== БДР — ПО ПРОЕКТАМ ===")
        for proj, pd in pnl_projects.items():
            lines.append(
                f"  {proj}: выручка {_rub(pd.get('revenue'))} ₽"
                f" | вал. прибыль {_signed(pd.get('gross_profit'))} ₽"
                f" | расходы {_signed(pd.get('operating_expenses'))} ₽"
                f" | прибыль {_signed(pd.get('operating_profit'))} ₽"
            )
        lines.append("")

    cf_totals = cf.get("totals") or {}
    if cf_totals:
        lines.append("=== ДДС — ИТОГО (денежные потоки) ===")
        for k, v in cf_totals.items():
            if v is not None:
                lines.append(f"  {_CF_LABELS_RU.get(k, k)}: {_signed(v)} ₽")
        lines.append("")

    cf_monthly = cf.get("monthly") or {}
    if cf_monthly:
        lines.append("=== ДДС — ПОМЕСЯЧНО ===")
        for mk in sorted(cf_monthly.keys()):
            m    = cf_monthly[mk]
            name = name_map.get(mk, mk).capitalize()
            parts = []
            for k in ["operating_cashflow", "financial_cashflow", "net_cashflow", "cash_end"]:
                if m.get(k) is not None:
                    parts.append(f"{_CF_LABELS_RU.get(k, k)} {_signed(m[k])} ₽")
            lines.append(f"  {name}: " + " | ".join(parts))
        lines.append("")

    cf_details = cf.get("details") or {}
    if cf_details:
        lines.append("=== ДЕТАЛИЗАЦИЯ ДДС (денежные выплаты, не БДР) ===")
        for k, v in sorted(cf_details.items(), key=lambda x: float(x[1] or 0)):
            if v is not None:
                lines.append(f"  {_CF_DETAIL_LABELS_RU.get(k, k)}: {_signed(v)} ₽")
        lines.append("")

    # Constraints (order-preserving dedup)
    _seen: set[str] = set()
    deduped: list[str] = []
    for c in constraints:
        if c not in _seen:
            _seen.add(c)
            deduped.append(c)
    if deduped:
        lines.append("=== ОГРАНИЧЕНИЯ ===")
        for c in deduped:
            lines.append(f"  • {c}")
        lines.append("")

    q_score  = quality.get("score")
    q_status = quality.get("status")
    if q_score is not None:
        lines.append(f"Качество данных: {q_status}, оценка {q_score}/100\n")

    return "\n".join(lines)


# ─── Prompt builder ───────────────────────────────────────────────────────────

def build_question_prompt(
    question: str,
    analysis_context: dict,
    benchmark: dict | None = None,
    history: list[dict] | None = None,
    revenue_scenario: dict | None = None,
) -> list[dict]:
    """Return messages list for OpenAI chat completion."""
    period_label = (
        (analysis_context.get("period") or {}).get("period_label") or "за период"
    )
    compact = _build_compact_context(analysis_context)
    user_parts = [
        f"Вопрос пользователя: {question}\n",
        f"Отвечай только на основе данных ниже. "
        f"Используй «{period_label}» вместо «за период».\n",
        compact,
    ]

    if history:
        user_parts.append(_format_history_block(history))

    if benchmark:
        user_parts.append(_format_payroll_benchmark_block(benchmark))

    if revenue_scenario:
        user_parts.append(_format_revenue_scenario_block(revenue_scenario))

    # Topic switch detection
    if history:
        current_topic  = detect_question_topic(question)
        previous_topic = detect_last_topic(history)
        is_topic_switch = (
            current_topic != "unknown"
            and previous_topic != "unknown"
            and current_topic != previous_topic
        )
        if is_topic_switch:
            user_parts.append(
                f"\n[СМЕНА ТЕМЫ] Предыдущая тема: {previous_topic}. Новая тема: {current_topic}. "
                "Это новый самостоятельный вопрос. Не продолжай предыдущую тему. "
                "Используй историю только для понимания формулировки нового вопроса."
            )

    return [
        {"role": "system", "content": _QA_SYSTEM_PROMPT},
        {"role": "user",   "content": "\n".join(user_parts)},
    ]


# ─── Context loader ───────────────────────────────────────────────────────────

def load_analysis_context(analysis_id: str) -> dict | None:
    if not _is_safe_analysis_id(analysis_id):
        return None
    path = settings.output_dir / f"{analysis_id}_analysis_context.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ─── Routed prompt (for deterministic intents) ────────────────────────────────

_ROUTED_SYSTEM_PROMPT = """Ты — финансовый консультант для собственника бизнеса.
Тебе переданы готовые расчёты в разделе ANSWER_PAYLOAD.

Правила:
1. Используй ТОЛЬКО цифры из ANSWER_PAYLOAD. Не пересчитывай, не изменяй, не округляй их.
2. Если ANSWER_PAYLOAD.status == "no_data" — ответь: «В загруженных данных нет информации для ответа на этот вопрос.»
3. Если ANSWER_PAYLOAD.status == "needs_clarification" — вежливо попроси уточнить, следуй answer_guidance.
4. Proxy-результаты называй «proxy-прибыль от основной деятельности», НИКОГДА — «чистая прибыль».
5. Если в limitations упоминается proxy — обязательно укажи это в ответе.
6. Следуй инструкциям из поля answer_guidance по структуре ответа.
7. НЕ предлагай список вопросов после ответа.
8. НЕ выводи поля ANSWER_PAYLOAD как есть — объясняй их содержание на русском языке.
9. Все цифры из ANSWER_PAYLOAD сохраняй точно — не округляй и не пересчитывай.

Формат: Markdown. Кратко и по существу.
— Для развёрнутых ответов: ## Ответ / ## Расшифровка / ## Ограничения.
— Для коротких уточняющих: без заголовков, 2–4 предложения или список."""


def _build_routed_prompt(
    question: str,
    payload: dict,
    history: list[dict] | None = None,
) -> list[dict]:
    """Build messages for a deterministic-handler intent."""
    import json as _json

    period_note = ""
    # Include last 2 history questions for follow-up context only
    history_snippet = ""
    if history:
        recent = history[-2:]
        parts = []
        for entry in recent:
            q = (entry.get("question") or "").strip()
            a = (entry.get("answer") or "").strip()
            if q:
                parts.append(f"Вопрос: {q}")
            if a:
                parts.append(f"Ответ (кратко): {(a[:300] + '…') if len(a) > 300 else a}")
        if parts:
            history_snippet = (
                "\n=== КОНТЕКСТ ДИАЛОГА (только для понимания уточнений) ===\n"
                + "\n".join(parts)
                + "\n"
            )

    payload_json = _json.dumps(payload, ensure_ascii=False, indent=2)
    user_content = (
        f"Вопрос пользователя: {question}\n"
        f"{history_snippet}\n"
        f"ANSWER_PAYLOAD:\n```json\n{payload_json}\n```\n\n"
        f"Следуй answer_guidance из ANSWER_PAYLOAD для формулировки ответа."
    )
    return [
        {"role": "system", "content": _ROUTED_SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]


# ─── Main function ────────────────────────────────────────────────────────────

def answer_question(
    question: str,
    analysis_id: str,
    history: list[dict] | None = None,
) -> dict:
    """
    Answer a user question using the 3-stage pipeline:
      1. build_query_plan   — classify intent + operations
      2. execute_query_plan — Python handlers produce answer_payload
      3. compose_answer     — LLM explains pre-computed payload

    Returns:
        {status, answer_markdown, analysis_id, model, error,
         intent, scenario, query_plan (debug), answer_payload (debug)}
    """
    if not _is_safe_analysis_id(analysis_id):
        return {
            "status": "error", "answer_markdown": "",
            "analysis_id": analysis_id, "model": "",
            "error": "Некорректный идентификатор анализа.",
            "intent": None, "scenario": None,
            "query_plan": None, "answer_payload": None,
        }

    ctx = load_analysis_context(analysis_id)
    if ctx is None:
        return {
            "status": "error", "answer_markdown": "",
            "analysis_id": analysis_id, "model": "",
            "error": "Контекст анализа не найден. Сначала загрузите и обработайте отчёт.",
            "intent": None, "scenario": None,
            "query_plan": None, "answer_payload": None,
        }

    if not question or not question.strip():
        return {
            "status": "error", "answer_markdown": "",
            "analysis_id": analysis_id, "model": "",
            "error": "Вопрос не может быть пустым.",
            "intent": None, "scenario": None,
            "query_plan": None, "answer_payload": None,
        }

    question = question.strip()
    history  = [h for h in (history or []) if isinstance(h, dict) and h.get("question")]

    from app.services.analytical_query_planner import build_query_plan
    from app.services.analytical_executor      import execute_query_plan
    from app.services.answer_composer          import compose_answer

    # Stage 1: plan
    query_plan = build_query_plan(question, ctx, history)

    # Stage 2: execute
    answer_payload = execute_query_plan(query_plan, ctx, history)

    # Stage 3: compose
    compact_context: str | None = None
    if query_plan.get("intent") == "general_management_explanation":
        compact_context = _build_compact_context(ctx)

    composed = compose_answer(
        question, answer_payload, ctx,
        compact_context=compact_context,
        history=history or None,
    )

    intent   = query_plan.get("intent")
    scenario = (answer_payload.get("metadata") or {}).get("scenario")

    return {
        "status":          composed["status"],
        "answer_markdown": composed["answer_markdown"],
        "analysis_id":     analysis_id,
        "model":           composed.get("model", ""),
        "error":           composed.get("error"),
        "intent":          intent,
        "scenario":        scenario,
        "query_plan":      query_plan   if settings.debug_mode else None,
        "answer_payload":  answer_payload if settings.debug_mode else None,
    }
