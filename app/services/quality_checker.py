"""
Data quality checks for normalized financial data.

Analyses completeness, convergence, and calculated-field flags to produce
an actionable quality report consumed by result.html and (later) the AI stage.
"""


def _sum_over_months(monthly: dict, field: str) -> float | None:
    """Sum a field across all month entries; returns None if field absent everywhere."""
    vals = [m[field] for m in monthly.values() if m.get(field) is not None]
    return sum(vals) if vals else None


def _sum_over_projects(projects: dict, field: str) -> float | None:
    """Sum a field across all project entries; returns None if field absent everywhere."""
    vals = [p[field] for p in projects.values() if p.get(field) is not None]
    return sum(vals) if vals else None


def check_data_quality(normalized_data: dict) -> dict:
    """
    Run quality checks on normalized financial data.

    Returns:
      {
        "status":          "good" | "warning" | "risky",
        "score":           0-100,
        "critical_issues": [...],
        "warnings":        [...],
        "notes":           [...],
        "checks":          {...},   # check_name -> "ok" | "missing" | "mismatch" | detail str
      }
    """
    result: dict = {
        "status":          "good",
        "score":           100,
        "critical_issues": [],
        "warnings":        [],
        "notes":           [],
        "checks":          {},
    }

    detected  = normalized_data.get("detected_reports") or []
    pnl       = normalized_data.get("pnl") or {}
    cashflow  = normalized_data.get("cashflow") or {}
    period    = normalized_data.get("period") or {}
    projects  = normalized_data.get("projects_detected") or []
    unmapped  = normalized_data.get("unmapped_lines") or []

    pnl_monthly   = pnl.get("monthly") or {}
    pnl_projects  = pnl.get("projects") or {}
    cf_monthly    = cashflow.get("monthly") or {}
    period_months = period.get("months") or []

    def _deduct(pts: int) -> None:
        result["score"] = max(0, result["score"] - pts)

    # ─── Check 1: БДР present ─────────────────────────────────────────────────
    if "pnl" not in detected:
        result["critical_issues"].append(
            "БДР не обнаружен. Нельзя сделать выводы по прибыли, марже и расходам."
        )
        _deduct(25)
        result["checks"]["pnl_present"] = "missing"
    else:
        result["checks"]["pnl_present"] = "ok"

    # ─── Check 2: ДДС present ─────────────────────────────────────────────────
    if "cashflow" not in detected:
        result["critical_issues"].append(
            "ДДС не обнаружен. Нельзя сделать выводы по деньгам и кассовому разрыву."
        )
        _deduct(25)
        result["checks"]["cashflow_present"] = "missing"
    else:
        result["checks"]["cashflow_present"] = "ok"

    # ─── Check 3: Net profit proxy ────────────────────────────────────────────
    if pnl.get("uses_net_profit_proxy"):
        result["warnings"].append(
            "Чистая прибыль не найдена. Используется proxy на основе прибыли "
            "от основной деятельности."
        )
        _deduct(5)
        result["checks"]["net_profit_proxy"] = "proxy"
    else:
        result["checks"]["net_profit_proxy"] = "ok"

    # ─── Check 4: Calculated fields ───────────────────────────────────────────
    if pnl.get("gross_profit_calculated"):
        result["notes"].append(
            "Валовая прибыль рассчитана как выручка минус себестоимость."
        )
        result["checks"]["gross_profit_source"] = "calculated"
    else:
        result["checks"]["gross_profit_source"] = "ok"

    outflows_src = cashflow.get("operating_outflows_source") or ""
    if "calculated" in outflows_src:
        result["notes"].append(
            "Операционные выплаты рассчитаны на основе поступлений "
            "и операционного денежного потока."
        )
        result["checks"]["operating_outflows_source"] = "calculated"

    net_cf_src = cashflow.get("net_cashflow_source") or ""
    if "calculated_from_activity_cashflows" in net_cf_src:
        result["notes"].append(
            "Чистый денежный поток рассчитан как сумма операционного, "
            "инвестиционного и финансового потоков."
        )
        result["checks"]["net_cashflow_source"] = "calculated"

    cs_src = cashflow.get("cash_start_source") or ""
    if "calculated_from_cash_end_and_net_cashflow" in cs_src:
        result["notes"].append(
            "Остаток денег на начало рассчитан на основе остатка на конец "
            "и чистого денежного потока."
        )
        result["checks"]["cash_start_source"] = "calculated"

    # ─── Check 5: Monthly PnL convergence ────────────────────────────────────
    if pnl_monthly:
        _PNL_CONV = {
            "revenue":              pnl.get("revenue"),
            "gross_profit":         pnl.get("gross_profit"),
            "operating_expenses":   pnl.get("operating_expenses"),
            "operating_profit":     pnl.get("operating_profit"),
        }
        for field, total in _PNL_CONV.items():
            if total is None:
                continue
            monthly_sum = _sum_over_months(pnl_monthly, field)
            if monthly_sum is None:
                continue
            if abs(monthly_sum - total) > 1.0:
                result["warnings"].append(
                    f"Помесячный БДР не сходится с итогом по показателю {field}."
                )
                _deduct(10)
                result["checks"][f"monthly_pnl_{field}"] = "mismatch"
            else:
                result["checks"][f"monthly_pnl_{field}"] = "ok"

    # ─── Check 6: Project PnL convergence ────────────────────────────────────
    if pnl_projects:
        _PNL_CONV = {
            "revenue":              pnl.get("revenue"),
            "gross_profit":         pnl.get("gross_profit"),
            "operating_expenses":   pnl.get("operating_expenses"),
            "operating_profit":     pnl.get("operating_profit"),
        }
        for field, total in _PNL_CONV.items():
            if total is None:
                continue
            proj_sum = _sum_over_projects(pnl_projects, field)
            if proj_sum is None:
                continue
            if abs(proj_sum - total) > 1.0:
                result["warnings"].append(
                    f"БДР по проектам не сходится с итогом по показателю {field}."
                )
                _deduct(10)
                result["checks"][f"project_pnl_{field}"] = "mismatch"
            else:
                result["checks"][f"project_pnl_{field}"] = "ok"

    # ─── Check 7: Monthly cashflow convergence ────────────────────────────────
    if cf_monthly:
        _CF_CONV = {
            "operating_cashflow": cashflow.get("operating_cashflow"),
            "financial_cashflow": cashflow.get("financial_cashflow"),
            "net_cashflow":       cashflow.get("net_cashflow"),
        }
        for field, total in _CF_CONV.items():
            if total is None:
                continue
            monthly_sum = _sum_over_months(cf_monthly, field)
            if monthly_sum is None:
                continue
            if abs(monthly_sum - total) > 1.0:
                result["warnings"].append(
                    f"Помесячный ДДС не сходится с итогом по показателю {field}."
                )
                _deduct(10)
                result["checks"][f"monthly_cf_{field}"] = "mismatch"
            else:
                result["checks"][f"monthly_cf_{field}"] = "ok"

    # ─── Check 8: Cash balance reconciliation ────────────────────────────────
    cb_recon = normalized_data.get("cash_balance_reconciliation") or {}
    if cb_recon:
        cb_net = cb_recon.get("net_cashflow")
        cf_net = cashflow.get("net_cashflow")
        if cb_net is not None and cf_net is not None:
            if abs(cb_net - cf_net) > 1.0:
                result["warnings"].append(
                    "Чистый денежный поток по файлу остатков отличается от ДДС "
                    "по видам деятельности. Для анализа используется ДДС по видам "
                    "деятельности, потому что он раскрывает операционный и финансовый поток."
                )
                _deduct(10)
                result["checks"]["cash_reconciliation"] = "mismatch"
            else:
                result["checks"]["cash_reconciliation"] = "ok"

    # ─── Check 9: Unmapped lines ──────────────────────────────────────────────
    n_unmapped = len(unmapped)
    if n_unmapped > 20 and "pnl" in detected:
        result["warnings"].append(
            "В БДР осталось много нераспознанных строк. "
            "Часть детализации могла не попасть в анализ."
        )
        _deduct(5)
        result["checks"]["pnl_unmapped_lines"] = f"high ({n_unmapped})"
    else:
        result["checks"]["pnl_unmapped_lines"] = "ok"

    if n_unmapped > 10 and "cashflow" in detected:
        result["warnings"].append(
            "В ДДС осталось много нераспознанных строк. "
            "Часть детализации могла не попасть в анализ."
        )
        _deduct(5)
        result["checks"]["cashflow_unmapped_lines"] = f"high ({n_unmapped})"
    else:
        result["checks"]["cashflow_unmapped_lines"] = "ok"

    # ─── Check 10: Empty breakdowns ───────────────────────────────────────────
    if projects and not pnl_projects:
        result["warnings"].append(
            "Проекты определены, но БДР не удалось разложить по проектам."
        )
        _deduct(10)
        result["checks"]["pnl_projects_breakdown"] = "missing"
    else:
        result["checks"]["pnl_projects_breakdown"] = "ok" if (not projects or pnl_projects) else "n/a"

    if period_months and not pnl_monthly:
        result["warnings"].append(
            "Месяцы определены, но БДР не удалось разложить по месяцам."
        )
        _deduct(10)
        result["checks"]["pnl_monthly_breakdown"] = "missing"
    else:
        result["checks"]["pnl_monthly_breakdown"] = "ok" if (not period_months or pnl_monthly) else "n/a"

    if period_months and not cf_monthly:
        result["warnings"].append(
            "Месяцы определены, но ДДС не удалось разложить по месяцам."
        )
        _deduct(10)
        result["checks"]["cashflow_monthly_breakdown"] = "missing"
    else:
        result["checks"]["cashflow_monthly_breakdown"] = "ok" if (not period_months or cf_monthly) else "n/a"

    # ─── Final status ─────────────────────────────────────────────────────────
    if result["critical_issues"]:
        result["status"] = "risky"
    elif result["score"] >= 85:
        result["status"] = "good"
    elif result["score"] >= 65:
        result["status"] = "warning"
    else:
        result["status"] = "risky"

    return result
