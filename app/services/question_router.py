"""
Question router: classify user intent and compute deterministic answer payloads.

The LLM receives only pre-computed facts — it CANNOT recalculate or modify figures.
All structured financial questions are handled by Python handlers here.
"""
from __future__ import annotations

import re
from enum import Enum


# ─── Intent enum ──────────────────────────────────────────────────────────────

class QuestionIntent(str, Enum):
    PAYROLL_BENCHMARK        = "payroll_benchmark"
    REVENUE_GROWTH_SCENARIO  = "revenue_growth_scenario"
    COST_PERCENTAGE_FOLLOWUP = "cost_percentage_followup"
    ANNUAL_FORECAST          = "annual_forecast"
    WORST_MONTH              = "worst_month"
    PROJECT_PROFITABILITY    = "project_profitability"
    BREAK_EVEN_PERIOD        = "break_even_period"
    BREAK_EVEN_PROJECT       = "break_even_project"
    EXPENSE_RANKING          = "expense_ranking"
    PROFIT_VS_CASH           = "profit_vs_cash"
    MISSING_DATA             = "missing_data"
    GENERAL_EXPLANATION      = "general_explanation"


# ─── Keyword sets ─────────────────────────────────────────────────────────────

_PAYROLL_KW = frozenset(["фот", "фонд оплаты", "зарплат", "заработн"])
_NORM_KW    = frozenset([
    "приемлем", "нормальн", "должен быть", "должно быть",
    "сколько должен", "сколько нужно", "норматив",
    "допустим", "ориентир", "оптимальн", "рекомендован",
])
_REVENUE_KW = frozenset(["выручк", "продаж"])
_GROWTH_KW  = frozenset(["вырас", "увеличи", "добавит", "прибавит", "прирост", "рост"])
_COST_KW    = frozenset([
    "агентск", "комисс", "расход", "затрат",
    "стоит", "обойдет", "себестоимост", "подрядчик",
    "добавь расход", "вычти", "дополнительн расход",
])
_FORECAST_KW = frozenset([
    "за год", "на год", "годовой", "ежегодн",
    "12 месяц", "12 мес", "пропорция сохранится", "экстраполир",
])
_WORST_KW    = frozenset(["худш", "слабый", "слабее", "наихудш"])
_BEST_KW     = frozenset(["лучший", "лучш", "наилучш", "сильный"])
_MONTH_KW    = frozenset([
    "месяц", "март", "феврал", "январ", "апрел",
    "май", "июн", "июл", "август", "сентябр",
    "октябр", "ноябр", "декабр",
])
_PROJECT_KW  = frozenset([
    "проект", "направлен", "авторадио", "европа", "ретро",
    "сайт", "наружн", "заб тв", "забmedia",
])
_PROF_KW     = frozenset(["убыточн", "прибыльн", "прибыл"])
_BREAKEVEN_KW = frozenset([
    "безубыточ", "до нуля", "нулев", "сколько не хватило",
    "не хватило", "покрытие",
])
_EXPENSE_KW  = frozenset([
    "расход", "затрат", "крупнейш", "больше всего",
    "куда уходит", "куда идут",
    "статьи", "проверить", "рейтинг расход",
])
_EXPENSE_QUAL_KW = frozenset([
    "какие", "топ", "больше всего", "крупнейш",
    "куда", "статьи", "проверить", "рейтинг",
])
_PCASH_KW    = frozenset([
    "прибыль и деньги", "деньги и прибыль",
    "бдр и ддс", "ддс и бдр",
    "почему разница", "разрыв между",
    "прибыль не равна", "отличается",
])
_KNOWN_PROJECTS = [
    "европа+", "авторадио", "ретро fm", "ретро фм", "ретро",
    "забmedia", "заб тв", "наружная реклама", "наружн", "сайт",
]
_MONTH_PERIOD_KW   = frozenset(["в месяц", "ежемесячно", "каждый месяц", "в мес"])
_QUARTER_PERIOD_KW = frozenset([
    "за квартал", "в квартал", "за период",
    "за 3 месяц", "за три месяц",
])

_AMOUNT_RE = re.compile(
    r"(?:на\s+|\+\s*)?(\d+(?:[.,]\d+)?)\s*"
    r"(млн|млрд|миллион(?:ов)?|тыс(?:яч[иа]?)?)",
    re.IGNORECASE | re.UNICODE,
)
_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


def _parse_amount(m: re.Match) -> float | None:
    try:
        num = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    s    = (m.group(2) or "").lower()
    mult = (
        1_000_000_000 if s.startswith("млрд") else
        1_000_000     if s.startswith("млн") or s.startswith("мил") else
        1_000
    )
    return (num * mult) if num > 0 else None


def _has_recent_revenue_scenario(history: list[dict]) -> dict | None:
    """Return scenario dict from recent history metadata, or None."""
    for entry in reversed((history or [])[-5:]):
        meta = entry.get("metadata") or {}
        if meta.get("intent") == QuestionIntent.REVENUE_GROWTH_SCENARIO:
            sc = meta.get("scenario") or {}
            if sc.get("total_revenue_increase"):
                return sc
    return None


# ─── Intent detection ─────────────────────────────────────────────────────────

def detect_intent(question: str, history: list[dict] | None = None) -> QuestionIntent:
    """Classify question into a QuestionIntent. Most specific checks first."""
    q = question.lower()
    h = history or []

    # COST_PERCENTAGE_FOLLOWUP — needs %, cost keyword, prior revenue scenario
    if _PCT_RE.search(q) and any(kw in q for kw in _COST_KW):
        if _has_recent_revenue_scenario(h):
            return QuestionIntent.COST_PERCENTAGE_FOLLOWUP

    # REVENUE_GROWTH_SCENARIO — revenue + growth + parseable amount
    if any(kw in q for kw in _REVENUE_KW) and any(kw in q for kw in _GROWTH_KW):
        if _AMOUNT_RE.search(q):
            return QuestionIntent.REVENUE_GROWTH_SCENARIO

    # PAYROLL_BENCHMARK — payroll + normative keyword
    if any(kw in q for kw in _PAYROLL_KW) and any(kw in q for kw in _NORM_KW):
        return QuestionIntent.PAYROLL_BENCHMARK

    # ANNUAL_FORECAST
    if any(kw in q for kw in _FORECAST_KW):
        return QuestionIntent.ANNUAL_FORECAST

    # BREAK_EVEN — check before month/project to catch "сколько не хватило"
    if any(kw in q for kw in _BREAKEVEN_KW):
        if any(p in q for p in _KNOWN_PROJECTS) or any(kw in q for kw in _PROJECT_KW):
            return QuestionIntent.BREAK_EVEN_PROJECT
        return QuestionIntent.BREAK_EVEN_PERIOD

    # WORST/BEST MONTH
    if any(kw in q for kw in _WORST_KW | _BEST_KW) and any(kw in q for kw in _MONTH_KW):
        return QuestionIntent.WORST_MONTH
    if "какой месяц" in q:
        return QuestionIntent.WORST_MONTH

    # PROJECT_PROFITABILITY
    if any(kw in q for kw in _PROJECT_KW) and any(kw in q for kw in _PROF_KW):
        return QuestionIntent.PROJECT_PROFITABILITY
    if "какие проекты" in q:
        return QuestionIntent.PROJECT_PROFITABILITY

    # EXPENSE_RANKING — expense keyword + qualifying word
    if any(kw in q for kw in _EXPENSE_KW) and any(kw in q for kw in _EXPENSE_QUAL_KW):
        return QuestionIntent.EXPENSE_RANKING

    # PROFIT_VS_CASH
    if any(kw in q for kw in _PCASH_KW):
        return QuestionIntent.PROFIT_VS_CASH

    return QuestionIntent.GENERAL_EXPLANATION


# ─── Format helpers ───────────────────────────────────────────────────────────

def _rub(v) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except Exception:
        return "—"
    return f"{'-' if v < 0 else ''}{abs(v):,.0f}".replace(",", " ")


def _signed(v) -> str:
    if v is None:
        return "—"
    try:
        fv = float(v)
    except Exception:
        return "—"
    return f"{'+' if fv > 0 else ''}{_rub(v)}"


def _pct(r) -> str:
    if r is None:
        return "—"
    return f"{r * 100:.1f}%".replace(".", ",")


# ─── Context accessors ────────────────────────────────────────────────────────

def _kv(d: dict, key: str) -> float | None:
    """Float value from a KPI entry dict."""
    entry = d.get(key)
    if entry and entry.get("value") is not None:
        try:
            return float(entry["value"])
        except Exception:
            pass
    return None


def _kf(d: dict, key: str) -> str:
    """Formatted string from a KPI entry dict."""
    entry = d.get(key)
    return (entry or {}).get("formatted") or "—"


def _ki(d: dict, key: str) -> str:
    """Interpretation string from a KPI entry dict."""
    return ((d.get(key) or {}).get("interpretation") or "")


def _sk(ctx: dict) -> dict:
    return (ctx.get("kpi") or {}).get("summary_kpi") or {}


def _mk(ctx: dict) -> dict:
    return (ctx.get("kpi") or {}).get("monthly_kpi") or {}


def _pk(ctx: dict) -> dict:
    return (ctx.get("kpi") or {}).get("project_kpi") or {}


def _period(ctx: dict) -> dict:
    return ctx.get("period") or {}


def _month_names(ctx: dict) -> dict[str, str]:
    p = _period(ctx)
    return dict(zip(p.get("months") or [], p.get("month_names") or []))


# ─── Payload factory ──────────────────────────────────────────────────────────

def _payload(
    intent: QuestionIntent,
    facts: dict,
    calculations: dict,
    limitations: list[str],
    answer_guidance: str,
    scenario: dict | None = None,
    status: str = "success",
) -> dict:
    return {
        "intent":          intent.value,
        "status":          status,
        "facts":           facts,
        "calculations":    calculations,
        "limitations":     limitations,
        "answer_guidance": answer_guidance,
        "scenario":        scenario,
    }


# ─── Handler: PAYROLL_BENCHMARK ───────────────────────────────────────────────

def _handle_payroll_benchmark(question: str, ctx: dict, history: list[dict]) -> dict:
    sk         = _sk(ctx)
    pnl_totals = (ctx.get("pnl") or {}).get("totals") or {}

    revenue: float | None = _kv(sk, "revenue")
    if revenue is None:
        try:
            revenue = float(pnl_totals.get("revenue") or 0) or None
        except Exception:
            pass
    if not revenue or revenue <= 0:
        return _payload(
            QuestionIntent.PAYROLL_BENCHMARK, {}, {}, [],
            "Данные о выручке не найдены. Сообщи, что расчёт недоступен.",
            status="no_data",
        )

    payroll: float | None = _kv(sk, "payroll")
    if payroll is None:
        try:
            payroll = abs(float(pnl_totals.get("payroll") or 0)) or None
        except Exception:
            pass
    if not payroll:
        return _payload(
            QuestionIntent.PAYROLL_BENCHMARK, {}, {}, [],
            "Данные о ФОТ не найдены. Сообщи, что расчёт недоступен.",
            status="no_data",
        )
    payroll = abs(payroll)

    ratio = _kv(sk, "payroll_ratio")
    if ratio is None:
        ratio = payroll / revenue

    low_r, high_r = 0.35, 0.40
    low_a  = revenue * low_r
    high_a = revenue * high_r
    excess = payroll - high_a

    pct_m  = _PCT_RE.search(question.lower())
    custom: dict | None = None
    if pct_m:
        try:
            cp = float(pct_m.group(1).replace(",", ".")) / 100
            if 0.01 <= cp <= 0.99:
                ca = revenue * cp
                custom = {
                    "percent":       cp,
                    "percent_label": f"{cp * 100:.0f}%",
                    "amount":        ca,
                    "amount_rub":    _rub(ca),
                    "excess":        payroll - ca,
                    "excess_rub":    _signed(payroll - ca),
                }
        except Exception:
            pass

    return _payload(
        QuestionIntent.PAYROLL_BENCHMARK,
        facts={
            "revenue":                  revenue,
            "revenue_rub":              _rub(revenue),
            "current_payroll":          payroll,
            "current_payroll_rub":      _rub(payroll),
            "current_payroll_ratio":    ratio,
            "current_payroll_ratio_pct": _pct(ratio),
        },
        calculations={
            "benchmark_range_pct":      f"{low_r * 100:.0f}–{high_r * 100:.0f}%",
            "benchmark_low_amount":     low_a,
            "benchmark_low_amount_rub": _rub(low_a),
            "benchmark_high_amount":    high_a,
            "benchmark_high_amount_rub":_rub(high_a),
            "excess_over_high":         excess,
            "excess_over_high_rub":     _signed(excess),
            "custom":                   custom,
        },
        limitations=[
            "Ориентир 35–40% — управленческий, не отраслевой норматив и не законодательный стандарт.",
            "Для медиабизнеса допустимый уровень ФОТ может отличаться от общего ориентира.",
        ],
        answer_guidance=(
            "1. Покажи фактический ФОТ и его долю в выручке (facts.current_payroll, facts.current_payroll_ratio_pct).\n"
            "2. Сравни с ориентиром 35–40% (calculations.benchmark_range_pct, _low_amount, _high_amount).\n"
            "3. Если превышение (calculations.excess_over_high > 0) — укажи его с суммой.\n"
            "4. Если пользователь спросил про конкретный % — добавь блок с calculations.custom.\n"
            "5. ЯВНО отметь: «Это управленческий ориентир, не норматив»."
        ),
    )


# ─── Handler: REVENUE_GROWTH_SCENARIO ────────────────────────────────────────

def _handle_revenue_growth_scenario(question: str, ctx: dict, history: list[dict]) -> dict:
    q = question.lower()
    m = _AMOUNT_RE.search(q)
    if not m:
        return _payload(
            QuestionIntent.REVENUE_GROWTH_SCENARIO, {}, {}, [],
            "Сумма прироста не распознана. Уточни: на сколько рублей вырастет выручка?",
            status="needs_clarification",
        )
    amount = _parse_amount(m)
    if not amount:
        return _payload(
            QuestionIntent.REVENUE_GROWTH_SCENARIO, {}, {}, [],
            "Не удалось распознать сумму. Напиши явно, например: «на 2 млн».",
            status="needs_clarification",
        )

    if any(kw in q for kw in _MONTH_PERIOD_KW):
        period_basis = "month"
        period_note  = None
    elif any(kw in q for kw in _QUARTER_PERIOD_KW):
        period_basis = "period"
        period_note  = None
    else:
        period_basis = "period"
        period_note  = (
            "Период прироста не указан явно. "
            "Расчёт — как прирост за весь отчётный период."
        )

    sk           = _sk(ctx)
    p            = _period(ctx)
    months_count = len(p.get("months") or []) or 1

    revenue: float | None = _kv(sk, "revenue")
    if revenue is None:
        try:
            revenue = float((ctx.get("pnl") or {}).get("totals", {}).get("revenue") or 0) or None
        except Exception:
            pass
    if not revenue:
        return _payload(
            QuestionIntent.REVENUE_GROWTH_SCENARIO, {}, {}, [],
            "Данные о выручке не найдены.",
            status="no_data",
        )

    total_increase = amount * months_count if period_basis == "month" else amount
    new_revenue    = revenue + total_increase

    be_revenue    = _kv(sk, "break_even_revenue")
    new_be_gap    = (new_revenue - be_revenue) if be_revenue is not None else None
    gm_ratio      = _kv(sk, "gross_margin")
    cur_op_profit = _kv(sk, "operating_profit")

    additional_gp: float | None = None
    proxy_new_op:  float | None = None
    if gm_ratio is not None:
        additional_gp = total_increase * gm_ratio
        if cur_op_profit is not None:
            proxy_new_op = cur_op_profit + additional_gp

    sc = {
        "amount":                        amount,
        "period_basis":                  period_basis,
        "months_count":                  months_count,
        "total_revenue_increase":        total_increase,
        "current_revenue":               revenue,
        "new_revenue":                   new_revenue,
        "break_even_revenue":            be_revenue,
        "new_break_even_gap":            new_be_gap,
        "current_operating_profit":      cur_op_profit,
        "gross_margin_ratio":            gm_ratio,
        "additional_gross_profit_proxy": additional_gp,
        "proxy_new_operating_profit":    proxy_new_op,
    }

    lims = [
        "Proxy-сценарий при сохранении текущей валовой маржи и без роста операционных расходов.",
        "В реальности вместе с выручкой могут вырасти себестоимость, ФОТ, подрядчики, налоги.",
    ]
    if period_note:
        lims.append(period_note)

    period_str = f"{_rub(amount)} ₽ в месяц × {months_count} = {_rub(total_increase)} ₽" \
        if period_basis == "month" else f"{_rub(total_increase)} ₽ за период"

    guidance = (
        f"Прирост: {period_str}.\n"
        f"Текущая выручка: {_rub(revenue)} ₽ → новая: {_rub(new_revenue)} ₽.\n"
    )
    if new_be_gap is not None:
        sign = "ВЫШЕ" if new_be_gap >= 0 else "НИЖЕ"
        guidance += f"Разрыв к точке безубыточности: {_signed(new_be_gap)} ₽ ({sign}).\n"
    if gm_ratio is not None and additional_gp is not None:
        guidance += f"Доп. валовая прибыль proxy ({_pct(gm_ratio)} маржа): {_rub(additional_gp)} ₽.\n"
    if proxy_new_op is not None:
        guidance += f"Новая операционная прибыль proxy: {_signed(proxy_new_op)} ₽.\n"
    guidance += (
        "ОБЯЗАТЕЛЬНО: называй результат «proxy-сценарий».\n"
        "ПРЕДУПРЕДИ: вместе с выручкой могут вырасти расходы."
    )

    return _payload(
        QuestionIntent.REVENUE_GROWTH_SCENARIO,
        facts={
            "amount":                    amount,
            "amount_rub":                _rub(amount),
            "period_basis":              period_basis,
            "months_count":              months_count,
            "current_revenue":           revenue,
            "current_revenue_rub":       _rub(revenue),
            "total_revenue_increase":    total_increase,
            "total_revenue_increase_rub":_rub(total_increase),
        },
        calculations={
            "new_revenue":                   new_revenue,
            "new_revenue_rub":               _rub(new_revenue),
            "break_even_revenue":            be_revenue,
            "break_even_revenue_rub":        _rub(be_revenue),
            "new_break_even_gap":            new_be_gap,
            "new_break_even_gap_rub":        _signed(new_be_gap),
            "gross_margin_ratio":            gm_ratio,
            "gross_margin_pct":              _pct(gm_ratio),
            "additional_gp_proxy":           additional_gp,
            "additional_gp_proxy_rub":       _rub(additional_gp),
            "current_operating_profit":      cur_op_profit,
            "current_operating_profit_rub":  _signed(cur_op_profit),
            "proxy_new_operating_profit":    proxy_new_op,
            "proxy_new_operating_profit_rub":_signed(proxy_new_op),
        },
        limitations=lims,
        answer_guidance=guidance,
        scenario=sc,
    )


# ─── Handler: COST_PERCENTAGE_FOLLOWUP ───────────────────────────────────────

def _handle_cost_percentage_followup(question: str, ctx: dict, history: list[dict]) -> dict:
    sc = _has_recent_revenue_scenario(history)
    if not sc:
        return _payload(
            QuestionIntent.COST_PERCENTAGE_FOLLOWUP, {}, {}, [],
            "Предыдущий сценарий роста выручки не найден в истории диалога. "
            "Попроси уточнить: от какой суммы нужно считать процент расходов?",
            status="needs_clarification",
        )

    pct_m = _PCT_RE.search(question)
    if not pct_m:
        return _payload(
            QuestionIntent.COST_PERCENTAGE_FOLLOWUP, {}, {}, [],
            "Процент не распознан. Уточни: какой именно процент расходов?",
            status="needs_clarification",
        )
    try:
        pct = float(pct_m.group(1).replace(",", ".")) / 100
    except Exception:
        pct = None
    if not pct or not (0.005 <= pct <= 0.99):
        return _payload(
            QuestionIntent.COST_PERCENTAGE_FOLLOWUP, {}, {}, [],
            "Процент не распознан корректно. Уточни значение.",
            status="needs_clarification",
        )

    base       = sc["total_revenue_increase"]
    add_gp     = sc.get("additional_gross_profit_proxy")
    cur_op     = sc.get("current_operating_profit")
    add_cost   = base * pct
    net_incr   = (add_gp - add_cost) if add_gp is not None else None
    proxy_new_op = (cur_op + net_incr) if cur_op is not None and net_incr is not None else None

    guidance = (
        f"База расчёта — дополнительная выручка {_rub(base)} ₽ из предыдущего сценария, "
        "НЕ текущая выручка.\n"
        f"Расходы {pct * 100:.0f}% от {_rub(base)} ₽ = {_rub(add_cost)} ₽ (calculations.additional_cost).\n"
    )
    if add_gp is not None:
        guidance += f"Доп. валовая прибыль proxy: {_rub(add_gp)} ₽.\n"
        guidance += f"Чистый прирост после расходов: {_signed(net_incr)} ₽ (calculations.net_incremental_effect).\n"
    if proxy_new_op is not None:
        guidance += f"Новая операционная прибыль proxy: {_signed(proxy_new_op)} ₽.\n"
    guidance += "Пометь: proxy-сценарий при прочих равных."

    return _payload(
        QuestionIntent.COST_PERCENTAGE_FOLLOWUP,
        facts={
            "base_description": "дополнительная выручка из предыдущего proxy-сценария",
            "base_amount":       base,
            "base_amount_rub":   _rub(base),
            "cost_percent":      pct,
            "cost_percent_label":f"{pct * 100:.0f}%",
        },
        calculations={
            "additional_cost":             add_cost,
            "additional_cost_rub":         _rub(add_cost),
            "additional_gp_proxy":         add_gp,
            "additional_gp_proxy_rub":     _rub(add_gp),
            "net_incremental_effect":      net_incr,
            "net_incremental_effect_rub":  _signed(net_incr),
            "current_operating_profit":    cur_op,
            "current_operating_profit_rub":_signed(cur_op),
            "proxy_new_operating_profit":    proxy_new_op,
            "proxy_new_operating_profit_rub":_signed(proxy_new_op),
        },
        limitations=[
            "База — дополнительная выручка из proxy-сценария, а не текущая выручка.",
            "Proxy-сценарий при прочих равных.",
        ],
        answer_guidance=guidance,
    )


# ─── Handler: ANNUAL_FORECAST ─────────────────────────────────────────────────

def _handle_annual_forecast(question: str, ctx: dict, history: list[dict]) -> dict:
    sk           = _sk(ctx)
    p            = _period(ctx)
    months_count = len(p.get("months") or []) or 3
    period_label = p.get("period_label") or f"{months_count} мес."

    op_profit = _kv(sk, "operating_profit")
    revenue   = _kv(sk, "revenue")
    op_cf     = _kv(sk, "operating_cashflow")

    if op_profit is None and revenue is None:
        return _payload(
            QuestionIntent.ANNUAL_FORECAST, {}, {}, [],
            "Данные для прогноза не найдены.",
            status="no_data",
        )

    multiplier = 12 / months_count
    ann_op  = (op_profit * multiplier) if op_profit is not None else None
    ann_rev = (revenue   * multiplier) if revenue   is not None else None
    ann_cf  = (op_cf     * multiplier) if op_cf     is not None else None

    guidance = (
        f"Масштабирование: {months_count} мес. → 12 мес. (×{multiplier:.1f}).\n"
        f"Proxy-прибыль от основной деятельности за 12 мес.: {_signed(ann_op)} ₽.\n"
        f"Proxy-выручка за 12 мес.: {_rub(ann_rev)} ₽.\n"
        "ОБЯЗАТЕЛЬНО:\n"
        "  — называй результат «proxy-прибыль от основной деятельности», НЕ «чистая прибыль»;\n"
        "  — если чистой прибыли в отчёте нет — прямо скажи об этом;\n"
        "  — укажи ограничение: сезонность и изменения расходов не учтены."
    )

    return _payload(
        QuestionIntent.ANNUAL_FORECAST,
        facts={
            "period_label":                  period_label,
            "months_count":                  months_count,
            "multiplier":                    multiplier,
            "quarterly_operating_profit":    op_profit,
            "quarterly_operating_profit_rub":_signed(op_profit),
            "quarterly_revenue":             revenue,
            "quarterly_revenue_rub":         _rub(revenue),
            "quarterly_operating_cashflow":  op_cf,
            "quarterly_operating_cashflow_rub":_signed(op_cf),
        },
        calculations={
            "annual_operating_profit_proxy":    ann_op,
            "annual_operating_profit_proxy_rub":_signed(ann_op),
            "annual_revenue_proxy":             ann_rev,
            "annual_revenue_proxy_rub":         _rub(ann_rev),
            "annual_operating_cashflow_proxy":  ann_cf,
            "annual_operating_cashflow_proxy_rub":_signed(ann_cf),
        },
        limitations=[
            "Масштабирование — грубый proxy. Сезонность, изменения расходов и рыночная конъюнктура не учтены.",
            "Результат — «proxy-прибыль от основной деятельности», НЕ чистая прибыль.",
        ],
        answer_guidance=guidance,
    )


# ─── Handler: WORST_MONTH ─────────────────────────────────────────────────────

def _handle_worst_month(question: str, ctx: dict, history: list[dict]) -> dict:
    mk = _mk(ctx)
    nm = _month_names(ctx)
    if not mk:
        return _payload(QuestionIntent.WORST_MONTH, {}, {}, [], "", status="no_data")

    months_data = []
    for mk_key, m in mk.items():
        op   = _kv(m, "operating_profit")
        rev  = _kv(m, "revenue")
        beg  = _kv(m, "break_even_gap")
        name = (nm.get(mk_key) or mk_key).capitalize()
        months_data.append({
            "key":                  mk_key,
            "name":                 name,
            "operating_profit":     op,
            "operating_profit_rub": _signed(op),
            "revenue":              rev,
            "revenue_rub":          _rub(rev),
            "break_even_gap":       beg,
            "break_even_gap_rub":   _signed(beg),
            "break_even_gap_ratio": _kf(m, "break_even_gap_ratio"),
        })

    sortable = [m for m in months_data if m["operating_profit"] is not None]
    if not sortable:
        return _payload(QuestionIntent.WORST_MONTH, {}, {}, [], "", status="no_data")

    sorted_months = sorted(sortable, key=lambda m: m["operating_profit"])
    worst, best   = sorted_months[0], sorted_months[-1]

    q = question.lower()
    is_best_q = any(kw in q for kw in _BEST_KW)

    focus     = best if is_best_q else worst
    focus_adj = "лучший" if is_best_q else "худший"

    guidance = (
        f"{focus_adj.capitalize()} месяц по операционной прибыли: "
        f"{focus['name']} ({focus['operating_profit_rub']} ₽), "
        f"выручка {focus['revenue_rub']} ₽, "
        f"разрыв к точке безубыточности {focus['break_even_gap_rub']} ₽.\n"
        f"Для сравнения — {'худший' if is_best_q else 'лучший'} месяц: "
        f"{'worst' if is_best_q else 'best'}: {(worst if is_best_q else best)['name']} "
        f"({(worst if is_best_q else best)['operating_profit_rub']} ₽).\n"
        "Объясни: что именно повлияло — выручка упала или расходы выросли?"
    )

    return _payload(
        QuestionIntent.WORST_MONTH,
        facts={
            "all_months":    months_data,
            "worst_month":   worst,
            "best_month":    best,
            "is_best_question": is_best_q,
        },
        calculations={},
        limitations=[],
        answer_guidance=guidance,
    )


# ─── Handler: PROJECT_PROFITABILITY ──────────────────────────────────────────

def _handle_project_profitability(question: str, ctx: dict, history: list[dict]) -> dict:
    pk = _pk(ctx)
    if not pk:
        return _payload(QuestionIntent.PROJECT_PROFITABILITY, {}, {}, [], "", status="no_data")

    profitable, unprofitable = [], []
    for name, p in pk.items():
        op  = _kv(p, "operating_profit")
        rev = _kv(p, "revenue")
        beg = _kv(p, "break_even_gap")
        gm  = _kv(p, "gross_margin")
        entry = {
            "name":                name,
            "operating_profit":    op,
            "operating_profit_rub":_signed(op),
            "revenue":             rev,
            "revenue_rub":         _rub(rev),
            "break_even_gap":      beg,
            "break_even_gap_rub":  _signed(beg),
            "gross_margin_pct":    _pct(gm),
            "contribution":        _ki(p, "contribution_to_total_profit"),
        }
        if op is not None and op >= 0:
            profitable.append(entry)
        else:
            unprofitable.append(entry)

    profitable.sort(  key=lambda x: x["operating_profit"] or 0, reverse=True)
    unprofitable.sort(key=lambda x: x["operating_profit"] or 0)

    most_critical = unprofitable[0] if unprofitable else None

    guidance = (
        f"Прибыльных: {len(profitable)}, убыточных: {len(unprofitable)}.\n"
        "Перечисли ВСЕ проекты из facts.profitable и facts.unprofitable с конкретными цифрами.\n"
    )
    if most_critical:
        guidance += (
            f"Наиболее критичный убыточный: {most_critical['name']} "
            f"({most_critical['operating_profit_rub']} ₽, "
            f"разрыв к точке безуб. {most_critical['break_even_gap_rub']} ₽)."
        )

    return _payload(
        QuestionIntent.PROJECT_PROFITABILITY,
        facts={
            "profitable":     profitable,
            "unprofitable":   unprofitable,
            "total_projects": len(pk),
            "most_critical_unprofitable": most_critical,
        },
        calculations={},
        limitations=[],
        answer_guidance=guidance,
    )


# ─── Handler: BREAK_EVEN_PERIOD ──────────────────────────────────────────────

def _handle_break_even_period(question: str, ctx: dict, history: list[dict]) -> dict:
    sk = _sk(ctx)
    mk = _mk(ctx)
    nm = _month_names(ctx)

    bev   = _kv(sk, "break_even_revenue")
    rev   = _kv(sk, "revenue")
    beg   = _kv(sk, "break_even_gap")
    begrf = _kf(sk, "break_even_gap_ratio")

    if bev is None:
        return _payload(QuestionIntent.BREAK_EVEN_PERIOD, {}, {}, [], "", status="no_data")

    shortfall = (-beg if beg is not None and beg < 0 else 0)

    monthly = []
    for mk_key in sorted(mk.keys()):
        m    = mk[mk_key]
        name = (nm.get(mk_key) or mk_key).capitalize()
        monthly.append({
            "month":                    name,
            "revenue_rub":              _kf(m, "revenue"),
            "break_even_revenue_rub":   _kf(m, "break_even_revenue"),
            "break_even_gap":           _kv(m, "break_even_gap"),
            "break_even_gap_rub":       _kf(m, "break_even_gap"),
            "break_even_gap_ratio_pct": _kf(m, "break_even_gap_ratio"),
        })

    gap_dir = "ВЫШЕ" if beg is not None and beg >= 0 else "НИЖЕ"
    guidance = (
        f"Proxy-точка безубыточности за период: {_rub(bev)} ₽.\n"
        f"Фактическая выручка: {_rub(rev)} ₽.\n"
        f"Разрыв: {_signed(beg)} ₽ — выручка {gap_dir} точки безубыточности.\n"
        f"Покрытие: {begrf}.\n"
        + (f"Не хватило до безубыточности: {_rub(shortfall)} ₽.\n" if shortfall > 0 else "") +
        "Покажи помесячную таблицу из calculations.monthly.\n"
        "ОБЯЗАТЕЛЬНО: укажи, что расчёт является proxy."
    )

    return _payload(
        QuestionIntent.BREAK_EVEN_PERIOD,
        facts={
            "break_even_revenue":     bev,
            "break_even_revenue_rub": _rub(bev),
            "current_revenue":        rev,
            "current_revenue_rub":    _rub(rev),
            "break_even_gap":         beg,
            "break_even_gap_rub":     _signed(beg),
            "break_even_gap_ratio":   begrf,
            "shortfall":              shortfall,
            "shortfall_rub":          _rub(shortfall) if shortfall else "—",
        },
        calculations={"monthly": monthly},
        limitations=[
            "Расчёт является proxy: расходы не разделены полностью на постоянные и переменные."
        ],
        answer_guidance=guidance,
    )


# ─── Handler: BREAK_EVEN_PROJECT ─────────────────────────────────────────────

def _handle_break_even_project(question: str, ctx: dict, history: list[dict]) -> dict:
    pk = _pk(ctx)
    if not pk:
        return _payload(QuestionIntent.BREAK_EVEN_PROJECT, {}, {}, [], "", status="no_data")

    q = question.lower()
    target: str | None = None
    for proj_name in pk.keys():
        if proj_name.lower() in q:
            target = proj_name
            break
    if target is None:
        for alias in _KNOWN_PROJECTS:
            if alias in q:
                for proj_name in pk.keys():
                    if alias in proj_name.lower():
                        target = proj_name
                        break
                if target:
                    break

    above, below = [], []
    for name, p in pk.items():
        bev = _kv(p, "break_even_revenue")
        beg = _kv(p, "break_even_gap")
        entry = {
            "name":                   name,
            "revenue_rub":            _kf(p, "revenue"),
            "operating_profit":       _kv(p, "operating_profit"),
            "operating_profit_rub":   _kf(p, "operating_profit"),
            "break_even_revenue":     bev,
            "break_even_revenue_rub": _rub(bev),
            "break_even_gap":         beg,
            "break_even_gap_rub":     _signed(beg),
            "break_even_gap_ratio":   _kf(p, "break_even_gap_ratio"),
            "gross_margin_pct":       _pct(_kv(p, "gross_margin")),
        }
        if beg is not None and beg >= 0:
            above.append(entry)
        else:
            below.append(entry)

    above.sort(key=lambda x: x["break_even_gap"] or 0, reverse=True)
    below.sort(key=lambda x: x["break_even_gap"] or 0)

    guidance = (
        f"{'Конкретный проект: ' + target + '.' if target else 'Проект не указан — покажи все.'}\n"
        "Раздели на два списка: выше точки безубыточности (facts.above_break_even) "
        "и ниже (facts.below_break_even).\n"
        "Для каждого: разрыв и покрытие.\n"
        "ОБЯЗАТЕЛЬНО: расчёт является proxy."
    )

    return _payload(
        QuestionIntent.BREAK_EVEN_PROJECT,
        facts={
            "target_project":   target,
            "above_break_even": above,
            "below_break_even": below,
        },
        calculations={},
        limitations=[
            "Расчёт является proxy: расходы не разделены полностью на постоянные и переменные."
        ],
        answer_guidance=guidance,
    )


# ─── Handler: EXPENSE_RANKING ─────────────────────────────────────────────────

def _handle_expense_ranking(question: str, ctx: dict, history: list[dict]) -> dict:
    pnl_totals = (ctx.get("pnl") or {}).get("totals") or {}
    cf_details = (ctx.get("cashflow") or {}).get("details") or {}

    _PNL_EXP = [
        ("payroll",                 "ФОТ — зарплата и взносы (БДР)"),
        ("payroll_related",         "ФОТ-связанные расходы (БДР)"),
        ("rent",                    "Аренда (БДР)"),
        ("marketing",               "Маркетинг (БДР)"),
        ("bank_fees",               "Банковские комиссии (БДР)"),
        ("communication",           "Связь (БДР)"),
        ("legal_services",          "Юридические услуги (БДР)"),
        ("it_expenses",             "ИТ-расходы (БДР)"),
        ("depreciation",            "Амортизация (БДР)"),
        ("other_operating_expenses","Прочие операционные расходы (БДР)"),
        ("taxes",                   "Налоги (БДР)"),
    ]
    _CF_EXP = [
        ("payroll",                 "Заработная плата — денежная выплата (ДДС)"),
        ("employee_taxes",          "Налоги за сотрудников (ДДС)"),
        ("social_contributions",    "Взносы в фонды (ДДС)"),
        ("personal_income_tax",     "НДФЛ (ДДС)"),
        ("income_tax",              "Налоги на доходы (ДДС)"),
        ("contractors",             "Оплата подрядчикам (ДДС)"),
        ("marketing",               "Маркетинг и реклама (ДДС)"),
        ("bank_fees",               "Банковские комиссии (ДДС)"),
        ("it_communication_services","IT, связь, сервисы (ДДС)"),
        ("materials",               "Оплата за ТМЦ/материалы (ДДС)"),
        ("other_operating_outflows","Прочие выплаты (ДДС)"),
    ]

    pnl_expenses = []
    for key, label in _PNL_EXP:
        v = pnl_totals.get(key)
        if v is not None:
            fv = float(v)
            if fv != 0:
                pnl_expenses.append({"label": label, "value": fv, "value_rub": _rub(fv)})
    pnl_expenses.sort(key=lambda x: abs(x["value"]), reverse=True)

    cf_payments = []
    for key, label in _CF_EXP:
        v = cf_details.get(key)
        if v is not None:
            fv = float(v)
            if fv != 0:
                cf_payments.append({"label": label, "value": fv, "value_rub": _rub(fv)})
    cf_payments.sort(key=lambda x: abs(x["value"]), reverse=True)

    guidance = (
        "Два ОТДЕЛЬНЫХ раздела:\n"
        "1. БДР расходы (начисления): facts.pnl_expenses — отсортированы по убыванию.\n"
        "2. ДДС выплаты (фактические денежные): facts.cf_payments — отсортированы по убыванию.\n"
        "ЗАПРЕЩЕНО смешивать числа из БДР и ДДС.\n"
        "Назови крупнейшие статьи в каждом разделе."
    )

    return _payload(
        QuestionIntent.EXPENSE_RANKING,
        facts={
            "pnl_expenses": pnl_expenses,
            "cf_payments":  cf_payments,
        },
        calculations={},
        limitations=[
            "БДР расходы (начисления) и ДДС выплаты — разные величины. Нельзя их складывать или сравнивать напрямую.",
        ],
        answer_guidance=guidance,
    )


# ─── Handler: PROFIT_VS_CASH ──────────────────────────────────────────────────

def _handle_profit_vs_cash(question: str, ctx: dict, history: list[dict]) -> dict:
    sk        = _sk(ctx)
    cf_totals = (ctx.get("cashflow") or {}).get("totals") or {}

    op_profit = _kv(sk, "operating_profit")
    op_cf     = _kv(sk, "operating_cashflow")
    net_cf    = _kv(sk, "net_cashflow")
    gap       = _kv(sk, "profit_to_operating_cashflow_gap")

    fin_cf: float | None = None
    try:
        v = cf_totals.get("financial_cashflow")
        if v is not None:
            fin_cf = float(v)
    except Exception:
        pass

    if op_profit is None and op_cf is None:
        return _payload(QuestionIntent.PROFIT_VS_CASH, {}, {}, [], "", status="no_data")

    if gap is None and op_profit is not None and op_cf is not None:
        gap = op_cf - op_profit

    guidance = (
        f"Операционная прибыль (БДР): {_signed(op_profit)} ₽.\n"
        f"Операционный денежный поток (ДДС): {_signed(op_cf)} ₽.\n"
        f"Разрыв (ДДС − БДР): {_signed(gap)} ₽.\n"
        f"Финансовый поток: {_signed(fin_cf)} ₽ — объясни его природу (займы, дивиденды, ВГО).\n"
        f"Чистый денежный поток: {_signed(net_cf)} ₽.\n"
        "Объясни: разрыв — нормальное явление. Выставленные счета ≠ поступившие деньги.\n"
        "НЕ называй финансовый поток проблемой — объясни его роль."
    )

    return _payload(
        QuestionIntent.PROFIT_VS_CASH,
        facts={
            "operating_profit":      op_profit,
            "operating_profit_rub":  _signed(op_profit),
            "operating_cashflow":    op_cf,
            "operating_cashflow_rub":_signed(op_cf),
            "financial_cashflow":    fin_cf,
            "financial_cashflow_rub":_signed(fin_cf),
            "net_cashflow":          net_cf,
            "net_cashflow_rub":      _signed(net_cf),
            "gap_profit_vs_cashflow":gap,
            "gap_rub":               _signed(gap),
        },
        calculations={},
        limitations=[
            "БДР — начисления (метод начисления). ДДС — фактическое движение денег.",
            "Разрыв — нормальное явление, не обязательно проблема.",
        ],
        answer_guidance=guidance,
    )


# ─── Handler registry and main entry point ────────────────────────────────────

_HANDLERS = {
    QuestionIntent.PAYROLL_BENCHMARK:        _handle_payroll_benchmark,
    QuestionIntent.REVENUE_GROWTH_SCENARIO:  _handle_revenue_growth_scenario,
    QuestionIntent.COST_PERCENTAGE_FOLLOWUP: _handle_cost_percentage_followup,
    QuestionIntent.ANNUAL_FORECAST:          _handle_annual_forecast,
    QuestionIntent.WORST_MONTH:              _handle_worst_month,
    QuestionIntent.PROJECT_PROFITABILITY:    _handle_project_profitability,
    QuestionIntent.BREAK_EVEN_PERIOD:        _handle_break_even_period,
    QuestionIntent.BREAK_EVEN_PROJECT:       _handle_break_even_project,
    QuestionIntent.EXPENSE_RANKING:          _handle_expense_ranking,
    QuestionIntent.PROFIT_VS_CASH:           _handle_profit_vs_cash,
}


def route_question(
    question: str,
    analysis_context: dict,
    history: list[dict] | None = None,
) -> dict:
    """
    Classify question, run deterministic handler, return answer_payload.

    Returns:
        {intent, status, facts, calculations, limitations, answer_guidance, scenario}
    """
    history = history or []
    intent  = detect_intent(question, history)
    handler = _HANDLERS.get(intent)
    if handler:
        return handler(question, analysis_context, history)
    return _payload(
        intent, {}, {}, [],
        "Объясни вопрос, используя данные из финансового контекста.",
    )
