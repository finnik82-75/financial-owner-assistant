"""
LLM service: generate owner management report from analysis_context.

The LLM explains pre-calculated data — it does NOT compute any figures itself.
"""

from __future__ import annotations

from pathlib import Path

from app.config import settings

# ─── Russian label maps (local copy — no circular imports) ───────────────────

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

_SYSTEM_PROMPT = """Ты — финансовый директор и консультант для собственника бизнеса.

Твоя задача — объяснить управленческую отчётность простым и понятным языком.

Правила:
- Не считай показатели самостоятельно. Используй только цифры из переданного контекста.
- Если данных нет — прямо говори об ограничении, не додумывай.
- БДР показывает начисления (доходы и расходы по методу начисления).
- ДДС показывает фактическое движение денег на счетах.
- Прибыль не равна деньгам — не смешивай БДР и ДДС.
- Не называй налоги, дивиденды или ВГО проблемой сами по себе.
- Каждый вывод связан с конкретной цифрой из контекста.
- Сначала смысл, потом цифра.
- Пиши короткими, ясными предложениями. Без длинных абзацев.
- Не используй канцелярит.
- Все числовые значения (разрыв до точки безубыточности, расчётная точка безубыточности, прибыль по проектам и месяцам) бери ТОЛЬКО из переданного контекста. Не пересчитывай, не округляй, не корректируй самостоятельно.
- В пользовательском отчёте не используй технические названия полей вроде break_even_gap; замени их формулировкой «разрыв до точки безубыточности».
- Proxy-результаты называй «proxy-прибыль от основной деятельности» или «proxy-расчёт». НИКОГДА не называй proxy-прибыль «чистой прибылью».
- Если в разделе «СТАТУС ДДС» указано «ДДС НЕ НАЙДЕН» — ЗАПРЕЩЕНО выводить числовые значения: операционный денежный поток, чистый денежный поток, финансовый поток, остаток денег, денежный запас, кассовый разрыв, конверсия прибыли в деньги. Вместо любого числа по ДДС писать: «не рассчитано — ДДС не найден». Это правило приоритетнее любой инструкции по структуре отчёта.
- Если ДДС не найден, не включай числовой операционный денежный минус, не пиши пункт про крупнейшую статью ДДС-расходов как проблему и не делай выводов по денежному потоку. Вместо этого добавь в раздел «Рекомендуемые действия» действие «Дозагрузить ДДС или банковскую выписку».
- Раздел «KPI собственника» ОБЯЗАТЕЛЬНО содержит строку «Налоги / выручка», если в разделе «KPI СОБСТВЕННИКА» контекста есть этот показатель. Это управленческий KPI, не налоговая отчётность — не пропускать.

Обязательные требования к содержанию:
- Используй ВСЕ ключевые факты из раздела КЛЮЧЕВЫЕ ФАКТЫ — каждый факт должен найти отражение в отчёте.
- Раздел «Помесячная динамика» обязан опираться на данные из раздела ПОМЕСЯЧНЫЕ KPI — называй конкретные месяцы и конкретные суммы.
- Раздел «Проекты / направления» обязан перечислить КАЖДЫЙ проект из раздела KPI ПО ПРОЕКТАМ — и прибыльные, и убыточные. Пропускать проекты нельзя.
- ТОП-3 проблемы: каждая проблема должна содержать конкретную цифру из данных. Общие формулировки без цифр не допускаются.
- ТОП-3 решения: каждое решение привязано к конкретной проблеме и содержит название проекта, статьи расходов или месяца.

Запрещённые фразы:
- «финансово-хозяйственная деятельность демонстрирует»
- «наблюдается отрицательный тренд»
- «имеются некоторые риски»
- «необходимо обратить внимание»
- «в целом ситуация неоднозначная»
- «за период» или «в анализируемом периоде»
- «увеличить выручку» без указания конкретного канала, проекта или клиентского сегмента
- «оптимизировать расходы» без указания конкретной статьи расходов
- «пересмотреть проекты» без названия конкретного проекта

Формат ответа: Markdown. Начни сразу с заголовка «# Управленческий отчёт …»."""


# ─── Format helpers ───────────────────────────────────────────────────────────

def _rub(v) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}{abs(v):,.0f}".replace(",", " ")


def _signed(v) -> str:
    """Format rub value with explicit + sign for positive numbers."""
    if v is None:
        return "—"
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return "—"
    prefix = "+" if fv > 0 else ""
    return f"{prefix}{_rub(v)}"


# ─── User prompt builder ──────────────────────────────────────────────────────

def _build_user_prompt(ctx: dict) -> str:
    period      = ctx.get("period") or {}
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

    # Cashflow availability — drives conditional instruction blocks
    has_cashflow = bool(cf.get("totals"))

    lines: list[str] = []
    lines.append(f"Финансовые данные {period_label}. Напиши управленческий отчёт по инструкции в конце.\n")

    # ── Key findings ──────────────────────────────────────────────────────────
    lines.append("=== КЛЮЧЕВЫЕ ФАКТЫ ===")
    for i, f in enumerate(findings, 1):
        lines.append(f"{i}. {f}")
    lines.append(f"\nГлавный итог: {bs.get('main_result', '—')}\n")

    # ── PnL totals ────────────────────────────────────────────────────────────
    pnl_totals = pnl.get("totals") or {}
    if pnl_totals:
        lines.append("=== БДР — ИТОГО ===")
        for k, v in pnl_totals.items():
            if v is not None:
                lines.append(f"  {_PNL_LABELS_RU.get(k, k)}: {_signed(v)} ₽")
        lines.append("")

    # ── PnL monthly ───────────────────────────────────────────────────────────
    pnl_monthly = pnl.get("monthly") or {}
    if pnl_monthly:
        lines.append("=== БДР — ПОМЕСЯЧНО ===")
        for mk in sorted(pnl_monthly.keys()):
            m    = pnl_monthly[mk]
            name = name_map.get(mk, mk).capitalize()
            parts = []
            for k in ["revenue", "gross_profit", "operating_expenses", "operating_profit"]:
                if m.get(k) is not None:
                    parts.append(f"{_PNL_LABELS_RU.get(k, k)} {_signed(m[k])} ₽")
            lines.append(f"  {name}: " + " | ".join(parts))
        lines.append("")

    # ── PnL by project ────────────────────────────────────────────────────────
    pnl_projects = pnl.get("projects") or {}
    if pnl_projects:
        lines.append("=== БДР — ПО ПРОЕКТАМ ===")
        for proj, pd in pnl_projects.items():
            rev = pd.get("revenue")
            gp  = pd.get("gross_profit")
            op  = pd.get("operating_profit")
            opex = pd.get("operating_expenses")
            lines.append(
                f"  {proj}: выручка {_rub(rev)} ₽"
                f" | вал. прибыль {_signed(gp)} ₽"
                f" | расходы {_signed(opex)} ₽"
                f" | прибыль {_signed(op)} ₽"
            )
        lines.append("")

    # ── Cashflow totals ───────────────────────────────────────────────────────
    cf_totals = cf.get("totals") or {}
    if cf_totals:
        lines.append("=== ДДС — ИТОГО ===")
        for k, v in cf_totals.items():
            if v is not None:
                lines.append(f"  {_CF_LABELS_RU.get(k, k)}: {_signed(v)} ₽")
        lines.append("")

    # ── Cashflow monthly ──────────────────────────────────────────────────────
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

    # ── Cashflow details ──────────────────────────────────────────────────────
    cf_details = cf.get("details") or {}
    if cf_details:
        lines.append("=== ДЕТАЛИЗАЦИЯ ДДС (отсортировано по сумме) ===")
        sorted_details = sorted(
            [(k, v) for k, v in cf_details.items() if v is not None],
            key=lambda x: float(x[1]),
        )
        for k, v in sorted_details:
            lines.append(f"  {_CF_DETAIL_LABELS_RU.get(k, k)}: {_signed(v)} ₽")
        lines.append("")

    # ── KPI ───────────────────────────────────────────────────────────────────
    sk = kpi.get("summary_kpi") or {}
    if sk:
        lines.append("=== KPI СОБСТВЕННИКА ===")
        _pnl_kpi_keys = [
            "revenue", "gross_profit", "gross_margin",
            "operating_expenses", "operating_expense_ratio",
            "payroll_ratio", "rent_ratio", "taxes_ratio",
            "operating_profit", "operating_margin",
            "break_even_revenue", "break_even_gap", "break_even_gap_ratio",
        ]
        _cf_kpi_keys = [
            "operating_cashflow", "net_cashflow",
            "profit_to_operating_cashflow_gap", "profit_to_net_cashflow_gap",
            "profit_to_cash_conversion", "net_cashflow_ratio", "cash_reserve_months",
        ]
        # Always emit all PnL KPIs; taxes_ratio always appears (may be "не рассчитано")
        for k in _pnl_kpi_keys:
            entry = sk.get(k)
            if not entry:
                continue
            fmt = entry.get("formatted")
            if fmt in (None, "—"):
                # taxes_ratio must always appear even without data
                if k == "taxes_ratio":
                    interp = entry.get("interpretation") or "В данных нет суммы налогов."
                    lines.append(f"  {entry['label']}: не рассчитано")
                    lines.append(f"    ({interp})")
                # other missing PnL KPIs are skipped (revenue, gross_profit, etc.)
            else:
                interp = entry.get("interpretation", "")
                lines.append(f"  {entry['label']}: {fmt}")
                if interp:
                    lines.append(f"    ({interp})")
        # Cashflow KPIs: emit real values or explicit unavailability marker
        if has_cashflow:
            for k in _cf_kpi_keys:
                entry = sk.get(k)
                if entry and entry.get("formatted") not in (None, "—"):
                    interp = entry.get("interpretation", "")
                    lines.append(f"  {entry['label']}: {entry['formatted']}")
                    if interp:
                        lines.append(f"    ({interp})")
        else:
            _cf_fallback_labels = {
                "operating_cashflow":               "Операционный денежный поток",
                "net_cashflow":                     "Чистый денежный поток",
                "profit_to_operating_cashflow_gap": "Разрыв: прибыль vs опер. поток",
                "profit_to_net_cashflow_gap":       "Разрыв: прибыль vs чистый поток",
                "profit_to_cash_conversion":        "Конверсия прибыли в деньги",
                "net_cashflow_ratio":               "Чистый поток / поступления",
                "cash_reserve_months":              "Денежный запас (мес.)",
            }
            for k, fallback_lbl in _cf_fallback_labels.items():
                lbl = (sk.get(k) or {}).get("label") or fallback_lbl
                lines.append(f"  {lbl}: не рассчитано — ДДС не найден")
        lines.append("")

    # ── Monthly KPI ───────────────────────────────────────────────────────────
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
            lines.append(
                f"  {name}: выручка {rev} | маржа {gm} | прибыль {op}"
                f" | рентаб. {om} | опер.ДДС {opcf} | чист.ДДС {ncf}"
                f" | точка безуб. {bev} | разрыв {beg}"
            )
        lines.append("")

    # ── Project KPI ───────────────────────────────────────────────────────────
    pk_data = kpi.get("project_kpi") or {}
    if pk_data:
        lines.append("=== KPI ПО ПРОЕКТАМ ===")
        for proj, pd in pk_data.items():
            rev    = (pd.get("revenue") or {}).get("formatted", "—")
            gm     = (pd.get("gross_margin") or {}).get("formatted", "—")
            op     = (pd.get("operating_profit") or {}).get("formatted", "—")
            om     = (pd.get("operating_margin") or {}).get("formatted", "—")
            bev    = (pd.get("break_even_revenue") or {}).get("formatted", "—")
            beg    = (pd.get("break_even_gap") or {}).get("formatted", "—")
            contrib = (pd.get("contribution_to_total_profit") or {}).get("interpretation", "")
            lines.append(
                f"  {proj}: выручка {rev} | маржа {gm} | прибыль {op}"
                f" | рентаб. {om} | точка безуб. {bev} | разрыв {beg}"
            )
            if contrib:
                lines.append(f"    Вклад: {contrib}")
        lines.append("")

    # ── Constraints (deduplicated, order preserved) ───────────────────────────
    qwarn = quality.get("warnings") or []
    _seen: set[str] = set()
    deduped_constraints: list[str] = []
    for c in (constraints + qwarn):
        if c not in _seen:
            _seen.add(c)
            deduped_constraints.append(c)
    if deduped_constraints:
        lines.append("=== ОГРАНИЧЕНИЯ ===")
        for c in deduped_constraints:
            lines.append(f"  • {c}")
        lines.append("")

    # ── Data quality ──────────────────────────────────────────────────────────
    q_score  = quality.get("score")
    q_status = quality.get("status")
    if q_score is not None:
        lines.append(f"Качество данных: {q_status}, оценка {q_score}/100\n")

    # ── Explicit DDS status (hard gate for LLM) ───────────────────────────────
    if not has_cashflow:
        lines.append("=== СТАТУС ДДС ===")
        lines.append("  КРИТИЧЕСКИ ВАЖНО: ДДС (движение денежных средств) НЕ НАЙДЕН.")
        lines.append("  ЗАПРЕЩЕНО писать числовые значения: операционный поток, чистый")
        lines.append("  поток, финансовый поток, остаток денег, денежный запас,")
        lines.append("  кассовый разрыв, конверсия прибыли в деньги.")
        lines.append("  ВМЕСТО ЧИСЕЛ писать везде: «не рассчитано — ДДС не найден».")
        lines.append("")

    # ── Conditional instruction blocks ───────────────────────────────────────
    _section11_rule = ""
    _main_problem_line = "- **Главная проблема:** [название конкретной проблемы] — [сумма] ₽ (проект или статья)"
    if has_cashflow:
        _verdikt_chto = (
            "- **Что произошло:** выручка [X] ₽, операционная прибыль [Y] ₽, "
            "операционный ДДС [Z] ₽, чистый ДДС [W] ₽"
        )
        _verdikt_risk = (
            "- **Главный риск:** укажи конкретную угрозу, связанную с деньгами. "
            "Запрещено писать «чистый денежный поток упадёт до [X] ₽» — это тавтология. "
            "Пиши по образцу: «Без повторного финансового притока операционный денежный "
            "минус [значение operating_cashflow] ₽ напрямую снизит остаток денег на счёте.»"
        )
        _section2_include = (
            "Включи: выручку, валовую прибыль, прибыль от осн. деятельности, "
            "операционный ДДС, финансовый ДДС, чистый ДДС, остаток денег на конец."
        )
        _section6 = (
            "## 6. Деньги / ДДС\n"
            "Операционный поток, финансовый поток (объясни его роль: откуда пришли "
            "деньги), чистый поток, остаток денег, денежный запас."
        )
        _section7 = (
            "## 7. Прибыль vs деньги\n"
            "Разрыв между прибылью (БДР) и операционным денежным потоком (ДДС). "
            "Роль финансового потока. Конкретные суммы."
        )
        _s10_dds = "4. Крупнейшая статья ДДС-расходов: из раздела ДЕТАЛИЗАЦИЯ ДДС"
    else:
        _verdikt_chto = (
            "- **Что произошло:** выручка составила [X] ₽, прибыль от основной деятельности [Y] ₽."
        )
        _verdikt_risk = (
            "- **Ограничение анализа:** ДДС не найден, поэтому нельзя корректно оценить "
            "операционный денежный поток, чистый денежный поток, остаток денег, "
            "финансовый поток и риск кассового разрыва."
        )
        _main_problem_line = (
            "- **Главная проблема:** фактическая выручка ниже точки безубыточности "
            "на [сумма] ₽."
        )
        _section2_include = (
            "Включи только БДР-показатели: выручку, валовую прибыль, прибыль от "
            "осн. деятельности. По каждому ДДС-показателю (операционный поток, "
            "финансовый поток, чистый поток, остаток денег) пиши строку: "
            "«не рассчитано — ДДС не найден»."
        )
        _section6 = (
            "## 6. Деньги / ДДС\n"
            "ДДС не был загружен. Напиши ТОЛЬКО: «ДДС не найден. Данные о движении "
            "денежных средств отсутствуют. Выводы по денежным потокам, остатку денег "
            "и кассовым разрывам недоступны.» Не указывай никаких числовых значений."
        )
        _section7 = (
            "## 7. Прибыль vs деньги\n"
            "ДДС не найден. Напиши ТОЛЬКО: «Сравнение прибыли с денежным потоком "
            "невозможно — ДДС не был загружен.» Не указывай никаких числовых значений."
        )
        _s10_dds = (
            "4. ДДС не загружен | — | — | нельзя оценить денежный поток, "
            "кассовый разрыв и остаток денег"
        )
        _section11_rule = (
            "Если ДДС не найден, обязательно добавь действие: "
            "«Дозагрузить ДДС или банковскую выписку»."
        )

    # ── Task instruction ──────────────────────────────────────────────────────
    lines.append(f"""\
=== ИНСТРУКЦИЯ ===

Напиши управленческий отчёт для собственника на русском языке в формате Markdown.
Используй «{period_label}» везде вместо «за период» и «в анализируемом периоде».
Каждый вывод — конкретная цифра из данных выше. Не вычисляй ничего сам.

Структура (строго соблюдать все 12 разделов):

# Управленческий отчёт {period_label}

## 1. Вердикт
Ровно 4 строки (маркированный список). Каждая строка — конкретный факт с цифрой из данных выше:
{_verdikt_chto}
{_main_problem_line}
{_verdikt_risk}
- **Первое действие:** [конкретное действие с названием проекта/статьи] — пример формата: «Разобрать экономику проекта [название]: убыток [сумма] ₽ при выручке [сумма] ₽»

## 2. Итоги {period_label}
Таблица: Показатель | Значение | Смысл
{_section2_include}

## 3. Помесячная динамика
Используй данные из раздела ПОМЕСЯЧНЫЕ KPI. Обязательно:
- Если все месяцы убыточны: «Все месяцы были убыточными. Наименее слабый месяц — [месяц] ([сумма] ₽). Самый слабый месяц — [месяц] ([сумма] ₽).»
- Если есть прибыльные месяцы: назови лучший и худший месяц с конкретными суммами операционной прибыли.
- В любом случае: объясни причину разницы между месяцами — выручка изменилась? расходы выросли?

## 4. Проекты / направления
Используй данные из раздела KPI ПО ПРОЕКТАМ. Перечисли КАЖДЫЙ проект из этого раздела — пропускать нельзя.
Для каждого прибыльного проекта (operating_profit > 0) напиши строку:
«[Название] — прибыль [operating_profit] ₽, выше proxy-точки безубыточности на [значение разрыва до точки безубыточности] ₽»
Для каждого убыточного проекта (operating_profit < 0) напиши строку:
«[Название] — убыток [operating_profit] ₽, ниже proxy-точки безубыточности на [значение разрыва до точки безубыточности] ₽»
Затем укажи: какой убыточный проект наиболее критичен (наибольший отрицательный разрыв до точки безубыточности) и какова его доля в общем убытке.

## 5. Экономика / БДР
Выручка, валовая маржа, расходы, операционная прибыль, нагрузка ФОТ, налоговая нагрузка (Налоги / выручка).

{_section6}

{_section7}

## 8. KPI собственника
Таблица: KPI | Значение | Вывод — включи КАЖДЫЙ показатель из раздела «KPI СОБСТВЕННИКА».
Обязательные строки (не пропускать ни одну, если есть данные):
Выручка | Валовая прибыль | Валовая маржа | Операционные расходы | Расходы / выручка | ФОТ / выручка | **Налоги / выручка** | Прибыль от осн. деятельности | Операционная рентабельность | Точка безубыточности | Отклонение от точки безубыточности | Покрытие точки безубыточности.
Если в данных есть налоговая нагрузка и выручка, обязательно сохрани строку «Налоги / выручка».
По показателям ДДС (операционный поток, чистый поток, денежный запас): используй значение из KPI СОБСТВЕННИКА, либо пиши «не рассчитано — ДДС не найден».

## 9. Точка безубыточности
Используй данные расчётной точки безубыточности, разрыва до точки безубыточности и покрытия из KPI СОБСТВЕННИКА и ПОМЕСЯЧНЫЕ KPI / KPI ПО ПРОЕКТАМ.

Итог за период:
- Расчётная точка безубыточности: [значение break_even_revenue] ₽
- Фактическая выручка: [значение revenue] ₽
- Разрыв до безубыточности: [значение разрыва до точки безубыточности] ₽ (покрытие: [значение покрытия разрыва до точки безубыточности])

По месяцам — маркированный список каждого месяца из ПОМЕСЯЧНЫЕ KPI:
- [Месяц]: выручка [X] ₽, точка безубыточности [Y] ₽, разрыв [значение разрыва до точки безубыточности] ₽ (покрытие [значение покрытия разрыва до точки безубыточности])
Укажи, какой месяц ближе всего к безубыточности (наибольшее покрытие), какой — дальше всего.

По проектам — ОБЯЗАТЕЛЬНО перечислить КАЖДЫЙ проект из раздела KPI ПО ПРОЕКТАМ.
Нельзя пропустить ни один проект, если для него есть разрыв до точки безубыточности.
КРИТИЧЕСКИ ВАЖНО: значение разрыва до точки безубыточности для каждого проекта бери ТОЧНО из раздела KPI ПО ПРОЕКТАМ — не пересчитывай и не изменяй. Например, если в данных Наружная реклама разрыв до точки безубыточности = +239 038 ₽, в отчёте должна быть именно эта цифра.
Раздели проекты на два маркированных подсписка:

Выше proxy-точки безубыточности (разрыв до точки безубыточности > 0):
- [Название проекта]: разрыв +[значение разрыва до точки безубыточности] ₽

Ниже proxy-точки безубыточности (разрыв до точки безубыточности < 0):
- [Название проекта]: разрыв [значение разрыва до точки безубыточности] ₽

Самопроверка перед финальным ответом: убедись, что количество проектов в этом разделе равно количеству проектов в KPI ПО ПРОЕКТАМ. Если проект есть в KPI ПО ПРОЕКТАМ — он обязан быть упомянут либо в списке «выше», либо в списке «ниже».

Обязательно укажи дословно: «Расчёт является proxy, потому что расходы не разделены полностью на постоянные и переменные.»

## 10. Ключевые проблемы
Выведи от 3 до 5 проблем. Каждая — с конкретной цифрой из данных. Без цифры — не включать.
Обязательно рассмотри как кандидатов:
1. Недобор выручки до точки безубыточности за период: разрыв до точки безубыточности из KPI СОБСТВЕННИКА (отрицательное число)
2. Месяц с наибольшим отрицательным разрывом до точки безубыточности: из раздела ПОМЕСЯЧНЫЕ KPI
3. Проект с наибольшим отрицательным разрывом до точки безубыточности: из раздела KPI ПО ПРОЕКТАМ
{_s10_dds}
5. Нагрузка ФОТ/выручка: payroll_ratio из KPI СОБСТВЕННИКА
Таблица: Проблема | Цифра | Месяц / проект | Риск

## 11. Рекомендуемые действия
Выведи от 3 до 5 действий. Каждое привязано к конкретной проблеме из раздела «Ключевые проблемы» — и содержит название проекта, статьи расходов, месяца или KPI.
{_section11_rule}
Запрещено: «увеличить выручку», «оптимизировать расходы», «пересмотреть проекты» — без указания конкретного проекта, статьи или месяца.
Кандидаты для действий:
- Сократить разрыв до точки безубыточности: указать конкретную сумму недобора (разрыв до точки безубыточности) и что именно — выручка или расходы — создаёт этот разрыв
- Разобрать экономику месяца с наибольшим отрицательным разрывом до точки безубыточности: почему расходы в этом месяце резко превысили валовую прибыль?
- Разобрать экономику проекта с наибольшим отрицательным разрывом до точки безубыточности: название проекта, убыток, разрыв до безубыточности
- Снизить нагрузку ФОТ: назвать конкретный уровень ФОТ/выручка и какую статью ФОТ стоит пересмотреть
Таблица: Действие | Где применить (проект/статья/месяц) | Что контролировать | Ожидаемый эффект
Не придумывай точный денежный эффект — пиши «снизить операционный убыток», «проверить экономику проекта [название]».

## 12. Ограничения анализа
Перечисли ограничения из раздела ОГРАНИЧЕНИЯ выше. Не добавляй ограничений, которых нет в данных.\
""")

    return "\n".join(lines)


# ─── Prompt builder ───────────────────────────────────────────────────────────

def build_owner_report_prompt(
    analysis_context: dict,
    knowledge_base: dict | None = None,
) -> list[dict]:
    """Return the messages list for the OpenAI chat completion call."""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": _build_user_prompt(analysis_context)},
    ]


# ─── Report post-processing ─────────────────────────────────────────────────

def is_cashflow_missing(analysis_context: dict | None) -> bool:
    if not analysis_context:
        return False

    cashflow_found = False

    found_reports = analysis_context.get("found_reports") or analysis_context.get("detected_reports") or {}
    if isinstance(found_reports, dict):
        for key in ("dds", "cashflow", "cash_flow"):
            value = found_reports.get(key)
            if value is True:
                cashflow_found = True
                break
            if isinstance(value, str) and value.lower() in {"true", "yes", "found", "detected"}:
                cashflow_found = True
                break
            if value is False:
                break
    elif isinstance(found_reports, (list, tuple, set)):
        found_text = " ".join(str(v).lower() for v in found_reports)
        if "dds" in found_text and ("найден" in found_text or "обнаружен" in found_text or "загружен" in found_text):
            cashflow_found = True

    quality = analysis_context.get("quality") or analysis_context.get("data_quality") or {}
    warnings: list[str] = []
    if isinstance(quality, dict):
        warnings.extend(quality.get("warnings") or [])
        warnings.extend(quality.get("critical_issues") or [])
        warnings.extend(quality.get("notes") or [])
    warning_text = " ".join(str(w).lower() for w in warnings)
    if "ддс" in warning_text and ("не найден" in warning_text or "не загружен" in warning_text or "не обнаружен" in warning_text or "не найдено" in warning_text):
        cashflow_found = False

    cashflow = analysis_context.get("cashflow") or {}
    if not isinstance(cashflow, dict):
        cashflow = {}
    totals = cashflow.get("totals") or {}
    if isinstance(totals, dict):
        values = [v for v in totals.values() if v not in (None, "", "—", "not_found", "не рассчитано")]
        if values:
            cashflow_found = True

    parsed_data = analysis_context.get("parsed_data") or {}
    parsed_cashflow = parsed_data.get("cashflow") or {}
    if isinstance(parsed_cashflow, dict):
        parsed_totals = parsed_cashflow.get("totals") or {}
        if isinstance(parsed_totals, dict):
            parsed_values = [v for v in parsed_totals.values() if v not in (None, "", "—", "not_found", "не рассчитано")]
            if parsed_values:
                cashflow_found = True

    if cashflow_found:
        return False

    return True


def sanitize_owner_report_when_no_cashflow(report_md: str, analysis_context: dict | None = None) -> str:
    if not report_md:
        return report_md
    if not is_cashflow_missing(analysis_context):
        return report_md

    lines = report_md.splitlines()

    def _find_section_start(lines_list: list[str], heading: str) -> int | None:
        for i, line in enumerate(lines_list):
            if line.startswith(heading):
                return i
        return None

    def _find_next_heading(lines_list: list[str], start_idx: int, prefix: str) -> int:
        for j in range(start_idx + 1, len(lines_list)):
            if lines_list[j].startswith(prefix):
                return j
        return len(lines_list)

    # Section 1: replace forbidden cashflow phrases and add the analysis limitation.
    sec1_start = _find_section_start(lines, "## 1. Вердикт")
    if sec1_start is not None:
        sec1_end = _find_next_heading(lines, sec1_start, "## 2.")
        section_lines = lines[sec1_start:sec1_end]
        cleaned_lines: list[str] = []
        for line in section_lines:
            lower = line.lower()
            if any(
                phrase in lower
                for phrase in [
                    "повторного финансового притока",
                    "операционный денежный минус",
                    "снизит остаток денег",
                    "остаток денег на счёте",
                    "кассовый разрыв",
                    "денежный минус",
                ]
            ):
                continue
            cleaned_lines.append(line)
        replacement = []
        replacement.extend(cleaned_lines)
        if not any("Ограничение анализа: ДДС не найден" in line for line in replacement):
            replacement.append(
                "- Ограничение анализа: ДДС не найден, поэтому нельзя корректно оценить "
                "операционный денежный поток, чистый денежный поток, остаток денег, "
                "финансовый поток и риск кассового разрыва."
            )
        lines[sec1_start:sec1_end] = replacement

    # Section 10: remove cashflow-related problem rows and add the fallback row.
    sec10_start = _find_section_start(lines, "## 10. Ключевые проблемы")
    if sec10_start is not None:
        sec10_end = _find_next_heading(lines, sec10_start, "## 11.")
        section_lines = lines[sec10_start:sec10_end]
        cleaned_lines = []
        for line in section_lines:
            lower = line.lower()
            if any(
                token in lower
                for token in [
                    "крупнейшая статья ддс-расходов",
                    "ддс-расходов",
                    "операционный денежный минус",
                    "кассовый разрыв",
                    "денежный поток",
                ]
            ):
                continue
            cleaned_lines.append(line)
        if not any("ДДС не загружен" in line for line in cleaned_lines):
            cleaned_lines.extend([
                "",
                "**Дополнительное ограничение по данным:**",
                "- ДДС не загружен — нельзя оценить денежный поток, кассовый разрыв и остаток денег.",
            ])
        lines[sec10_start:sec10_end] = cleaned_lines

    # Section 11: add the required action line.
    sec11_start = _find_section_start(lines, "## 11. Рекомендуемые действия")
    if sec11_start is not None:
        sec11_end = _find_next_heading(lines, sec11_start, "## 12.")
        section_lines = lines[sec11_start:sec11_end]
        if not any("Дозагрузить ДДС или банковскую выписку" in line for line in section_lines):
            section_lines.extend([
                "",
                "**Дополнительное действие:**",
                "- Дозагрузить ДДС или банковскую выписку. Это позволит оценить поступления, выплаты, остаток денег, операционный денежный поток, кассовый разрыв и денежный запас.",
            ])
            lines[sec11_start:sec11_end] = section_lines

    # Section 7: replace any cashflow-vs-profit comparison with a safe no-cashflow note.
    sec7_start = _find_section_start(lines, "## 7. Прибыль vs деньги")
    if sec7_start is not None:
        sec7_end = _find_next_heading(lines, sec7_start, "## 8.")
        lines[sec7_start:sec7_end] = [
            "## 7. Прибыль vs деньги",
            "",
            "Сравнение прибыли и денег не выполнено, потому что ДДС не найден.",
            "",
            "По БДР видна прибыль от основной деятельности, но без ДДС нельзя проверить, "
            "как эта прибыль связана с фактическим движением денег.",
        ]

    return "\n".join(lines).strip() + "\n"


def generate_owner_report(
    analysis_context: dict,
    knowledge_base: dict | None = None,
) -> dict:
    """
    Call OpenAI to generate the owner management report.

    Returns:
        {status, report_markdown, model, error, saved_path}

    On success, saves the report to data/outputs/{analysis_id}_owner_report.md
    and returns saved_path.
    """
    if not settings.openai_api_key:
        return {
            "status":          "error",
            "report_markdown": "",
            "model":           "",
            "error":           "AI-отчёт не сформирован: не настроен OPENAI_API_KEY.",
            "saved_path":      "",
        }

    messages = build_owner_report_prompt(analysis_context, knowledge_base)

    try:
        from openai import OpenAI
        client   = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            temperature=settings.openai_temperature,
            max_tokens=4096,
        )
        report_md = response.choices[0].message.content or ""
        cashflow_missing = is_cashflow_missing(analysis_context)
        cashflow_found = not cashflow_missing
        print("[OWNER_REPORT] cashflow_missing =", cashflow_missing)
        print("[OWNER_REPORT] cashflow_found =", cashflow_found)
        if cashflow_missing:
            report_md = sanitize_owner_report_when_no_cashflow(report_md, analysis_context)
        model_used = response.model or settings.openai_model

    except Exception as exc:
        return {
            "status":          "error",
            "report_markdown": "",
            "model":           settings.openai_model,
            "error":           f"Ошибка при обращении к OpenAI: {exc}",
            "saved_path":      "",
        }

    # Save markdown file
    analysis_id = analysis_context.get("analysis_id", "unknown")
    out_path    = settings.output_dir / f"{analysis_id}_owner_report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_md, encoding="utf-8")

    return {
        "status":          "success",
        "report_markdown": report_md,
        "model":           model_used,
        "error":           None,
        "saved_path":      str(out_path),
    }
