"""
Analytical executor: runs deterministic Python handlers for query_plan operations.

Returns answer_payload with all pre-computed figures.
LLM never sees raw analysis_context numbers — only answer_payload.
"""
from __future__ import annotations

import re

from app.services.expense_hierarchy import (
    classify_pnl_expense_line,
    classify_cashflow_detail_line,
)

# ─── Format helpers ───────────────────────────────────────────────────────────

def _rub(v) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except Exception:
        return "—"
    return f"{'-' if v < 0 else ''}{abs(v):,.0f}".replace(",", " ")


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
    e = d.get(key)
    if e and e.get("value") is not None:
        try:
            return float(e["value"])
        except Exception:
            pass
    return None


def _kf(d: dict, key: str) -> str:
    return ((d.get(key) or {}).get("formatted") or "—")


def _sk(ctx: dict) -> dict:
    return (ctx.get("kpi") or {}).get("summary_kpi") or {}


def _mk(ctx: dict) -> dict:
    return (ctx.get("kpi") or {}).get("monthly_kpi") or {}


def _pk(ctx: dict) -> dict:
    return (ctx.get("kpi") or {}).get("project_kpi") or {}


def _month_names(ctx: dict) -> dict[str, str]:
    p = ctx.get("period") or {}
    return dict(zip(p.get("months") or [], p.get("month_names") or []))


# ─── Payload factories ────────────────────────────────────────────────────────

def _ok(
    intent: str,
    facts: dict,
    calculations: dict,
    tables: list,
    limitations: list,
    guidance: str,
    metadata: dict | None = None,
    source_fields: list | None = None,
) -> dict:
    return {
        "status":          "success",
        "intent":          intent,
        "facts":           facts,
        "calculations":    calculations,
        "tables":          tables,
        "limitations":     limitations,
        "answer_guidance": guidance,
        "source_fields":   source_fields or [],
        "metadata":        metadata or {},
    }


def _no_data(intent: str, guidance: str = "") -> dict:
    return {
        "status":          "no_data",
        "intent":          intent,
        "facts":           {},
        "calculations":    {},
        "tables":          [],
        "limitations":     [],
        "answer_guidance": guidance or "Данные не найдены в загруженных отчётах.",
        "source_fields":   [],
        "metadata":        {},
    }


def _clarify(intent: str, question: str) -> dict:
    return {
        "status":                 "needs_clarification",
        "intent":                 intent,
        "facts":                  {},
        "calculations":           {},
        "tables":                 [],
        "limitations":            [],
        "answer_guidance":        f"Уточни: {question}",
        "clarification_question": question,
        "source_fields":          [],
        "metadata":               {},
    }


# ─── History scenario accessor ────────────────────────────────────────────────

def _get_history_scenario(history: list[dict]) -> dict | None:
    """Get revenue scenario figures from recent history (new or old format)."""
    for entry in reversed(history[-5:]):
        # New format: answer_payload.facts
        ap    = entry.get("answer_payload") or {}
        facts = ap.get("facts") or {}
        calcs = ap.get("calculations") or {}
        if facts.get("total_revenue_increase"):
            return {**facts, **calcs}
        # Old format: metadata.scenario
        meta = entry.get("metadata") or {}
        sc   = meta.get("scenario") or {}
        if sc.get("total_revenue_increase") or sc.get("total_increase"):
            return sc
    return None


# ─── Handlers ─────────────────────────────────────────────────────────────────

def _handle_compare_months(plan: dict, ctx: dict, history: list[dict]) -> dict:
    mk = _mk(ctx)
    nm = _month_names(ctx)
    if not mk:
        return _no_data("worst_month")

    months_data = []
    for mk_key, m in sorted(mk.items()):
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
        return _no_data("worst_month")

    by_profit = sorted(sortable, key=lambda m: m["operating_profit"])
    worst, best = by_profit[0], by_profit[-1]

    table = {
        "id":      "monthly_comparison",
        "title":   "Операционная прибыль по месяцам",
        "headers": ["Месяц", "Выручка", "Прибыль", "Разрыв до безуб."],
        "rows": [
            [m["name"], m["revenue_rub"], m["operating_profit_rub"], m["break_even_gap_rub"]]
            for m in by_profit
        ],
    }

    guidance = (
        f"Худший месяц: {worst['name']} (прибыль {worst['operating_profit_rub']} ₽, "
        f"разрыв {worst['break_even_gap_rub']} ₽).\n"
        f"Лучший месяц: {best['name']} (прибыль {best['operating_profit_rub']} ₽).\n"
        "Покажи таблицу из tables[0] со всеми месяцами.\n"
        "Объясни: что повлияло — выручка упала или расходы выросли?"
    )

    return _ok("worst_month",
        facts={"all_months": months_data, "worst": worst, "best": best},
        calculations={},
        tables=[table],
        limitations=[],
        guidance=guidance,
    )


def _handle_rank_projects(plan: dict, ctx: dict, history: list[dict]) -> dict:
    pk = _pk(ctx)
    if not pk:
        return _no_data("project_analysis")

    project_filter = (plan.get("entities") or {}).get("project_filter")
    subtype        = (plan.get("parameters") or {}).get("subtype", "all_projects")

    profitable, unprofitable = [], []
    for name, p in pk.items():
        if project_filter and project_filter.lower() not in name.lower():
            continue
        op  = _kv(p, "operating_profit")
        rev = _kv(p, "revenue")
        beg = _kv(p, "break_even_gap")
        gm  = _kv(p, "gross_margin")
        exp = (rev - op) if (rev is not None and op is not None) else None
        entry = {
            "name":                 name,
            "operating_profit":     op,
            "operating_profit_rub": _signed(op),
            "revenue":              rev,
            "revenue_rub":          _rub(rev),
            "expenses_rub":         _rub(exp) if exp is not None else "—",
            "break_even_gap":       beg,
            "break_even_gap_rub":   _signed(beg),
            "gross_margin_pct":     _pct(gm),
            "break_even_gap_ratio": _kf(p, "break_even_gap_ratio"),
            "contribution":         ((p.get("contribution_to_total_profit") or {}).get("interpretation") or ""),
        }
        if op is not None and op >= 0:
            profitable.append(entry)
        else:
            unprofitable.append(entry)

    if project_filter and not profitable and not unprofitable:
        return _no_data("project_analysis", f"Проект «{project_filter}» не найден в данных.")

    profitable.sort(key=lambda x: x["operating_profit"] or 0, reverse=True)
    unprofitable.sort(key=lambda x: x["operating_profit"] or 0)

    # Select rows and columns based on subtype
    if subtype == "loss_projects_only":
        displayed    = unprofitable
        other_names  = [p["name"] for p in profitable]
        table_title  = "Убыточные проекты"
        headers      = ["Проект", "Выручка", "Расходы", "Прибыль", "Разрыв до безуб."]
        rows         = [
            [p["name"], p["revenue_rub"], p["expenses_rub"],
             p["operating_profit_rub"], p["break_even_gap_rub"]]
            for p in displayed
        ]
    elif subtype == "profitable_projects_only":
        displayed    = profitable
        other_names  = [p["name"] for p in unprofitable]
        table_title  = "Прибыльные проекты"
        headers      = ["Проект", "Выручка", "Прибыль", "Маржа", "Разрыв к безуб."]
        rows         = [
            [p["name"], p["revenue_rub"], p["operating_profit_rub"],
             p["gross_margin_pct"], p["break_even_gap_rub"]]
            for p in displayed
        ]
    else:
        displayed    = profitable + unprofitable
        other_names  = []
        table_title  = "Прибыльность проектов"
        headers      = ["Проект", "Выручка", "Прибыль", "Разрыв к безуб.", "Маржа"]
        rows         = [
            [p["name"], p["revenue_rub"], p["operating_profit_rub"],
             p["break_even_gap_rub"], p["gross_margin_pct"]]
            for p in displayed
        ]

    table = {
        "id":      "project_profitability",
        "title":   table_title,
        "headers": headers,
        "rows":    rows,
    }

    most_critical = unprofitable[0] if unprofitable else None

    if subtype == "loss_projects_only":
        guidance = (
            f"Убыточных проектов: {len(unprofitable)}. "
            + (f"Наибольший убыток: {most_critical['name']} "
               f"({most_critical['operating_profit_rub']} ₽).\n" if most_critical else "")
            + "Покажи таблицу убыточных проектов.\n"
            + (f"Прибыльные проекты ({', '.join(other_names)}) упомяни ОДНОЙ строкой "
               "ПОСЛЕ таблицы — без их цифр. "
               "НЕ добавляй прибыльные проекты в таблицу." if other_names else "")
        )
    elif subtype == "profitable_projects_only":
        guidance = (
            f"Прибыльных проектов: {len(profitable)}.\n"
            "Покажи таблицу прибыльных проектов.\n"
            + (f"Убыточные проекты ({', '.join(other_names)}) упомяни ОДНОЙ строкой "
               "ПОСЛЕ таблицы. "
               "НЕ добавляй убыточные проекты в таблицу." if other_names else "")
        )
    else:
        guidance = (
            f"Прибыльных: {len(profitable)}, убыточных: {len(unprofitable)}.\n"
            + (f"Самый убыточный: {most_critical['name']} "
               f"({most_critical['operating_profit_rub']} ₽).\n" if most_critical else "")
            + "Покажи таблицу из tables[0]."
        )

    limitations = [
        "Прибыль проекта — proxy: расходы распределены пропорционально, "
        "не разделены на постоянные и переменные.",
        "Точка безубыточности проекта рассчитана на основе распределённых расходов проекта.",
    ]

    return _ok("project_analysis",
        facts={
            "profitable":     profitable,
            "unprofitable":   unprofitable,
            "project_filter": project_filter,
            "subtype":        subtype,
            "other_names":    other_names,
        },
        calculations={},
        tables=[table],
        limitations=limitations,
        guidance=guidance,
    )


# ─── Expense breakdown constants ─────────────────────────────────────────────
# Only pure expense line items — no revenue, gross/operating profit, ВГО, EBITDA.

_PNL_EXPENSE_KEYS: list[tuple[str, str]] = [
    ("payroll",                  "ФОТ"),
    ("payroll_related",          "ФОТ-связанные расходы"),
    ("rent",                     "Аренда"),
    ("marketing",                "Маркетинг"),
    ("bank_fees",                "Банковские комиссии"),
    ("communication",            "Связь"),
    ("legal_services",           "Юридические услуги"),
    ("it_expenses",              "ИТ-расходы"),
    ("depreciation",             "Амортизация"),
    ("other_operating_expenses", "Прочие операционные расходы"),
    ("taxes",                    "Налоги"),
]

# Primary safe CF level — employee_taxes is the aggregate; no double-counting.
_CF_PRIMARY_OUTFLOW_KEYS: list[tuple[str, str]] = [
    ("payroll",                   "Заработная плата"),
    ("contractors",               "Оплата подрядчикам"),
    ("employee_taxes",            "Налоги за сотрудников"),
    ("income_tax",                "Налоги на доходы"),
    ("marketing",                 "Маркетинг и реклама"),
    ("bank_fees",                 "Банковские комиссии"),
    ("it_communication_services", "IT, связь, сервисы"),
    ("materials",                 "Оплата за ТМЦ/материалы"),
    ("other_operating_outflows",  "Прочие выплаты"),
]

# Sub-items of employee_taxes — shown as a separate breakdown block.
_CF_TAX_BREAKDOWN_KEYS: list[tuple[str, str]] = [
    ("social_contributions", "Взносы в фонды"),
    ("personal_income_tax",  "НДФЛ"),
]


def build_expense_breakdown(
    ctx: dict,
    as_percent: bool = False,
    scope: str = "both",
) -> dict:
    """
    Build structured expense breakdown from analysis_context.

    scope:
      "pnl_only"      — only БДР section  (cashflow_outflows = None)
      "cashflow_only" — only ДДС section  (pnl_expenses = None)
      "both"          — both sections (default)

    The CF section separates _CF_TAX_BREAKDOWN_KEYS (НДФЛ, Взносы)
    into a 'tax_breakdown' sub-list when employee_taxes is present,
    so they are never double-counted against the primary level.
    """
    pnl_totals = (ctx.get("pnl") or {}).get("totals") or {}
    cf_totals  = (ctx.get("cashflow") or {}).get("totals") or {}
    cf_details = (ctx.get("cashflow") or {}).get("details") or {}

    pnl_section: dict | None = None
    cf_section:  dict | None = None

    # ── БДР expenses ──────────────────────────────────────────────────────────
    if scope in ("both", "pnl_only"):
        pnl_base: float | None = None
        oe = pnl_totals.get("operating_expenses")
        if oe is not None:
            try:
                pnl_base = abs(float(oe))
            except Exception:
                pass

        pnl_items: list[dict] = []
        pnl_included_keys: set[str] = set()
        seen_pnl: set[str] = set()
        for key, label in _PNL_EXPENSE_KEYS:
            v = pnl_totals.get(key)
            if v is None:
                continue
            try:
                fv = abs(float(v))
            except Exception:
                continue
            if fv > 0 and label not in seen_pnl:
                seen_pnl.add(label)
                pnl_items.append({"label": label, "amount": fv})
                pnl_included_keys.add(key)

        pnl_items.sort(key=lambda x: x["amount"], reverse=True)

        actual_pnl_base = (pnl_base if (pnl_base and pnl_base > 0)
                           else sum(x["amount"] for x in pnl_items) or 1.0)
        for item in pnl_items:
            item["share"]      = round(item["amount"] / actual_pnl_base, 4)
            item["amount_rub"] = _rub(item["amount"])
            item["share_pct"]  = _pct(item["share"])

        pnl_excluded: list[dict] = []
        for key, val in pnl_totals.items():
            if key in pnl_included_keys:
                continue
            try:
                fv = abs(float(val))
            except Exception:
                continue
            if fv <= 0:
                continue
            cls = classify_pnl_expense_line(key)
            pnl_excluded.append({
                "key":    key,
                "label":  cls["label"],
                "level":  cls["level"],
                "reason": cls["reason"],
            })

        pnl_items_sum = sum(i["amount"] for i in pnl_items)
        pnl_overflow  = pnl_items_sum > actual_pnl_base * 1.15

        pnl_lims: list[str] = ["Доли рассчитаны от операционных расходов по БДР."]
        if pnl_base is None:
            pnl_lims.append(
                "Строка «Операционные расходы» отсутствует; "
                "доля рассчитана от суммы отдельных статей."
            )
        if pnl_overflow:
            pnl_lims.append(
                "Сумма детальных строк превышает базу более чем на 15%. "
                "Возможно, в отчёте есть пересекающиеся группы и детализация. "
                "Доли показаны справочно."
            )

        pnl_section = {
            "base_label":       "Операционные расходы по БДР",
            "base_amount":      actual_pnl_base,
            "base_amount_rub":  _rub(actual_pnl_base),
            "items":            pnl_items,
            "excluded":         pnl_excluded,
            "overflow_warning": pnl_overflow,
            "limitations":      pnl_lims,
        }

    # ── ДДС outflows ──────────────────────────────────────────────────────────
    if scope in ("both", "cashflow_only"):
        cf_base: float | None = None
        oo = cf_totals.get("operating_outflows")
        if oo is not None:
            try:
                cf_base = abs(float(oo))
            except Exception:
                pass

        # Primary level — safe aggregates (no double-counting)
        cf_items: list[dict] = []
        cf_included_keys: set[str] = set()
        seen_cf: set[str] = set()
        for key, label in _CF_PRIMARY_OUTFLOW_KEYS:
            v = cf_details.get(key)
            if v is None:
                continue
            try:
                fv = abs(float(v))
            except Exception:
                continue
            if fv > 0 and label not in seen_cf:
                seen_cf.add(label)
                cf_items.append({"label": label, "amount": fv})
                cf_included_keys.add(key)

        has_employee_taxes = "employee_taxes" in cf_included_keys

        # Tax detail — sub-items of employee_taxes
        cf_tax_items: list[dict] = []
        for key, label in _CF_TAX_BREAKDOWN_KEYS:
            v = cf_details.get(key)
            if v is None:
                continue
            try:
                fv = abs(float(v))
            except Exception:
                continue
            if fv > 0:
                cf_included_keys.add(key)
                if has_employee_taxes:
                    cf_tax_items.append({"label": label, "amount": fv})
                else:
                    if label not in seen_cf:
                        seen_cf.add(label)
                        cf_items.append({"label": label, "amount": fv})

        cf_items.sort(key=lambda x: x["amount"], reverse=True)

        actual_cf_base = (cf_base if (cf_base and cf_base > 0)
                          else sum(x["amount"] for x in cf_items) or 1.0)
        for item in cf_items:
            item["share"]      = round(item["amount"] / actual_cf_base, 4)
            item["amount_rub"] = _rub(item["amount"])
            item["share_pct"]  = _pct(item["share"])

        for item in cf_tax_items:
            item["amount_rub"] = _rub(item["amount"])

        cf_excluded: list[dict] = []
        for key, val in cf_details.items():
            if key in cf_included_keys:
                continue
            try:
                fv = abs(float(val))
            except Exception:
                continue
            if fv <= 0:
                continue
            cls = classify_cashflow_detail_line(key)
            cf_excluded.append({
                "key":    key,
                "label":  cls["label"],
                "level":  cls["level"],
                "reason": cls["reason"],
            })

        cf_items_sum = sum(i["amount"] for i in cf_items)
        cf_overflow  = cf_items_sum > actual_cf_base * 1.15

        cf_lims: list[str] = [
            "Доли рассчитаны от операционных выплат по ДДС.",
            "ДДС показывает движение денег, а не начисленные расходы.",
        ]
        if has_employee_taxes and cf_tax_items:
            cf_lims.append(
                "«Налоги за сотрудников» включают НДФЛ и взносы в фонды. "
                "Расшифровка показана отдельно и не учитывается в расчёте доли."
            )
        if cf_overflow:
            cf_lims.append(
                "Сумма детальных строк превышает базу более чем на 15%. "
                "Возможно, в отчёте есть пересекающиеся группы и детализация. "
                "Доли показаны справочно."
            )

        cf_section = {
            "base_label":       "Операционные выплаты по ДДС",
            "base_amount":      actual_cf_base,
            "base_amount_rub":  _rub(actual_cf_base),
            "items":            cf_items,
            "tax_breakdown":    cf_tax_items,
            "excluded":         cf_excluded,
            "overflow_warning": cf_overflow,
            "limitations":      cf_lims,
        }

    return {
        "pnl_expenses":      pnl_section,
        "cashflow_outflows": cf_section,
    }


def _render_expense_breakdown_md(breakdown: dict, as_percent: bool = False) -> str:
    """Pre-render expense breakdown to Markdown tables (Python, no LLM)."""
    lines: list[str] = []

    def _section(section: dict | None) -> None:
        if not section:
            return
        title = f"{section.get('base_label', '')} — {section.get('base_amount_rub', '—')} ₽"
        lines.append(f"**{title}**")
        lines.append("")
        if as_percent:
            lines.append("| Статья | Сумма, ₽ | Доля |")
            lines.append("| --- | --- | --- |")
            for item in section.get("items") or []:
                lines.append(
                    f"| {item['label']} | {item['amount_rub']} | {item['share_pct']} |"
                )
        else:
            lines.append("| Статья | Сумма, ₽ |")
            lines.append("| --- | --- |")
            for item in section.get("items") or []:
                lines.append(f"| {item['label']} | {item['amount_rub']} |")
        if section.get("overflow_warning"):
            lines.append("")
            lines.append(
                "> *Сумма детальных строк превышает базу более чем на 15%. "
                "Возможно, в отчёте есть пересекающиеся группы и детализация. "
                "Доли показаны справочно.*"
            )
        tax_items = section.get("tax_breakdown") or []
        if tax_items:
            lines.append("")
            lines.append("*Расшифровка налогов за сотрудников:*")
            lines.append("")
            lines.append("| Статья | Сумма, ₽ |")
            lines.append("| --- | --- |")
            for item in tax_items:
                lines.append(f"| {item['label']} | {item['amount_rub']} |")
        lines.append("")

    _section(breakdown.get("pnl_expenses"))
    _section(breakdown.get("cashflow_outflows"))
    return "\n".join(lines)


def _handle_rank_expenses(plan: dict, ctx: dict, history: list[dict]) -> dict:
    as_percent = bool((plan.get("parameters") or {}).get("as_percent", False))
    scope      = (plan.get("parameters") or {}).get("scope", "both")
    breakdown  = build_expense_breakdown(ctx, as_percent=as_percent, scope=scope)

    pnl_exp = breakdown.get("pnl_expenses")   # None when scope == "cashflow_only"
    cf_exp  = breakdown.get("cashflow_outflows")  # None when scope == "pnl_only"

    prerendered_md = _render_expense_breakdown_md(breakdown, as_percent=as_percent)

    # Tables — only non-None sections
    tables: list[dict] = []
    hdrs = ["Статья", "Сумма, ₽", "Доля"] if as_percent else ["Статья", "Сумма, ₽"]
    if pnl_exp:
        rows = (
            [[i["label"], i["amount_rub"], i["share_pct"]] for i in pnl_exp["items"]]
            if as_percent else
            [[i["label"], i["amount_rub"]] for i in pnl_exp["items"]]
        )
        tables.append({
            "id":      "pnl_expenses",
            "title":   f"{pnl_exp['base_label']} — {pnl_exp['base_amount_rub']} ₽",
            "headers": hdrs,
            "rows":    rows,
        })
    if cf_exp:
        rows = (
            [[i["label"], i["amount_rub"], i["share_pct"]] for i in cf_exp["items"]]
            if as_percent else
            [[i["label"], i["amount_rub"]] for i in cf_exp["items"]]
        )
        tables.append({
            "id":      "cf_payments",
            "title":   f"{cf_exp['base_label']} — {cf_exp['base_amount_rub']} ₽",
            "headers": hdrs,
            "rows":    rows,
        })

    pnl_top = (pnl_exp["items"][0] if pnl_exp and pnl_exp["items"] else {})
    cf_top  = (cf_exp["items"][0]  if cf_exp  and cf_exp["items"]  else {})

    def _top_str(item: dict) -> str:
        s = f"{item.get('label', '—')} ({item.get('amount_rub', '—')} ₽"
        if as_percent:
            s += f", {item.get('share_pct', '—')}"
        return s + ")"

    if scope == "pnl_only":
        guidance = (
            f"Крупнейшая статья БДР: {_top_str(pnl_top)}.\n"
            "Только БДР. Таблицы преднарисованы. Добавь только короткий вывод.\n"
            "НЕ упоминай ДДС. НЕ пиши про несовместимость БДР и ДДС."
        )
    elif scope == "cashflow_only":
        guidance = (
            f"Крупнейшая выплата ДДС: {_top_str(cf_top)}.\n"
            "Только ДДС. Таблицы преднарисованы. Добавь только короткий вывод.\n"
            "НЕ упоминай БДР. НЕ пиши про несовместимость БДР и ДДС."
        )
    else:
        guidance = (
            f"Крупнейшая статья БДР: {_top_str(pnl_top)}.\n"
            f"Крупнейшая выплата ДДС: {_top_str(cf_top)}.\n"
            "Таблицы преднарисованы. ЗАПРЕЩЕНО: добавлять новые статьи, включать ВГО.\n"
            "ОБЯЗАТЕЛЬНО: укажи, что БДР и ДДС нельзя складывать — разные базы."
        )

    limitations: list[str] = []
    seen_lims: set[str] = set()
    for lim in ((pnl_exp["limitations"] if pnl_exp else [])
                + (cf_exp["limitations"] if cf_exp else [])):
        if lim not in seen_lims:
            seen_lims.add(lim)
            limitations.append(lim)
    if scope == "both" and pnl_exp and cf_exp:
        lim = "Показываю отдельно БДР и ДДС — это разные базы, их нельзя складывать."
        if lim not in seen_lims:
            limitations.append(lim)

    facts: dict = {"scope": scope, "as_percent": as_percent}
    if pnl_exp:
        facts.update({
            "pnl_expenses":         pnl_exp["items"],
            "pnl_base_amount":      pnl_exp["base_amount"],
            "pnl_base_amount_rub":  pnl_exp["base_amount_rub"],
            "pnl_excluded":         pnl_exp.get("excluded", []),
            "pnl_overflow_warning": pnl_exp.get("overflow_warning", False),
        })
    if cf_exp:
        facts.update({
            "cf_payments":         cf_exp["items"],
            "cf_base_amount":      cf_exp["base_amount"],
            "cf_base_amount_rub":  cf_exp["base_amount_rub"],
            "cf_excluded":         cf_exp.get("excluded", []),
            "cf_overflow_warning": cf_exp.get("overflow_warning", False),
            "cf_tax_breakdown":    cf_exp.get("tax_breakdown", []),
        })

    result = _ok(
        "expense_analysis",
        facts=facts,
        calculations={},
        tables=tables,
        limitations=limitations,
        guidance=guidance,
    )
    result["prerendered_tables_md"] = prerendered_md
    result["answer_type"]           = "expense_ranking"
    return result


def _handle_calculate_break_even(plan: dict, ctx: dict, history: list[dict]) -> dict:
    scope = (plan.get("entities") or {}).get("scope", "period")

    if scope == "project":
        pk = _pk(ctx)
        if not pk:
            return _no_data("break_even_analysis")
        above, below = [], []
        for name, p in pk.items():
            bev = _kv(p, "break_even_revenue")
            beg = _kv(p, "break_even_gap")
            entry = {
                "name":                   name,
                "revenue_rub":            _kf(p, "revenue"),
                "break_even_revenue":     bev,
                "break_even_revenue_rub": _rub(bev),
                "break_even_gap":         beg,
                "break_even_gap_rub":     _signed(beg),
                "break_even_gap_ratio":   _kf(p, "break_even_gap_ratio"),
                "operating_profit_rub":   _kf(p, "operating_profit"),
            }
            if beg is not None and beg >= 0:
                above.append(entry)
            else:
                below.append(entry)
        above.sort(key=lambda x: x["break_even_gap"] or 0, reverse=True)
        below.sort(key=lambda x: x["break_even_gap"] or 0)

        table = {
            "id":      "project_break_even",
            "title":   "Безубыточность по проектам",
            "headers": ["Проект", "Выручка", "Точка безуб.", "Разрыв", "Покрытие"],
            "rows":    [
                [e["name"], e["revenue_rub"], e["break_even_revenue_rub"],
                 e["break_even_gap_rub"], e["break_even_gap_ratio"]]
                for e in above + below
            ],
        }
        return _ok("break_even_analysis",
            facts={"above_break_even": above, "below_break_even": below},
            calculations={},
            tables=[table],
            limitations=["Расчёт является proxy: расходы не разделены на постоянные и переменные."],
            guidance=(
                "Раздели проекты: выше точки безубыточности (facts.above_break_even) "
                "и ниже (facts.below_break_even). ОБЯЗАТЕЛЬНО укажи: расчёт является proxy."
            ),
        )

    # Period break-even
    sk  = _sk(ctx)
    mk  = _mk(ctx)
    nm  = _month_names(ctx)
    bev = _kv(sk, "break_even_revenue")
    rev = _kv(sk, "revenue")
    beg = _kv(sk, "break_even_gap")
    begrf = _kf(sk, "break_even_gap_ratio")

    if bev is None:
        return _no_data("break_even_analysis")

    shortfall = (-beg if beg is not None and beg < 0 else 0)

    monthly_rows = []
    for mk_key in sorted(mk.keys()):
        m    = mk[mk_key]
        name = (nm.get(mk_key) or mk_key).capitalize()
        monthly_rows.append([
            name,
            _kf(m, "revenue"),
            _kf(m, "break_even_revenue"),
            _kf(m, "break_even_gap"),
            _kf(m, "break_even_gap_ratio"),
        ])

    table = {
        "id":      "monthly_break_even",
        "title":   "Безубыточность по месяцам",
        "headers": ["Месяц", "Выручка", "Точка безуб.", "Разрыв", "Покрытие"],
        "rows":    monthly_rows,
    }

    gap_dir = "ВЫШЕ" if beg is not None and beg >= 0 else "НИЖЕ"
    guidance = (
        f"Proxy-точка безубыточности за период: {_rub(bev)} ₽.\n"
        f"Фактическая выручка: {_rub(rev)} ₽ — {gap_dir} точки безубыточности.\n"
        f"Разрыв: {_signed(beg)} ₽, покрытие: {begrf}.\n"
        + (f"Не хватило до безубыточности: {_rub(shortfall)} ₽.\n" if shortfall > 0 else "")
        + "Покажи таблицу из tables[0] с помесячными данными.\n"
        "ОБЯЗАТЕЛЬНО: укажи, что расчёт является proxy."
    )

    return _ok("break_even_analysis",
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
        calculations={},
        tables=[table],
        limitations=["Расчёт является proxy: расходы не разделены полностью на постоянные и переменные."],
        guidance=guidance,
        source_fields=["break_even_revenue", "break_even_gap", "break_even_gap_ratio"],
    )


def _handle_simulate_revenue_change(plan: dict, ctx: dict, history: list[dict]) -> dict:
    sk     = _sk(ctx)
    period = ctx.get("period") or {}
    params = plan.get("parameters") or {}

    # Cost follow-up: inherit total_revenue_increase from history
    if params.get("inherit_from_history"):
        hist_sc = _get_history_scenario(history)
        if not hist_sc:
            return _clarify(
                "revenue_growth_scenario",
                "Предыдущий сценарий роста выручки не найден. Укажи сумму прироста явно.",
            )
        total_increase = (
            hist_sc.get("total_revenue_increase")
            or hist_sc.get("total_increase")
        )
        add_gp   = (
            hist_sc.get("additional_gross_profit_proxy")
            or hist_sc.get("additional_gp_proxy")
        )
        cur_op   = hist_sc.get("current_operating_profit") or _kv(sk, "operating_profit")
        cost_pct = params.get("additional_cost_percent", 0)
        add_cost = (total_increase or 0) * cost_pct
        net_eff  = ((add_gp or 0) - add_cost) if add_gp is not None else None
        proxy_op = ((cur_op or 0) + net_eff)  if cur_op is not None and net_eff is not None else None

        guidance = (
            f"База расчёта — доп. выручка {_rub(total_increase)} ₽ из предыдущего сценария.\n"
            f"Расходы {cost_pct * 100:.0f}% × {_rub(total_increase)} ₽ = {_rub(add_cost)} ₽.\n"
        )
        if add_gp is not None:
            guidance += f"Доп. валовая прибыль proxy: {_rub(add_gp)} ₽.\n"
            guidance += f"Чистый прирост после расходов: {_signed(net_eff)} ₽.\n"
        if proxy_op is not None:
            guidance += f"Новая операционная прибыль proxy: {_signed(proxy_op)} ₽.\n"
        guidance += "Пометь: proxy-сценарий при прочих равных."

        return _ok("revenue_growth_scenario",
            facts={
                "base_description":         "доп. выручка из предыдущего proxy-сценария",
                "total_revenue_increase":   total_increase,
                "total_revenue_increase_rub": _rub(total_increase),
                "cost_percent":             cost_pct,
                "cost_percent_label":       f"{cost_pct * 100:.0f}%",
            },
            calculations={
                "additional_cost":                add_cost,
                "additional_cost_rub":            _rub(add_cost),
                "additional_gp_proxy":            add_gp,
                "additional_gp_proxy_rub":        _rub(add_gp),
                "net_incremental_effect":         net_eff,
                "net_incremental_effect_rub":     _signed(net_eff),
                "current_operating_profit":       cur_op,
                "current_operating_profit_rub":   _signed(cur_op),
                "proxy_new_operating_profit":     proxy_op,
                "proxy_new_operating_profit_rub": _signed(proxy_op),
            },
            tables=[],
            limitations=[
                "База — доп. выручка из proxy-сценария, не текущая выручка.",
                "Proxy-сценарий при прочих равных.",
            ],
            guidance=guidance,
        )

    # Standard revenue growth
    amount = params.get("amount")
    if not amount:
        return _clarify(
            "revenue_growth_scenario",
            "Сумма прироста выручки не указана. Напиши, например: «на 2 млн».",
        )

    period_basis = params.get("period_basis", "period")
    months_count = len(period.get("months") or []) or 1
    revenue      = _kv(sk, "revenue")
    if not revenue:
        return _no_data("revenue_growth_scenario", "Данные о выручке не найдены.")

    total_increase = amount * months_count if period_basis == "month" else amount
    new_revenue    = revenue + total_increase
    be_revenue     = _kv(sk, "break_even_revenue")
    new_be_gap     = (new_revenue - be_revenue) if be_revenue is not None else None
    gm_ratio       = _kv(sk, "gross_margin")
    cur_op         = _kv(sk, "operating_profit")
    add_gp         = (total_increase * gm_ratio)  if gm_ratio is not None else None

    cost_pct  = params.get("additional_cost_percent")
    add_cost  = (total_increase * cost_pct) if cost_pct else None
    net_eff   = (add_gp - add_cost) if (add_gp is not None and add_cost is not None) else None
    eff_base  = net_eff if net_eff is not None else add_gp
    proxy_op  = (cur_op + eff_base) if cur_op is not None and eff_base is not None else None

    period_str = (
        f"{_rub(amount)} ₽ × {months_count} мес. = {_rub(total_increase)} ₽"
        if period_basis == "month"
        else f"{_rub(total_increase)} ₽ за период"
    )
    guidance = (
        f"Прирост: {period_str}.\n"
        f"Текущая выручка: {_rub(revenue)} ₽ → новая: {_rub(new_revenue)} ₽.\n"
    )
    if new_be_gap is not None:
        guidance += f"Разрыв к точке безуб.: {_signed(new_be_gap)} ₽ ({'ВЫШЕ' if new_be_gap >= 0 else 'НИЖЕ'}).\n"
    if gm_ratio is not None and add_gp is not None:
        guidance += f"Доп. валовая прибыль proxy ({_pct(gm_ratio)} маржа): {_rub(add_gp)} ₽.\n"
    if proxy_op is not None:
        guidance += f"Новая операционная прибыль proxy: {_signed(proxy_op)} ₽.\n"
    guidance += (
        "ОБЯЗАТЕЛЬНО: называй «proxy-сценарий». "
        "Предупреди о возможном росте расходов вместе с выручкой."
    )

    sc_meta = {
        "total_revenue_increase":        total_increase,
        "additional_gross_profit_proxy": add_gp,
        "current_operating_profit":      cur_op,
        "amount":                        amount,
        "period_basis":                  period_basis,
        "months_count":                  months_count,
    }

    return _ok("revenue_growth_scenario",
        facts={
            "amount":                        amount,
            "amount_rub":                    _rub(amount),
            "period_basis":                  period_basis,
            "months_count":                  months_count,
            "current_revenue":               revenue,
            "current_revenue_rub":           _rub(revenue),
            "total_revenue_increase":        total_increase,
            "total_revenue_increase_rub":    _rub(total_increase),
            "gross_margin_ratio":            gm_ratio,
            "gross_margin_pct":              _pct(gm_ratio),
        },
        calculations={
            "new_revenue":                       new_revenue,
            "new_revenue_rub":                   _rub(new_revenue),
            "break_even_revenue":                be_revenue,
            "break_even_revenue_rub":            _rub(be_revenue),
            "new_break_even_gap":                new_be_gap,
            "new_break_even_gap_rub":            _signed(new_be_gap),
            "additional_gp_proxy":               add_gp,
            "additional_gp_proxy_rub":           _rub(add_gp),
            "current_operating_profit":          cur_op,
            "current_operating_profit_rub":      _signed(cur_op),
            "additional_cost":                   add_cost,
            "additional_cost_rub":               _rub(add_cost),
            "net_incremental_effect":            net_eff,
            "net_incremental_effect_rub":        _signed(net_eff),
            "proxy_new_operating_profit":        proxy_op,
            "proxy_new_operating_profit_rub":    _signed(proxy_op),
        },
        tables=[],
        limitations=[
            "Proxy-сценарий при сохранении текущей валовой маржи и без роста операционных расходов.",
            "В реальности вместе с выручкой могут вырасти себестоимость, ФОТ, подрядчики, налоги.",
        ],
        guidance=guidance,
        metadata={"scenario": sc_meta},
        source_fields=["revenue", "gross_margin", "break_even_revenue", "operating_profit"],
    )


def _handle_simulate_payroll_ratio(plan: dict, ctx: dict, history: list[dict]) -> dict:
    sk         = _sk(ctx)
    pnl_totals = (ctx.get("pnl") or {}).get("totals") or {}
    params     = plan.get("parameters") or {}

    revenue = _kv(sk, "revenue")
    if revenue is None:
        try:
            revenue = float(pnl_totals.get("revenue") or 0) or None
        except Exception:
            pass
    if not revenue or revenue <= 0:
        return _no_data("payroll_benchmark", "Данные о выручке не найдены.")

    payroll = _kv(sk, "payroll")
    if payroll is None:
        try:
            payroll = abs(float(pnl_totals.get("payroll") or 0)) or None
        except Exception:
            pass
    if not payroll:
        return _no_data("payroll_benchmark", "Данные о ФОТ не найдены.")
    payroll = abs(payroll)

    ratio = _kv(sk, "payroll_ratio")
    if ratio is None:
        ratio = payroll / revenue

    target_ratio = params.get("target_ratio")
    low_r, high_r = 0.35, 0.40
    low_a  = revenue * low_r
    high_a = revenue * high_r
    excess = payroll - high_a

    custom: dict | None = None
    if target_ratio is not None:
        ca = revenue * target_ratio
        custom = {
            "percent":       target_ratio,
            "percent_label": f"{target_ratio * 100:.0f}%",
            "amount":        ca,
            "amount_rub":    _rub(ca),
            "excess":        payroll - ca,
            "excess_rub":    _signed(payroll - ca),
        }

    guidance = (
        f"ФОТ фактический: {_rub(payroll)} ₽ ({_pct(ratio)} от выручки {_rub(revenue)} ₽).\n"
        f"Ориентир: {low_r * 100:.0f}–{high_r * 100:.0f}% = {_rub(low_a)}–{_rub(high_a)} ₽.\n"
        + (f"Превышение ориентира: {_signed(excess)} ₽.\n" if excess > 0 else "ФОТ в пределах ориентира.\n")
        + ("" if not custom else
           f"При {custom['percent_label']} от выручки: целевой ФОТ = {custom['amount_rub']} ₽, "
           f"разница = {custom['excess_rub']} ₽.\n")
        + "ЯВНО отметь: «Это управленческий ориентир, не норматив»."
    )

    return _ok("payroll_benchmark",
        facts={
            "revenue":                   revenue,
            "revenue_rub":               _rub(revenue),
            "current_payroll":           payroll,
            "current_payroll_rub":       _rub(payroll),
            "current_payroll_ratio":     ratio,
            "current_payroll_ratio_pct": _pct(ratio),
        },
        calculations={
            "benchmark_range_pct":         f"{low_r * 100:.0f}–{high_r * 100:.0f}%",
            "benchmark_low_amount":        low_a,
            "benchmark_low_amount_rub":    _rub(low_a),
            "benchmark_high_amount":       high_a,
            "benchmark_high_amount_rub":   _rub(high_a),
            "excess_over_high":            excess,
            "excess_over_high_rub":        _signed(excess),
            "custom":                      custom,
        },
        tables=[],
        limitations=[
            "Ориентир 35–40% — управленческий, не отраслевой норматив.",
            "Для медиабизнеса допустимый уровень ФОТ может отличаться.",
        ],
        guidance=guidance,
        source_fields=["revenue", "payroll", "payroll_ratio"],
    )


def _handle_calculate_year_end_target(plan: dict, ctx: dict, history: list[dict]) -> dict:
    sk           = _sk(ctx)
    period       = ctx.get("period") or {}
    months_count = len(period.get("months") or []) or 3
    period_label = period.get("period_label") or f"{months_count} мес."
    remaining    = 12 - months_count

    if remaining <= 0:
        return _no_data("year_end_break_even",
                        "В отчёте охвачен весь год — расчёт до конца года неприменим.")

    op_profit  = _kv(sk, "operating_profit")
    be_revenue = _kv(sk, "break_even_revenue")
    gm_ratio   = _kv(sk, "gross_margin")
    revenue    = _kv(sk, "revenue")

    if None in (op_profit, be_revenue, gm_ratio, revenue):
        return _no_data("year_end_break_even", "Недостаточно данных для расчёта.")

    monthly_be   = be_revenue / months_count
    current_mon  = revenue / months_count
    to_recover   = -op_profit             # positive = current loss
    mon_profit_n = to_recover / remaining if to_recover > 0 else 0
    add_mon_rev  = mon_profit_n / gm_ratio if gm_ratio > 0 else 0
    req_mon_rev  = monthly_be + add_mon_rev
    monthly_gap  = req_mon_rev - current_mon

    guidance = (
        f"Отчётный период: {period_label} ({months_count} мес.), осталось {remaining} мес.\n"
        f"Текущий результат: {_signed(op_profit)} ₽.\n"
        + (f"Нужно отыграть: {_rub(to_recover)} ₽ за {remaining} мес.\n"
           if to_recover > 0 else "Убытка нет — достаточно поддерживать безубыточность.\n")
        + f"Нужная выручка в месяц: {_rub(req_mon_rev)} ₽.\n"
        f"Текущая средняя выручка в месяц: {_rub(current_mon)} ₽.\n"
        f"Доп. прирост в месяц: {_signed(monthly_gap)} ₽.\n"
        "ОБЯЗАТЕЛЬНО: это proxy-расчёт при постоянной валовой марже."
    )

    return _ok("year_end_break_even",
        facts={
            "period_label":               period_label,
            "months_in_report":           months_count,
            "remaining_months":           remaining,
            "current_op_profit":          op_profit,
            "current_op_profit_rub":      _signed(op_profit),
            "current_monthly_revenue":    current_mon,
            "current_monthly_rev_rub":    _rub(current_mon),
            "monthly_break_even":         monthly_be,
            "monthly_break_even_rub":     _rub(monthly_be),
            "gross_margin_ratio":         gm_ratio,
            "gross_margin_pct":           _pct(gm_ratio),
        },
        calculations={
            "profit_to_recover":          to_recover,
            "profit_to_recover_rub":      _rub(to_recover),
            "monthly_profit_needed":      mon_profit_n,
            "monthly_profit_needed_rub":  _rub(mon_profit_n),
            "additional_monthly_revenue": add_mon_rev,
            "additional_monthly_rev_rub": _rub(add_mon_rev),
            "required_monthly_revenue":   req_mon_rev,
            "required_monthly_rev_rub":   _rub(req_mon_rev),
            "monthly_gap":                monthly_gap,
            "monthly_gap_rub":            _signed(monthly_gap),
        },
        tables=[],
        limitations=[
            "Proxy-расчёт: постоянная валовая маржа в оставшихся месяцах.",
            "Реальный результат зависит от сезонности и изменений расходов.",
        ],
        guidance=guidance,
        source_fields=["operating_profit", "break_even_revenue", "gross_margin", "revenue"],
    )


def _handle_annualize_result(plan: dict, ctx: dict, history: list[dict]) -> dict:
    sk           = _sk(ctx)
    period       = ctx.get("period") or {}
    months_count = len(period.get("months") or []) or 3
    period_label = period.get("period_label") or f"{months_count} мес."

    op_profit = _kv(sk, "operating_profit")
    revenue   = _kv(sk, "revenue")
    op_cf     = _kv(sk, "operating_cashflow")

    if op_profit is None and revenue is None:
        return _no_data("annual_forecast", "Данные для прогноза не найдены.")

    mult    = 12 / months_count
    ann_op  = (op_profit * mult) if op_profit is not None else None
    ann_rev = (revenue   * mult) if revenue   is not None else None
    ann_cf  = (op_cf     * mult) if op_cf     is not None else None

    guidance = (
        f"Масштабирование: {months_count} мес. → 12 мес. (×{mult:.1f}).\n"
        f"Proxy-прибыль от основной деятельности за 12 мес.: {_signed(ann_op)} ₽.\n"
        f"Proxy-выручка за 12 мес.: {_rub(ann_rev)} ₽.\n"
        "ОБЯЗАТЕЛЬНО: «proxy-прибыль от основной деятельности», НЕ «чистая прибыль».\n"
        "Укажи ограничение: сезонность и изменения расходов не учтены."
    )

    return _ok("annual_forecast",
        facts={
            "period_label":         period_label,
            "months_count":         months_count,
            "multiplier":           mult,
            "quarterly_op_profit":  op_profit,
            "quarterly_op_profit_rub": _signed(op_profit),
            "quarterly_revenue":    revenue,
            "quarterly_revenue_rub":_rub(revenue),
        },
        calculations={
            "annual_op_profit_proxy":     ann_op,
            "annual_op_profit_proxy_rub": _signed(ann_op),
            "annual_revenue_proxy":       ann_rev,
            "annual_revenue_proxy_rub":   _rub(ann_rev),
            "annual_op_cf_proxy":         ann_cf,
            "annual_op_cf_proxy_rub":     _signed(ann_cf),
        },
        tables=[],
        limitations=[
            "Масштабирование — грубый proxy. Сезонность и изменения расходов не учтены.",
            "Результат — «proxy-прибыль от основной деятельности», НЕ чистая прибыль.",
        ],
        guidance=guidance,
        source_fields=["operating_profit", "revenue", "operating_cashflow"],
    )


def _handle_explain_profit_vs_cash(plan: dict, ctx: dict, history: list[dict]) -> dict:
    sk        = _sk(ctx)
    cf_totals = (ctx.get("cashflow") or {}).get("totals") or {}

    op_profit = _kv(sk, "operating_profit")
    op_cf     = _kv(sk, "operating_cashflow")
    net_cf    = _kv(sk, "net_cashflow")

    fin_cf: float | None = None
    try:
        v = cf_totals.get("financial_cashflow")
        if v is not None:
            fin_cf = float(v)
    except Exception:
        pass

    if op_profit is None and op_cf is None:
        return _no_data("profit_vs_cash")

    gap_kpi = _kv(sk, "profit_to_operating_cashflow_gap")
    gap = gap_kpi if gap_kpi is not None else (
        (op_cf - op_profit) if op_profit is not None and op_cf is not None else None
    )

    guidance = (
        f"Операционная прибыль (БДР): {_signed(op_profit)} ₽.\n"
        f"Операционный денежный поток (ДДС): {_signed(op_cf)} ₽.\n"
        f"Разрыв: {_signed(gap)} ₽.\n"
        f"Финансовый поток: {_signed(fin_cf)} ₽ — займы, ВГО, дивиденды.\n"
        f"Чистый поток: {_signed(net_cf)} ₽.\n"
        "Объясни: разрыв — нормальное явление (начисленная выручка ≠ полученные деньги)."
    )

    return _ok("profit_vs_cash",
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
        tables=[],
        limitations=[
            "БДР — начисления (метод начисления). ДДС — фактическое движение денег.",
            "Разрыв — нормальное явление, не обязательно проблема.",
        ],
        guidance=guidance,
        source_fields=["operating_profit", "operating_cashflow", "net_cashflow"],
    )


def _handle_explain_data_limitations(plan: dict, ctx: dict, history: list[dict]) -> dict:
    data_type = (plan.get("entities") or {}).get("data_type", "unknown")
    _EXPLANATIONS = {
        "project_payroll": (
            "В загруженном управленческом отчёте ФОТ отражён общей суммой по компании (БДР). "
            "Разбивка ФОТ по проектам в этих данных отсутствует. "
            "Для анализа ФОТ по направлениям нужны данные учёта по центрам затрат."
        ),
        "monthly_project": (
            "Детализация проектов по месяцам в загруженных данных отсутствует."
        ),
        "unknown": (
            "Запрошенные данные отсутствуют в загруженном управленческом отчёте."
        ),
    }
    msg = _EXPLANATIONS.get(data_type, _EXPLANATIONS["unknown"])

    return {
        "status":          "no_data",
        "intent":          "data_limitations",
        "facts":           {"data_type": data_type, "explanation": msg},
        "calculations":    {},
        "tables":          [],
        "limitations":     [msg],
        "answer_guidance": msg,
        "source_fields":   [],
        "metadata":        {},
    }


def _handle_general(plan: dict, ctx: dict, history: list[dict]) -> dict:
    """General management explanation — signals answer_composer to use full context."""
    return {
        "status":          "general",
        "intent":          "general_management_explanation",
        "facts":           {},
        "calculations":    {},
        "tables":          [],
        "limitations":     [],
        "answer_guidance": "Используй полный финансовый контекст для ответа.",
        "source_fields":   [],
        "metadata":        {},
    }


# ─── Dispatcher ───────────────────────────────────────────────────────────────

_OP_HANDLERS: dict = {
    "compare_months":                 _handle_compare_months,
    "rank_projects":                  _handle_rank_projects,
    "rank_expenses":                  _handle_rank_expenses,
    "calculate_break_even":           _handle_calculate_break_even,
    "calculate_gap_to_break_even":    _handle_calculate_break_even,
    "simulate_revenue_change":        _handle_simulate_revenue_change,
    "simulate_cost_change":           _handle_simulate_revenue_change,
    "simulate_payroll_ratio":         _handle_simulate_payroll_ratio,
    "calculate_year_end_target":      _handle_calculate_year_end_target,
    "annualize_result":               _handle_annualize_result,
    "explain_profit_vs_cash":         _handle_explain_profit_vs_cash,
    "explain_data_limitations":       _handle_explain_data_limitations,
    "general_management_explanation": _handle_general,
}


def execute_query_plan(
    query_plan: dict,
    analysis_context: dict,
    history: list[dict] | None = None,
) -> dict:
    """
    Execute operations from query_plan, return answer_payload.

    Returns:
        {status, intent, facts, calculations, tables,
         limitations, answer_guidance, source_fields, metadata}
    """
    history    = history or []
    operations = query_plan.get("operations") or []
    intent     = query_plan.get("intent") or "general_management_explanation"

    if not operations:
        return _no_data(intent, "Операции не определены в плане.")

    primary_op = operations[0]
    handler    = _OP_HANDLERS.get(primary_op)
    if not handler:
        return _no_data(intent, f"Операция «{primary_op}» не поддерживается.")

    payload = handler(query_plan, analysis_context, history)

    # Merge additional operations (compound plans)
    seen = {primary_op}
    for op in operations[1:]:
        if op in seen:
            continue
        seen.add(op)
        h = _OP_HANDLERS.get(op)
        if h:
            extra = h(query_plan, analysis_context, history)
            if extra.get("status") == "success":
                payload["facts"].update(extra.get("facts") or {})
                payload["calculations"].update(extra.get("calculations") or {})
                payload["tables"].extend(extra.get("tables") or [])
                for lim in (extra.get("limitations") or []):
                    if lim not in payload.get("limitations", []):
                        payload["limitations"].append(lim)

    return payload
