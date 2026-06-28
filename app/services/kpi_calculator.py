"""
KPI calculator for the owner's financial dashboard.

All arithmetic is pure Python — no LLM involved in the calculation step.
"""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def safe_divide(numerator, denominator) -> float | None:
    """Return numerator / denominator, or None when either is None or denominator is 0."""
    if numerator is None or denominator is None:
        return None
    try:
        d = float(denominator)
        if d == 0.0:
            return None
        return float(numerator) / d
    except (TypeError, ValueError):
        return None


def _pct_str(p: float, decimals: int = 1) -> str:
    """Format percentage with Russian decimal comma, e.g. '73,8%'."""
    return f"{p:.{decimals}f}".replace('.', ',') + '%'


def _num(v: float, spec: str = '.1f') -> str:
    """Format a float with given spec, using Russian decimal comma."""
    return format(v, spec).replace('.', ',')


def _months_str(v: float) -> str:
    return _num(v, '.2f') + ' мес.'


def format_kpi_value(value, unit: str) -> str:
    """Human-readable KPI value string."""
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if unit == "percent":
        return _pct_str(v * 100)
    if unit == "months":
        return _months_str(v)
    if unit == "ratio":
        return _num(v, '.2f')
    # default: "rub"
    sign = "-" if v < 0 else ""
    return f"{sign}{abs(v):,.0f}".replace(",", " ")  # narrow no-break space


def _fmt_rub(v: float | None) -> str:
    return format_kpi_value(v, "rub")


def _fmt_pct(v: float | None) -> str:
    return format_kpi_value(v, "percent")


def _pct(v: float | None) -> str:
    """Compact percentage string for interpretation text."""
    if v is None:
        return "—"
    return _pct_str(v * 100)


def _rub(v: float | None) -> str:
    """Compact ruble string for interpretation text."""
    if v is None:
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}{abs(v):,.0f}".replace(",", " ")  # narrow no-break space


# ─── Interpretation helpers ───────────────────────────────────────────────────

def _interp_gross_margin(v: float | None) -> str:
    if v is None:
        return "Недостаточно данных для расчёта."
    p = v * 100
    suffix = f"После себестоимости остаётся {_pct_str(p)} выручки."
    if p >= 70:
        return f"{suffix} Валовая экономика сильная."
    if p >= 40:
        return f"{suffix} Валовая маржа в норме."
    if p >= 20:
        return f"{suffix} Валовая маржа умеренная."
    return f"{suffix} Валовая маржа низкая — себестоимость ест большую часть выручки."


def _interp_operating_margin(v: float | None) -> str:
    if v is None:
        return "Недостаточно данных для расчёта."
    p = v * 100
    if p > 10:
        return f"Операционная рентабельность {_pct_str(p)} — основная деятельность прибыльна."
    if p >= 0:
        return f"Операционная рентабельность {_pct_str(p)} — бизнес на грани безубыточности."
    return "Основная деятельность убыточна: расходы выше валовой прибыли."


def _interp_payroll_ratio(v: float | None) -> str:
    if v is None:
        return "Данные о ФОТ не найдены."
    p = v * 100
    if p >= 70:
        return f"ФОТ занимает {_pct_str(p)} выручки — критическая нагрузка на бизнес."
    if p >= 50:
        return f"ФОТ занимает {_pct_str(p)} выручки — это ключевая нагрузка на бизнес."
    if p >= 30:
        return f"ФОТ занимает {_pct_str(p)} выручки — умеренная доля."
    return f"ФОТ занимает {_pct_str(p)} выручки — нагрузка в норме."


def _interp_rent_ratio(v: float | None) -> str:
    if v is None:
        return "Данные об аренде не найдены."
    p = v * 100
    if p >= 15:
        return f"Аренда занимает {_pct_str(p)} выручки — высокая фиксированная нагрузка."
    if p >= 8:
        return f"Аренда занимает {_pct_str(p)} выручки — заметная, но управляемая статья."
    return f"Аренда занимает {_pct_str(p)} выручки — умеренная доля."


def _interp_taxes_ratio(v: float | None) -> str:
    if v is None:
        return "Данные о налогах не найдены."
    p = v * 100
    return f"Налоговая нагрузка составляет {_pct_str(p)} выручки."


def _interp_opex_ratio(v: float | None) -> str:
    if v is None:
        return "Недостаточно данных для расчёта."
    p = v * 100
    if p >= 100:
        return f"Расходы {_pct_str(p)} выручки — операционная деятельность убыточна."
    if p >= 85:
        return f"Расходы составляют {_pct_str(p)} выручки — очень высокая нагрузка."
    if p >= 70:
        return f"Расходы составляют {_pct_str(p)} выручки — высокая нагрузка."
    return f"Расходы составляют {_pct_str(p)} выручки — умеренный уровень."


def _interp_operating_cashflow(v: float | None) -> str:
    if v is None:
        return "Данные операционного потока не найдены."
    if v > 0:
        return f"Операционная деятельность генерирует деньги: {_rub(v)} ₽."
    if v < 0:
        return "Операционная деятельность сжигает деньги."
    return "Операционный денежный поток равен нулю."


def _interp_net_cashflow(v: float | None) -> str:
    if v is None:
        return "Данные чистого потока не найдены."
    if v > 0:
        return f"Чистый денежный поток положительный: +{_rub(v)} ₽ за период."
    if v < 0:
        return f"Чистый денежный поток отрицательный: {_rub(v)} ₽ за период."
    return "Чистый денежный поток равен нулю."


def _interp_profit_to_op_cf_gap(gap: float | None, op_profit: float | None,
                                 op_cf: float | None) -> str:
    if gap is None:
        return "Недостаточно данных."
    if op_profit is None or op_cf is None:
        return "Недостаточно данных."
    if gap < 0:
        diff = abs(gap)
        if op_profit < 0 and op_cf < 0:
            return (f"Операционный денежный поток лучше прибыли на {_rub(diff)} ₽, "
                    "но оба показателя отрицательные: бизнес убыточен по БДР "
                    "и одновременно сжигает деньги операционно.")
        return (f"Операционный поток лучше учётной прибыли на {_rub(diff)} ₽: "
                "неденежные расходы (амортизация и др.) ухудшают прибыль относительно кассы.")
    if gap > 0:
        return (f"Прибыль лучше денежного потока на {_rub(gap)} ₽: "
                "часть выручки ещё не собрана деньгами или есть высокий расход НДС.")
    return "Прибыль и операционный денежный поток совпадают."


def _interp_profit_to_net_cf_gap(gap: float | None, op_profit: float | None,
                                  net_cf: float | None, fin_cf: float | None) -> str:
    if gap is None:
        return "Недостаточно данных."
    # Profit negative, net cashflow at zero or negative, but financial flow compensated
    if (op_profit is not None and op_profit < 0 and
            net_cf is not None and net_cf <= 0 and
            fin_cf is not None and fin_cf > 0):
        return (f"Финансовый поток {_rub(fin_cf)} ₽ почти перекрыл операционный "
                "денежный минус. Без финансирования чистый поток был бы существенно хуже.")
    # Profit negative, no financial flow data — neutral
    if op_profit is not None and op_profit < 0 and fin_cf is None:
        return ("Чистый денежный поток отличается от прибыли: на результат по деньгам "
                "влияют не только операционные начисления, но и денежные движения.")
    if gap < 0:
        return (f"Чистый денежный поток лучше операционной прибыли на {_rub(abs(gap))} ₽: "
                "финансирование или инвестиции компенсировали операционный убыток.")
    if gap > 0:
        return (f"Прибыль лучше чистого денежного потока на {_rub(gap)} ₽: "
                "финансовые или инвестиционные оттоки ухудшили кассовый результат.")
    return "Операционная прибыль и чистый денежный поток совпадают."


def _interp_profit_to_cash_conversion(v: float | None, op_profit: float | None,
                                       op_cf: float | None) -> str:
    if v is None:
        return "Недостаточно данных."
    if op_profit is not None and op_profit < 0 and op_cf is not None and op_cf < 0:
        return ("При отрицательной прибыли показатель конверсии читается ограниченно: "
                "бизнес одновременно убыточен и имеет отрицательный операционный денежный поток.")
    if op_profit is not None and op_profit > 0 and op_cf is not None and op_cf < 0:
        return "Бизнес прибыльный, но деньги не собираются — риск кассового разрыва."
    pct = v * 100
    if pct >= 90:
        return f"Высокая конверсия прибыли в денежный поток: {_pct_str(pct, 0)}."
    if pct >= 70:
        return f"Большая часть прибыли конвертируется в денежный поток: {_pct_str(pct, 0)}."
    return f"Конверсия прибыли в денежный поток: {_pct_str(pct, 0)} — ниже нормы."


def _interp_net_cashflow_ratio(v: float | None) -> str:
    if v is None:
        return "Недостаточно данных."
    p = v * 100
    if p >= 10:
        return (f"С каждых 100 ₽ поступлений остаётся {_num(p)} ₽ чистого потока — "
                "хороший результат.")
    if p >= 0:
        return (f"С каждых 100 ₽ поступлений остаётся {_num(p)} ₽ чистого потока — "
                "минимальный запас.")
    return f"На каждые 100 ₽ поступлений тратится на {_num(abs(p))} ₽ больше — дефицит."


def _interp_cash_reserve(v: float | None) -> str:
    if v is None:
        return "Недостаточно данных для расчёта денежного запаса."
    if v >= 3:
        return (f"Денежный запас покрывает {_num(v)} месяца операционных выплат — "
                "хороший буфер.")
    if v >= 1:
        return (f"Денежный запас покрывает {_num(v)} мес. операционных выплат — "
                "минимально приемлемый уровень.")
    if v >= 0.5:
        return "Денежный запас покрывает менее одного месяца операционных выплат."
    return ("Денежный запас покрывает менее 0,1 месяца операционных выплат." if v < 0.1 else
            "Денежный запас покрывает менее 0,5 месяца операционных выплат.")


# ─── Break-even helpers ───────────────────────────────────────────────────────

def _calc_break_even(revenue, gross_profit, operating_exp):
    """
    Calculate break-even revenue, gap, and coverage ratio.
    Returns (be_revenue, be_gap, be_ratio, warning_or_None).
    warning_or_None is set when calculation is not possible.
    """
    try:
        rev  = float(revenue)       if revenue       is not None else None
        gp   = float(gross_profit)  if gross_profit  is not None else None
        opex = float(operating_exp) if operating_exp is not None else None
    except (TypeError, ValueError):
        return None, None, None, None

    if rev is None or gp is None or opex is None:
        return None, None, None, None

    if rev <= 0:
        return (None, None, None,
                "Точка безубыточности не рассчитана: выручка отсутствует или отрицательная.")

    gm_ratio = gp / rev
    if gm_ratio <= 0:
        return (None, None, None,
                "Точка безубыточности не рассчитана: "
                "валовая маржа отсутствует или отрицательная.")

    be_revenue = abs(opex) / gm_ratio
    be_gap     = rev - be_revenue
    be_ratio   = safe_divide(rev, be_revenue)
    return be_revenue, be_gap, be_ratio, None


def _interp_break_even_revenue(bev: float | None, period_label: str = "") -> str:
    if bev is None:
        return ("Точка безубыточности не рассчитана: "
                "валовая маржа отсутствует или отрицательная.")
    bev_mln = bev / 1_000_000
    pl = f" {period_label}" if period_label else ""
    return f"Расчётная точка безубыточности{pl} — около {_num(bev_mln, '.2f')} млн ₽ выручки."


def _interp_break_even_revenue_project(bev: float | None) -> str:
    if bev is None:
        return ("Проектная точка безубыточности не рассчитана: "
                "валовая маржа отсутствует или отрицательная.")
    bev_mln = bev / 1_000_000
    return (f"Проектная точка безубыточности рассчитана как proxy "
            f"на основе распределённых расходов проекта: около {_num(bev_mln, '.2f')} млн ₽ выручки.")


def _interp_break_even_gap(gap: float | None) -> str:
    if gap is None:
        return "Не рассчитано."
    if gap < 0:
        return (f"Фактическая выручка ниже точки безубыточности "
                f"примерно на {_rub(abs(gap))} ₽.")
    if gap > 0:
        return f"Фактическая выручка выше точки безубыточности на {_rub(gap)} ₽."
    return "Фактическая выручка точно совпадает с точкой безубыточности."


def _interp_break_even_gap_ratio(ratio: float | None) -> str:
    if ratio is None:
        return "Не рассчитано."
    p = ratio * 100
    if p >= 100:
        return (f"Бизнес покрыл {_pct_str(p, 1)} точки безубыточности — "
                "достигнута операционная безубыточность.")
    return f"Бизнес покрыл около {_pct_str(p, 1)} выручки, необходимой для операционного нуля."


# ─── Contribution interpretation ─────────────────────────────────────────────

def _interp_contribution(proj_profit: float | None, total_profit: float | None,
                          ratio: float | None) -> str:
    if proj_profit is None or total_profit is None:
        return "Недостаточно данных."
    if total_profit < 0:
        if proj_profit > 0:
            return (f"Проект прибыльный (+{_rub(proj_profit)} ₽), "
                    "снижает общий убыток компании.")
        pct = abs(safe_divide(proj_profit, total_profit) or 0) * 100
        return (f"Проект убыточный ({_rub(proj_profit)} ₽), его убыток эквивалентен "
                f"{_pct_str(pct, 0)} итогового убытка компании до компенсации "
                "прибыльными направлениями.")
    else:
        if proj_profit > 0:
            pct = (ratio or 0) * 100
            return f"Проект прибыльный, даёт {_pct_str(pct, 0)} от общей прибыли."
        return (f"Проект убыточный ({_rub(proj_profit)} ₽), "
                "снижает общую прибыль компании.")


# ─── KPI entry builder ────────────────────────────────────────────────────────

def _kpi(label: str, value, unit: str, source: str, interpretation: str) -> dict:
    return {
        "label":          label,
        "value":          value,
        "formatted":      format_kpi_value(value, unit),
        "unit":           unit,
        "source":         source,
        "interpretation": interpretation,
    }


# ─── Main calculators ─────────────────────────────────────────────────────────

def calculate_owner_kpi(normalized_data: dict) -> dict:
    """
    Compute the full owner KPI report from normalized financial data.
    Returns a dict with summary_kpi, monthly_kpi, project_kpi, warnings.
    """
    result: dict = {
        "status":       "success",
        "period_label": normalized_data.get("period", {}).get("period_label") or "—",
        "summary_kpi":  {},
        "monthly_kpi":  {},
        "project_kpi":  {},
        "warnings":     [],
    }

    detected = normalized_data.get("detected_reports") or []
    pnl      = normalized_data.get("pnl") or {}
    cashflow = normalized_data.get("cashflow") or {}
    period   = normalized_data.get("period") or {}

    has_pnl      = "pnl" in detected
    has_cashflow = "cashflow" in detected
    months_count = len(period.get("months") or [])

    if not has_pnl:
        result["warnings"].append("KPI по прибыли и марже не рассчитаны: БДР не найден.")
    if not has_cashflow:
        result["warnings"].append("KPI по денежному потоку не рассчитаны: ДДС не найден.")

    # ── Source values ──────────────────────────────────────────────────────────
    revenue        = pnl.get("revenue")           if has_pnl else None
    gross_profit   = pnl.get("gross_profit")       if has_pnl else None
    operating_exp  = pnl.get("operating_expenses") if has_pnl else None
    payroll        = pnl.get("payroll")            if has_pnl else None
    rent           = pnl.get("rent")               if has_pnl else None
    taxes          = pnl.get("taxes")              if has_pnl else None
    op_profit      = pnl.get("operating_profit")   if has_pnl else None
    net_profit_val = (pnl.get("net_profit") or pnl.get("net_profit_proxy")) \
                     if has_pnl else None

    op_cf    = cashflow.get("operating_cashflow") if has_cashflow else None
    fin_cf   = cashflow.get("financial_cashflow") if has_cashflow else None
    net_cf   = cashflow.get("net_cashflow")       if has_cashflow else None
    op_in    = cashflow.get("operating_inflows")  if has_cashflow else None
    op_out   = cashflow.get("operating_outflows") if has_cashflow else None
    cash_end = cashflow.get("cash_end")           if has_cashflow else None

    # ── Ratios ─────────────────────────────────────────────────────────────────
    gross_margin  = safe_divide(gross_profit, revenue)
    opex_ratio    = safe_divide(operating_exp, revenue)
    payroll_ratio = safe_divide(payroll, revenue)
    rent_ratio    = safe_divide(rent, revenue)
    taxes_ratio   = safe_divide(taxes, revenue)
    op_margin     = safe_divide(op_profit, revenue)

    # ── Break-even (proxy) ─────────────────────────────────────────────────────
    _pl = period.get("period_label") or ""
    be_revenue, be_gap, be_ratio, be_warning = _calc_break_even(
        revenue, gross_profit, operating_exp
    )
    if be_warning:
        result["warnings"].append(be_warning)

    pt_op_cf_gap  = (op_profit - op_cf) \
                    if (op_profit is not None and op_cf is not None) else None
    pt_net_cf_gap = (op_profit - net_cf) \
                    if (op_profit is not None and net_cf is not None) else None
    pt_cash_conv  = safe_divide(op_cf, op_profit)
    net_cf_ratio  = safe_divide(net_cf, op_in)

    # cash_reserve_months = cash_end / (|op_out| / months_count)
    if cash_end is not None and op_out is not None and months_count > 0:
        monthly_burn = abs(op_out) / months_count
        cash_reserve = safe_divide(cash_end, monthly_burn)
    else:
        cash_reserve = None

    # ── Summary KPI dict ──────────────────────────────────────────────────────
    kpi = result["summary_kpi"]

    kpi["revenue"] = _kpi(
        "Выручка", revenue, "rub", "БДР",
        f"Общая выручка за период: {_rub(revenue)} ₽."
        if revenue is not None else "Нет данных.",
    )
    kpi["gross_profit"] = _kpi(
        "Валовая прибыль", gross_profit, "rub", "БДР",
        f"Валовая прибыль: {_rub(gross_profit)} ₽."
        if gross_profit is not None else "Нет данных.",
    )
    kpi["gross_margin"] = _kpi(
        "Валовая маржа", gross_margin, "percent", "БДР",
        _interp_gross_margin(gross_margin),
    )
    kpi["operating_expenses"] = _kpi(
        "Операционные расходы", operating_exp, "rub", "БДР",
        f"Суммарные расходы на деятельность: {_rub(operating_exp)} ₽."
        if operating_exp is not None else "Нет данных.",
    )
    kpi["operating_expense_ratio"] = _kpi(
        "Расходы / выручка", opex_ratio, "percent", "БДР",
        _interp_opex_ratio(opex_ratio),
    )
    kpi["payroll_ratio"] = _kpi(
        "ФОТ / выручка", payroll_ratio, "percent", "БДР",
        _interp_payroll_ratio(payroll_ratio),
    )
    kpi["rent_ratio"] = _kpi(
        "Аренда / выручка", rent_ratio, "percent", "БДР",
        _interp_rent_ratio(rent_ratio),
    )
    kpi["taxes_ratio"] = _kpi(
        "Налоги / выручка", taxes_ratio, "percent", "БДР",
        _interp_taxes_ratio(taxes_ratio),
    )
    kpi["operating_profit"] = _kpi(
        "Прибыль от основной деятельности", op_profit, "rub", "БДР",
        f"Операционная прибыль за период: {_rub(op_profit)} ₽."
        if op_profit is not None else "Нет данных.",
    )
    kpi["operating_margin"] = _kpi(
        "Операционная рентабельность", op_margin, "percent", "БДР",
        _interp_operating_margin(op_margin),
    )
    kpi["break_even_revenue"] = _kpi(
        "Точка безубыточности по выручке", be_revenue, "rub", "БДР",
        _interp_break_even_revenue(be_revenue, _pl),
    )
    kpi["break_even_gap"] = _kpi(
        "Отклонение от точки безубыточности", be_gap, "rub", "БДР",
        _interp_break_even_gap(be_gap),
    )
    kpi["break_even_gap_ratio"] = _kpi(
        "Покрытие точки безубыточности", be_ratio, "percent", "БДР",
        _interp_break_even_gap_ratio(be_ratio),
    )
    kpi["net_profit_or_proxy"] = _kpi(
        "Чистая прибыль" + (" (proxy)" if pnl.get("uses_net_profit_proxy") else ""),
        net_profit_val, "rub", "БДР",
        f"{'Proxy: ' if pnl.get('uses_net_profit_proxy') else ''}"
        f"чистая прибыль за период: {_rub(net_profit_val)} ₽."
        if net_profit_val is not None else "Нет данных.",
    )
    kpi["operating_cashflow"] = _kpi(
        "Операционный денежный поток", op_cf, "rub", "ДДС",
        _interp_operating_cashflow(op_cf),
    )
    kpi["net_cashflow"] = _kpi(
        "Чистый денежный поток", net_cf, "rub", "ДДС",
        _interp_net_cashflow(net_cf),
    )
    kpi["profit_to_operating_cashflow_gap"] = _kpi(
        "Разрыв: прибыль vs опер. поток", pt_op_cf_gap, "rub", "БДР+ДДС",
        _interp_profit_to_op_cf_gap(pt_op_cf_gap, op_profit, op_cf),
    )
    kpi["profit_to_net_cashflow_gap"] = _kpi(
        "Разрыв: прибыль vs чистый поток", pt_net_cf_gap, "rub", "БДР+ДДС",
        _interp_profit_to_net_cf_gap(pt_net_cf_gap, op_profit, net_cf, fin_cf),
    )
    kpi["profit_to_cash_conversion"] = _kpi(
        "Конверсия прибыли в деньги", pt_cash_conv, "ratio", "БДР+ДДС",
        _interp_profit_to_cash_conversion(pt_cash_conv, op_profit, op_cf),
    )
    kpi["net_cashflow_ratio"] = _kpi(
        "Чистый поток / поступления", net_cf_ratio, "percent", "ДДС",
        _interp_net_cashflow_ratio(net_cf_ratio),
    )
    kpi["cash_reserve_months"] = _kpi(
        "Денежный запас (мес.)", cash_reserve, "months", "ДДС",
        _interp_cash_reserve(cash_reserve),
    )

    # ── Monthly and project KPI ────────────────────────────────────────────────
    result["monthly_kpi"] = calculate_monthly_kpi(normalized_data)
    result["project_kpi"] = calculate_project_kpi(normalized_data)

    return result


def calculate_monthly_kpi(normalized_data: dict) -> dict:
    """
    Compute per-month KPI rows.
    Returns {month_key: {field: kpi_entry, ...}, ...}
    """
    pnl_monthly = (normalized_data.get("pnl") or {}).get("monthly") or {}
    cf_monthly  = (normalized_data.get("cashflow") or {}).get("monthly") or {}
    period      = normalized_data.get("period") or {}
    months      = period.get("months") or []
    names       = period.get("month_names") or []
    name_map    = dict(zip(months, names))

    if not pnl_monthly and not cf_monthly:
        return {}

    result: dict = {}
    for mk in sorted(set(list(pnl_monthly.keys()) + list(cf_monthly.keys()))):
        pm = pnl_monthly.get(mk) or {}
        cm = cf_monthly.get(mk) or {}
        month_name = name_map.get(mk, mk).capitalize()

        revenue   = pm.get("revenue")
        gp        = pm.get("gross_profit")
        opex_m    = pm.get("operating_expenses")
        op_profit = pm.get("operating_profit")
        op_cf     = cm.get("operating_cashflow")
        net_cf    = cm.get("net_cashflow")

        gm  = safe_divide(gp, revenue)
        om  = safe_divide(op_profit, revenue)
        gap = (op_profit - op_cf) \
              if (op_profit is not None and op_cf is not None) else None

        be_rev_m, be_gap_m, be_ratio_m, _ = _calc_break_even(revenue, gp, opex_m)

        result[mk] = {
            "month_name":       month_name,
            "revenue":          _kpi("Выручка", revenue, "rub", "БДР",
                                     f"Выручка за {month_name}: {_rub(revenue)} ₽."),
            "gross_margin":     _kpi("Валовая маржа", gm, "percent", "БДР",
                                     _interp_gross_margin(gm)),
            "operating_profit": _kpi("Операционная прибыль", op_profit, "rub", "БДР",
                                     _interp_operating_margin(om)),
            "operating_margin": _kpi("Операционная рентабельность", om, "percent", "БДР",
                                     _interp_operating_margin(om)),
            "operating_cashflow": _kpi("Опер. денежный поток", op_cf, "rub", "ДДС",
                                       _interp_operating_cashflow(op_cf)),
            "net_cashflow":     _kpi("Чистый денежный поток", net_cf, "rub", "ДДС",
                                     _interp_net_cashflow(net_cf)),
            "profit_to_cash_gap": _kpi("Разрыв прибыль vs деньги", gap, "rub", "БДР+ДДС",
                                       _interp_profit_to_op_cf_gap(gap, op_profit, op_cf)),
            "break_even_revenue": _kpi("Точка безубыточности", be_rev_m, "rub", "БДР",
                                       _interp_break_even_revenue(be_rev_m)),
            "break_even_gap":     _kpi("Разрыв до безубыточности", be_gap_m, "rub", "БДР",
                                       _interp_break_even_gap(be_gap_m)),
            "break_even_gap_ratio": _kpi("Покрытие точки", be_ratio_m, "percent", "БДР",
                                         _interp_break_even_gap_ratio(be_ratio_m)),
        }

    return result


def calculate_project_kpi(normalized_data: dict) -> dict:
    """
    Compute per-project KPI rows.
    Returns {project_name: {field: kpi_entry, ...}, ...}
    """
    pnl          = normalized_data.get("pnl") or {}
    pnl_projects = pnl.get("projects") or {}
    if not pnl_projects:
        return {}

    total_profit = pnl.get("operating_profit")
    result: dict = {}

    for proj, pd in pnl_projects.items():
        revenue   = pd.get("revenue")
        gp        = pd.get("gross_profit")
        opex      = pd.get("operating_expenses")
        op_profit = pd.get("operating_profit")

        gm      = safe_divide(gp, revenue)
        om      = safe_divide(op_profit, revenue)
        contrib = safe_divide(op_profit, total_profit)

        be_rev_p, be_gap_p, be_ratio_p, _ = _calc_break_even(revenue, gp, opex)

        result[proj] = {
            "revenue":          _kpi("Выручка", revenue, "rub", "БДР",
                                     f"Выручка проекта: {_rub(revenue)} ₽."),
            "gross_profit":     _kpi("Валовая прибыль", gp, "rub", "БДР",
                                     f"Валовая прибыль проекта: {_rub(gp)} ₽."),
            "gross_margin":     _kpi("Валовая маржа", gm, "percent", "БДР",
                                     _interp_gross_margin(gm)),
            "operating_expenses": _kpi("Расходы", opex, "rub", "БДР",
                                       f"Операционные расходы проекта: {_rub(opex)} ₽."),
            "operating_profit": _kpi("Прибыль", op_profit, "rub", "БДР",
                                     _interp_operating_margin(om)),
            "operating_margin": _kpi("Рентабельность", om, "percent", "БДР",
                                     _interp_operating_margin(om)),
            "contribution_to_total_profit": _kpi(
                "Вклад в результат", contrib, "ratio", "БДР",
                _interp_contribution(op_profit, total_profit, contrib),
            ),
            "break_even_revenue": _kpi(
                "Точка безубыточности (proxy)", be_rev_p, "rub", "БДР",
                _interp_break_even_revenue_project(be_rev_p),
            ),
            "break_even_gap": _kpi(
                "Разрыв до безубыточности", be_gap_p, "rub", "БДР",
                _interp_break_even_gap(be_gap_p),
            ),
            "break_even_gap_ratio": _kpi(
                "Покрытие точки", be_ratio_p, "percent", "БДР",
                _interp_break_even_gap_ratio(be_ratio_p),
            ),
        }

    return result
