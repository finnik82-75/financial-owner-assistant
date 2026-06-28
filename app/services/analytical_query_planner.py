"""
Analytical query planner: converts free-form questions to structured query plans.

Primary path:  comprehensive rule-based classifier (fast, predictable).
For unknowns:  LLM JSON planner as fallback.
"""
from __future__ import annotations

import json
import re

from app.config import settings

# ─── Keyword sets ─────────────────────────────────────────────────────────────

_PAYROLL_KW    = frozenset(["фот", "фонд оплаты", "зарплат", "заработн"])
_NORM_KW       = frozenset(["приемлем", "нормальн", "должен", "сколько должен",
                             "сколько нужно", "норматив", "допустим", "ориентир", "оптимальн"])
_REVENUE_KW    = frozenset(["выручк", "продаж"])
_GROWTH_KW     = frozenset(["вырас", "увеличи", "добавит", "прибавит", "прирост", "рост"])
_FORECAST_KW   = frozenset(["за год", "на год", "годовой", "12 месяц", "12 мес",
                             "ежегодн", "экстраполир", "пропорция сохранится"])
_WORST_KW      = frozenset(["худш", "слабый", "слабее", "наихудш"])
_BEST_KW       = frozenset(["лучший", "лучш", "наилучш", "сильный"])
_MONTH_KW      = frozenset(["месяц", "март", "феврал", "январ", "апрел", "май",
                             "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"])
_PROJECT_KW    = frozenset(["проект", "направлен", "авторадио", "европа", "ретро",
                             "сайт", "наружн", "заб тв", "забmedia"])
_PROF_KW       = frozenset(["убыточн", "прибыльн", "прибыл", "тянут", "тянет",
                             "минусе", "плюсе", "в минус", "в плюс"])
_LOSS_PROJ_KW   = frozenset(["убыточные", "убыточных", "убыточными",
                              "в минусе", "в минус", "кто в минус",
                              "тянут вниз", "тянет вниз"])
_PROFIT_PROJ_KW = frozenset(["прибыльные", "прибыльных", "прибыльными",
                              "в плюсе", "в плюс", "кто в плюс", "выше нуля"])
_BREAKEVEN_KW  = frozenset(["безубыточ", "до нуля", "нулев", "не хватило",
                             "покрытие", "сколько не хватило"])
_EXPENSE_KW    = frozenset(["расход", "затрат", "крупнейш", "больше всего",
                             "куда уходит", "куда идут", "статьи", "проверить", "рейтинг расход"])
_EXPENSE_QUAL  = frozenset(["какие", "топ", "больше всего", "крупнейш",
                             "куда", "статьи", "проверить", "рейтинг",
                             "долю", "доля", "долях", "доли", "процент", "% от"])
_EXPENSE_PCT_KW  = frozenset(["процент", "долю", "доля", "долях", "доли", "в %", "% от"])
_SCOPE_PNL_KW    = frozenset(["только по бдр", "по бдр", "бдр расходы", "расходы бдр",
                               "по начислен"])
_SCOPE_CF_KW     = frozenset(["только по ддс", "по ддс", "по деньгам", "по выплатам",
                               "денежные расходы", "движение денег", "денежные выплаты"])
_PCASH_KW      = frozenset(["прибыль и деньги", "деньги и прибыль", "бдр и ддс",
                             "ддс и бдр", "почему разница", "разрыв между",
                             "прибыль не равна", "отличается"])
_YEAR_TGT_KW   = frozenset(["до конца года", "к концу года", "за оставшиес",
                             "в оставшиес", "нужна выручка", "нужен результат",
                             "выйти в ноль", "чтобы выйти", "нужно зарабатывать"])
_MONTH_PERIOD  = frozenset(["в месяц", "ежемесячно", "каждый месяц", "в мес"])
_QUARTER_PERIOD= frozenset(["за квартал", "в квартал", "за период",
                             "за 3 месяц", "за три месяц"])

_KNOWN_PROJECTS = [
    "европа+", "авторадио", "ретро fm", "ретро фм", "ретро",
    "забmedia", "заб тв", "наружная реклама", "наружн", "сайт",
]

_AMOUNT_RE = re.compile(
    r"(?:на\s+|\+\s*)?(\d+(?:[.,]\d+)?)\s*(млн|млрд|миллион(?:ов)?|тыс(?:яч[иа]?)?)",
    re.IGNORECASE,
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


def _make_plan(
    intent: str,
    operations: list[str],
    entities: dict | None = None,
    parameters: dict | None = None,
    limitations: list | None = None,
    status: str = "success",
    clarification_question: str | None = None,
) -> dict:
    return {
        "status":                 status,
        "intent":                 intent,
        "operations":             operations,
        "entities":               entities or {},
        "parameters":             parameters or {},
        "required_data":          [],
        "clarification_question": clarification_question,
        "limitations":            limitations or [],
    }


def _general_plan() -> dict:
    return _make_plan("general_management_explanation", ["general_management_explanation"])


# ─── History accessors ────────────────────────────────────────────────────────

def _last_intent(history: list[dict]) -> str:
    """Get intent from the most recent history entry (new or old format)."""
    for entry in reversed(history[-4:]):
        intent = (
            (entry.get("query_plan") or {}).get("intent")
            or (entry.get("metadata") or {}).get("intent")
            or ""
        )
        if intent:
            return intent
    return ""


def _last_params(history: list[dict]) -> dict:
    """Get parameters from the most recent query_plan in history."""
    for entry in reversed(history[-3:]):
        p = (entry.get("query_plan") or {}).get("parameters") or {}
        if p:
            return p
    return {}


def _detect_expense_scope(q: str) -> str:
    """Return 'pnl_only', 'cashflow_only', or 'both' based on scope keywords."""
    has_pnl = any(kw in q for kw in _SCOPE_PNL_KW)
    has_cf  = any(kw in q for kw in _SCOPE_CF_KW)
    if has_pnl and not has_cf:
        return "pnl_only"
    if has_cf and not has_pnl:
        return "cashflow_only"
    return "both"


def _detect_project_subtype(q: str) -> str:
    """Return 'loss_projects_only', 'profitable_projects_only', or 'all_projects'."""
    if any(kw in q for kw in _LOSS_PROJ_KW):
        return "loss_projects_only"
    if any(kw in q for kw in _PROFIT_PROJ_KW):
        return "profitable_projects_only"
    return "all_projects"


# ─── Follow-up resolution ─────────────────────────────────────────────────────

def _resolve_followup(question: str, history: list[dict]) -> dict | None:
    if not history:
        return None

    q  = question.lower().strip()
    li = _last_intent(history)
    lp = _last_params(history)

    pct_m = _PCT_RE.search(q)

    # "А если 45%?" after payroll benchmark
    if pct_m and li == "payroll_benchmark":
        is_short = len(q.split()) <= 8
        is_clarifying = any(kw in q for kw in ["если", "при", "взять", "что будет"])
        if is_short or is_clarifying:
            try:
                pct = float(pct_m.group(1).replace(",", ".")) / 100
                if 0.01 <= pct <= 0.99:
                    return _make_plan(
                        "payroll_benchmark", ["simulate_payroll_ratio"],
                        parameters={"target_ratio": pct},
                    )
            except Exception:
                pass

    # "Агентские 10% от прироста" after revenue scenario
    if pct_m and li == "revenue_growth_scenario":
        cost_cue = any(
            kw in q for kw in
            ["прирост", "суммы", "этого", "агентск", "комисс", "расход", "от"]
        )
        if cost_cue:
            try:
                pct = float(pct_m.group(1).replace(",", ".")) / 100
                if 0.01 <= pct <= 0.99:
                    params = {**lp, "additional_cost_percent": pct, "inherit_from_history": True}
                    return _make_plan(
                        "revenue_growth_scenario", ["simulate_revenue_change"],
                        parameters=params,
                    )
            except Exception:
                pass

    # "сделай в %" / "покажи доли" / scope-change after expense_analysis
    if li == "expense_analysis":
        has_pct = any(kw in q for kw in _EXPENSE_PCT_KW)
        clarifying_with_pct = (
            any(kw in q for kw in ["сделай", "покаж", "преобраз", "перевед"])
            and "%" in q
        )
        new_scope = _detect_expense_scope(q)
        if has_pct or clarifying_with_pct:
            prev_scope = lp.get("scope", "both")
            scope = new_scope if new_scope != "both" else prev_scope
            return _make_plan(
                "expense_analysis", ["rank_expenses"],
                parameters={"as_percent": True, "scope": scope},
            )
        # Scope-only change ("а по ДДС?", "покажи только БДР")
        if new_scope != "both":
            return _make_plan(
                "expense_analysis", ["rank_expenses"],
                parameters={"as_percent": lp.get("as_percent", False), "scope": new_scope},
            )

    # "А по сайту?" / "покажи только убыточные?" after project analysis
    if li == "project_analysis":
        new_subtype = _detect_project_subtype(q)
        for p in _KNOWN_PROJECTS:
            if p in q:
                return _make_plan(
                    "project_analysis", ["rank_projects"],
                    entities={"project_filter": p},
                    parameters={"subtype": new_subtype if new_subtype != "all_projects"
                                           else lp.get("subtype", "all_projects")},
                )
        if new_subtype != "all_projects":
            return _make_plan(
                "project_analysis", ["rank_projects"],
                parameters={"subtype": new_subtype},
            )

    # "До конца года" as any follow-up
    if any(kw in q for kw in _YEAR_TGT_KW):
        return _make_plan("year_end_break_even", ["calculate_year_end_target"])

    return None


# ─── Rule-based planner ───────────────────────────────────────────────────────

def _rule_based_plan(question: str, ctx: dict, history: list[dict]) -> dict:
    q = question.lower()

    followup = _resolve_followup(question, history)
    if followup:
        return followup

    # Revenue growth scenario (requires parseable amount)
    if any(kw in q for kw in _REVENUE_KW) and any(kw in q for kw in _GROWTH_KW):
        m = _AMOUNT_RE.search(q)
        if m:
            amount = _parse_amount(m)
            if amount:
                period_basis = "month" if any(kw in q for kw in _MONTH_PERIOD) else "period"
                return _make_plan(
                    "revenue_growth_scenario", ["simulate_revenue_change"],
                    parameters={"amount": amount, "period_basis": period_basis},
                )

    # Payroll benchmark
    if any(kw in q for kw in _PAYROLL_KW) and any(kw in q for kw in _NORM_KW):
        pct_m  = _PCT_RE.search(q)
        params: dict = {}
        if pct_m:
            try:
                pct = float(pct_m.group(1).replace(",", ".")) / 100
                if 0.01 <= pct <= 0.99:
                    params["target_ratio"] = pct
            except Exception:
                pass
        return _make_plan("payroll_benchmark", ["simulate_payroll_ratio"], parameters=params)

    # Year-end target (before annual_forecast)
    if any(kw in q for kw in _YEAR_TGT_KW):
        return _make_plan("year_end_break_even", ["calculate_year_end_target"])

    # Annual forecast
    if any(kw in q for kw in _FORECAST_KW):
        return _make_plan("annual_forecast", ["annualize_result"])

    # Break-even (before month/project to catch "сколько не хватило")
    if any(kw in q for kw in _BREAKEVEN_KW):
        if any(p in q for p in _KNOWN_PROJECTS) or any(kw in q for kw in _PROJECT_KW):
            return _make_plan(
                "break_even_analysis", ["calculate_break_even"],
                entities={"scope": "project"},
            )
        return _make_plan(
            "break_even_analysis",
            ["calculate_break_even", "calculate_gap_to_break_even"],
        )

    # Worst/best month
    if (
        (any(kw in q for kw in _WORST_KW | _BEST_KW) and any(kw in q for kw in _MONTH_KW))
        or "какой месяц" in q
    ):
        return _make_plan("worst_month", ["compare_months"])

    # Project profitability
    if (any(kw in q for kw in _PROJECT_KW) and any(kw in q for kw in _PROF_KW)) or "какие проекты" in q:
        subtype = _detect_project_subtype(q)
        return _make_plan("project_analysis", ["rank_projects"], parameters={"subtype": subtype})

    # Named project with financial context
    for p in _KNOWN_PROJECTS:
        if p in q and any(kw in q for kw in _BREAKEVEN_KW | _PROF_KW | frozenset(["прибыл", "выручк"])):
            return _make_plan(
                "project_analysis", ["rank_projects"],
                entities={"project_filter": p},
            )

    # Expense ranking — triggered by expense keyword + qualifier OR scope keyword
    _has_scope_kw = (
        any(kw in q for kw in _SCOPE_PNL_KW)
        or any(kw in q for kw in _SCOPE_CF_KW)
    )
    if any(kw in q for kw in _EXPENSE_KW) and (
        any(kw in q for kw in _EXPENSE_QUAL) or _has_scope_kw
    ):
        as_pct = any(kw in q for kw in _EXPENSE_PCT_KW)
        scope  = _detect_expense_scope(q)
        params = {"as_percent": as_pct, "scope": scope}
        return _make_plan("expense_analysis", ["rank_expenses"], parameters=params)

    # Profit vs cash
    if any(kw in q for kw in _PCASH_KW):
        return _make_plan("profit_vs_cash", ["explain_profit_vs_cash"])
    if (
        "деньг" in q and ("прибыль" in q or "бдр" in q)
        and any(kw in q for kw in ["почему", "разница", "отличается", "не равна", "не совпадает"])
    ):
        return _make_plan("profit_vs_cash", ["explain_profit_vs_cash"])

    # Payroll by project → data limitations
    if any(kw in q for kw in _PAYROLL_KW) and any(p in q for p in _KNOWN_PROJECTS):
        return _make_plan(
            "data_limitations", ["explain_data_limitations"],
            entities={"data_type": "project_payroll"},
        )

    # Payroll without benchmark context
    if any(kw in q for kw in _PAYROLL_KW):
        return _make_plan("payroll_benchmark", ["simulate_payroll_ratio"])

    return _general_plan()


# ─── LLM planner ─────────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """Ты — аналитический планировщик финансовых запросов.
Переводи вопрос в JSON-план. НЕ отвечай на вопрос. НЕ считай цифры.

ДОСТУПНЫЕ ОПЕРАЦИИ:
compare_months — сравнение месяцев по прибыли
rank_projects — прибыльность проектов
rank_expenses — крупнейшие расходы (БДР и ДДС отдельно)
calculate_break_even — точка безубыточности и разрыв
simulate_revenue_change(amount,period_basis) — сценарий роста выручки
simulate_payroll_ratio(target_ratio) — ФОТ% от выручки
calculate_year_end_target — нужная выручка до конца года
annualize_result — масштабирование квартала на год
explain_profit_vs_cash — разрыв БДР vs ДДС
explain_data_limitations(data_type) — чего нет в данных
general_management_explanation — общий управленческий вопрос

ПРАВИЛА:
- Неоднозначно → needs_clarification, clarification_question = вопрос
- Данных в отчёте нет → unsupported
- Общий совет/стратегия → general_management_explanation

ВЫВОД: строго JSON, без markdown:
{"status":"success","intent":"...","operations":["..."],"entities":{},"parameters":{},"required_data":[],"clarification_question":null,"limitations":[]}"""


def _call_llm_planner(question: str, ctx: dict, history: list[dict]) -> dict | None:
    if not settings.openai_api_key:
        return None
    try:
        period   = ctx.get("period") or {}
        months   = period.get("month_names") or []
        projects = list(((ctx.get("kpi") or {}).get("project_kpi") or {}).keys())

        history_ctx = ""
        for entry in (history or [])[-2:]:
            intent = (
                (entry.get("query_plan") or {}).get("intent")
                or (entry.get("metadata") or {}).get("intent")
                or ""
            )
            q = (entry.get("question") or "")[:60]
            if intent:
                history_ctx += f"  Q: \"{q}\" → {intent}\n"

        context_str = (
            f"Период: {period.get('period_label', '')}, "
            f"месяцы: {', '.join(months)}, "
            f"проекты: {', '.join(projects) or 'нет'}\n"
            f"История:\n{history_ctx or '(начало диалога)'}"
        )

        from openai import OpenAI
        client   = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _PLANNER_SYSTEM},
                {"role": "user",   "content": f"КОНТЕКСТ:\n{context_str}\n\nВОПРОС: {question}"},
            ],
            temperature=0.0,
            max_tokens=400,
        )
        raw = (response.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw.strip())
        plan = json.loads(raw.strip())
        if plan.get("status") and plan.get("intent") and plan.get("operations"):
            return plan
        return None
    except Exception:
        return None


# ─── Main entry point ─────────────────────────────────────────────────────────

def build_query_plan(
    question: str,
    analysis_context: dict,
    history: list[dict] | None = None,
) -> dict:
    """
    Convert a user question to a structured query_plan.

    Returns:
        {status, intent, operations, entities, parameters,
         required_data, clarification_question, limitations}
    """
    history = history or []
    rb = _rule_based_plan(question, analysis_context, history)
    if rb["intent"] != "general_management_explanation":
        return rb
    # For unrecognized questions, try LLM planner
    llm = _call_llm_planner(question, analysis_context, history)
    if llm:
        return llm
    return rb
