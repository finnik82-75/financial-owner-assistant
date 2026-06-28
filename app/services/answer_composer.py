"""
Answer composer: explains pre-computed answer_payload using LLM.

LLM receives ONLY answer_payload + constraints — it cannot recalculate.
For general management questions: uses full compact context.
"""
from __future__ import annotations

import json

from app.config import settings

# ─── System prompts ───────────────────────────────────────────────────────────

_PAYLOAD_SYSTEM_PROMPT = """Ты — финансовый консультант для собственника бизнеса.
Тебе переданы готовые расчёты в ANSWER_PAYLOAD.

ПРАВИЛА (соблюдать строго):
1. Используй ТОЛЬКО цифры из ANSWER_PAYLOAD. Не пересчитывай, не изменяй, не округляй.
2. Если status="no_data" → «В загруженных отчётах данных нет.»
3. Если status="needs_clarification" → задай вопрос из clarification_question.
4. Proxy-прибыль → «proxy-прибыль от основной деятельности», НИКОГДА «чистая прибыль».
5. Если есть tables_markdown — обязательно включи таблицы в ответ без изменений.
6. Если есть limitations → упомяни их в конце.
7. НЕ добавляй цифры, которых нет в ANSWER_PAYLOAD.
8. НЕ предлагай список вопросов после ответа.
9. НЕ смешивай БДР (начисления) и ДДС (деньги).
10. Следуй answer_guidance для структуры ответа.
11. Если в payload есть таблицы расходов — выводи ТОЛЬКО строки из этих таблиц.
    НЕ добавляй новые статьи из контекста. НЕ дублируй строки.
    НЕ включай ВГО/ВГО-услуги/ВГО-проценты в операционные расходы.
12. Если facts.subtype='loss_projects_only': показывай в таблице ТОЛЬКО убыточные проекты.
    Прибыльные упомяни не более одной строкой текста ПОСЛЕ таблицы. НЕ добавляй их в таблицу.
    Если facts.subtype='profitable_projects_only': аналогично — только прибыльные в таблице.
13. Если limitations пусто или отсутствует — НЕ пиши «Нет ограничений».
    Просто не выводи блок ограничений.

Формат: Markdown. Кратко, управленческим языком.
— Развёрнутый вопрос: ## Ответ / ## Расшифровка / ## Ограничения.
— Уточняющий: без заголовков, 2–4 предложения или список."""

_EXPENSE_SUMMARY_SYSTEM = """Ты — краткий финансовый аналитик.
Таблицы расходов уже построены Python-кодом и показаны пользователю выше.
Твоя задача — написать ТОЛЬКО короткое резюме (2–5 предложений).

СТРОГИЕ ПРАВИЛА — нарушение недопустимо:
1. НЕ воспроизводи таблицы и НЕ перечисляй строки заново.
2. НЕ добавляй строки в таблицу. НЕ добавляй статьи, которых нет в переданных фактах.
3. НЕ смешивай группы и детализацию. НЕ включай итоговую строку как статью.
4. НЕ включай ВГО, ВГО-услуги, ВГО-проценты, выручку, прибыль в операционные расходы.
5. НЕ смешивай цифры БДР и ДДС — это разные базы.
6. ЕСЛИ в payload есть ОБА раздела (БДР и ДДС): обязательно предупреди, что их нельзя складывать между собой.
7. Упомяни крупнейшую статью в каждом показанном разделе (из переданных фактов).
8. Если показан ТОЛЬКО ОДИН раздел — НЕ упоминай другой раздел и НЕ пиши про несовместимость."""

_GENERAL_SYSTEM_PROMPT = """Ты — финансовый директор и консультант для собственника бизнеса.
Ты отвечаешь на вопросы по управленческому отчёту.
Используй ТОЛЬКО данные из ФИНАНСОВОГО КОНТЕКСТА ниже.
НЕ придумывай данные. НЕ пересчитывай KPI.
НЕ называй proxy-прибыль чистой прибылью. НЕ смешивай БДР и ДДС.
После ответа НЕ выводи список предлагаемых вопросов. Запрещено.
Формат: Markdown. Кратко и по существу."""


# ─── Table pre-renderer ───────────────────────────────────────────────────────

def _render_tables(tables: list[dict]) -> str:
    if not tables:
        return ""
    lines: list[str] = []
    for t in tables:
        title   = t.get("title") or ""
        headers = t.get("headers") or []
        rows    = t.get("rows") or []
        if title:
            lines.append(f"\n**{title}**")
        if headers:
            lines.append("| " + " | ".join(str(h) for h in headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in rows:
            lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


# ─── Message builders ─────────────────────────────────────────────────────────

def _build_payload_messages(
    question: str,
    payload: dict,
    history: list[dict] | None = None,
) -> list[dict]:
    tables_md = _render_tables(payload.get("tables") or [])
    payload_for_llm = {k: v for k, v in payload.items() if k != "tables"}
    if tables_md:
        payload_for_llm["tables_markdown"] = tables_md

    history_ctx = ""
    if history:
        parts = []
        for entry in history[-2:]:
            q = (entry.get("question") or "").strip()
            a = (entry.get("answer") or "").strip()
            if q:
                parts.append(f"Вопрос: {q}")
            if a:
                parts.append(f"Ответ (кратко): {(a[:250] + '…') if len(a) > 250 else a}")
        if parts:
            history_ctx = "\n=== КОНТЕКСТ ДИАЛОГА ===\n" + "\n".join(parts) + "\n"

    payload_json  = json.dumps(payload_for_llm, ensure_ascii=False, indent=2)
    user_content  = (
        f"Вопрос пользователя: {question}\n"
        f"{history_ctx}\n"
        f"ANSWER_PAYLOAD:\n```json\n{payload_json}\n```\n\n"
        "Следуй answer_guidance из ANSWER_PAYLOAD."
    )
    return [
        {"role": "system", "content": _PAYLOAD_SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]


def _build_general_messages(
    question: str,
    compact_context: str,
    history: list[dict] | None = None,
) -> list[dict]:
    history_snippet = ""
    if history:
        parts = []
        for entry in history[-3:]:
            q = (entry.get("question") or "").strip()
            a = (entry.get("answer") or "").strip()
            if q:
                parts.append(f"Вопрос: {q}")
            if a:
                parts.append(f"Ответ: {(a[:300] + '…') if len(a) > 300 else a}")
        if parts:
            history_snippet = "\n=== ИСТОРИЯ ДИАЛОГА ===\n" + "\n".join(parts)

    user_content = (
        f"Вопрос: {question}\n\n"
        f"ФИНАНСОВЫЙ КОНТЕКСТ:\n{compact_context}"
        f"{history_snippet}"
    )
    return [
        {"role": "system", "content": _GENERAL_SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]


# ─── Fallback (no LLM) ────────────────────────────────────────────────────────

def _fallback_answer(payload: dict) -> str:
    status = payload.get("status", "")
    if status == "no_data":
        guidance = payload.get("answer_guidance") or ""
        return "В загруженных отчётах данных для ответа нет.\n\n" + guidance if guidance else \
               "В загруженных отчётах данных для ответа нет."
    if status == "needs_clarification":
        q = payload.get("clarification_question") or payload.get("answer_guidance") or "Уточните вопрос."
        return f"Нужно уточнение: {q}"
    guidance = payload.get("answer_guidance") or ""
    return (
        "Расчёт выполнен. AI-объяснение временно недоступно.\n\n"
        f"*{guidance}*"
    ) if guidance else "Расчёт выполнен. AI-объяснение временно недоступно."


# ─── Expense ranking: pre-rendered tables + short LLM conclusion ─────────────

def _render_expense_debug(payload: dict) -> str:
    """Build a DEBUG block shown under the answer when debug_mode is enabled."""
    if not settings.debug_mode:
        return ""
    facts        = payload.get("facts") or {}
    pnl_excluded = facts.get("pnl_excluded") or []
    cf_excluded  = facts.get("cf_excluded") or []
    pnl_overflow = facts.get("pnl_overflow_warning", False)
    cf_overflow  = facts.get("cf_overflow_warning", False)

    lines = ["", "---", "**DEBUG: Детали расчёта расходов**", ""]
    lines.append(f"- **intent:** {payload.get('intent', '—')}")
    lines.append(f"- **scope:** {facts.get('scope', 'both')}")
    lines.append(f"- **as_percent:** {facts.get('as_percent', False)}")
    if "pnl_base_amount_rub" in facts:
        lines.append(f"- **pnl_base:** {facts.get('pnl_base_amount_rub', '—')} (operating_expenses)")
    if "cf_base_amount_rub" in facts:
        lines.append(f"- **cf_base:**  {facts.get('cf_base_amount_rub', '—')} (operating_outflows)")

    if pnl_overflow or cf_overflow:
        lines.append("")
        if pnl_overflow:
            lines.append("- **WARN БДР:** сумма статей > 115% базы — возможен двойной счёт")
        if cf_overflow:
            lines.append("- **WARN ДДС:** сумма статей > 115% базы — возможен двойной счёт")

    if pnl_excluded:
        lines.append("")
        lines.append("**Исключены из БДР (причины):**")
        for ex in pnl_excluded:
            lines.append(f"- `{ex['key']}` ({ex['label']}) — {ex['level']}: {ex['reason']}")

    if cf_excluded:
        lines.append("")
        lines.append("**Исключены из ДДС (причины):**")
        for ex in cf_excluded:
            lines.append(f"- `{ex['key']}` ({ex['label']}) — {ex['level']}: {ex['reason']}")

    return "\n".join(lines)


def _compose_expense_answer(
    question: str,
    payload: dict,
    prerendered_md: str,
    history: list[dict] | None = None,
) -> dict:
    """Combine Python-rendered expense tables with a short LLM conclusion."""
    facts      = payload.get("facts") or {}
    scope      = facts.get("scope", "both")
    pnl_items  = facts.get("pnl_expenses") or []
    cf_items   = facts.get("cf_payments") or []
    as_percent = facts.get("as_percent", False)

    pnl_top = pnl_items[0] if pnl_items else {}
    cf_top  = cf_items[0]  if cf_items  else {}
    debug   = _render_expense_debug(payload)

    def _fmt_top(item: dict) -> str:
        s = f"**{item.get('label', '—')}** — {item.get('amount_rub', '—')} ₽"
        if as_percent and item:
            s += f" ({item.get('share_pct', '—')})"
        return s

    def _python_conclusion() -> str:
        parts: list[str] = []
        if pnl_top and scope in ("both", "pnl_only"):
            parts.append(f"Крупнейшая статья операционных расходов (БДР): {_fmt_top(pnl_top)}.")
            if scope == "pnl_only":
                parts.append(f"Доли рассчитаны от операционных расходов БДР — {facts.get('pnl_base_amount_rub', '—')} ₽.")
        if cf_top and scope in ("both", "cashflow_only"):
            parts.append(f"Крупнейшая денежная выплата (ДДС): {_fmt_top(cf_top)}.")
            if scope == "cashflow_only":
                parts.append(f"Доли рассчитаны от операционных выплат ДДС — {facts.get('cf_base_amount_rub', '—')} ₽.")
        if scope == "both":
            parts.append(
                "*БДР и ДДС — разные базы: БДР показывает начисления, ДДС — движение денег. "
                "Суммы и проценты из этих двух таблиц нельзя складывать между собой.*"
            )
        return "\n\n".join(parts) if parts else "Расчёт выполнен."

    if not settings.openai_api_key:
        return {
            "status":          "success",
            "answer_markdown": prerendered_md + "\n---\n\n" + _python_conclusion() + debug,
            "model":           "",
            "error":           None,
        }

    def _top_str(item: dict) -> str:
        s = f"{item.get('label', '—')} ({item.get('amount_rub', '—')} ₽"
        if as_percent and item:
            s += f", {item.get('share_pct', '—')}"
        return s + ")"

    scope_label = {
        "pnl_only":      "только БДР (НЕ упоминай ДДС, НЕ пиши про несовместимость БДР/ДДС)",
        "cashflow_only": "только ДДС (НЕ упоминай БДР, НЕ пиши про несовместимость БДР/ДДС)",
        "both":          "БДР и ДДС (обязательно укажи, что их нельзя складывать)",
    }.get(scope, "оба раздела")

    prompt_lines = [f"Вопрос: {question}", "", f"Область: {scope_label}", "", "Факты:"]
    if pnl_top and scope in ("both", "pnl_only"):
        prompt_lines.append(f"- Крупнейшая статья БДР: {_top_str(pnl_top)}")
        prompt_lines.append(f"- База БДР: {facts.get('pnl_base_amount_rub', '—')} ₽")
    if cf_top and scope in ("both", "cashflow_only"):
        prompt_lines.append(f"- Крупнейшая выплата ДДС: {_top_str(cf_top)}")
        prompt_lines.append(f"- База ДДС: {facts.get('cf_base_amount_rub', '—')} ₽")
    prompt_lines += ["", "Ограничения: " + "; ".join(payload.get("limitations") or []), ""]
    if scope == "pnl_only":
        prompt_lines.append("Напиши 2–3 предложения: крупнейшая статья БДР и база для расчёта.")
    elif scope == "cashflow_only":
        prompt_lines.append("Напиши 2–3 предложения: крупнейшая выплата ДДС и база для расчёта.")
    else:
        prompt_lines.append("Напиши 3–5 предложений: крупнейшая статья в каждом разделе и предупреждение о несовместимости.")
    user_prompt = "\n".join(prompt_lines)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _EXPENSE_SUMMARY_SYSTEM},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=settings.openai_temperature,
            max_tokens=350,
        )
        conclusion = response.choices[0].message.content or ""
        model_used = response.model or settings.openai_model
        return {
            "status":          "success",
            "answer_markdown": prerendered_md + "\n---\n\n" + conclusion + debug,
            "model":           model_used,
            "error":           None,
        }
    except Exception:
        return {
            "status":          "success",
            "answer_markdown": prerendered_md + "\n---\n\n" + _python_conclusion() + debug,
            "model":           settings.openai_model,
            "error":           None,
        }


# ─── Main entry point ─────────────────────────────────────────────────────────

def compose_answer(
    question: str,
    answer_payload: dict,
    analysis_context: dict,
    compact_context: str | None = None,
    history: list[dict] | None = None,
) -> dict:
    """
    Produce the final Markdown answer by explaining answer_payload via LLM.

    When compact_context is provided (general management questions), uses full
    financial context instead of the payload-only path.

    Returns:
        {status, answer_markdown, model, error}
    """
    # Pre-rendered expense tables: Python builds tables, LLM adds only brief conclusion
    payload_status = answer_payload.get("status", "")
    if (
        answer_payload.get("answer_type") == "expense_ranking"
        and answer_payload.get("prerendered_tables_md")
        and payload_status == "success"
    ):
        return _compose_expense_answer(
            question, answer_payload,
            answer_payload["prerendered_tables_md"],
            history,
        )

    if not settings.openai_api_key:
        return {
            "status":          "success",
            "answer_markdown": _fallback_answer(answer_payload),
            "model":           "",
            "error":           None,
        }

    # no_data: skip LLM, return explanation directly
    if payload_status == "no_data":
        guidance = answer_payload.get("answer_guidance") or ""
        return {
            "status":          "success",
            "answer_markdown": (
                "В загруженных отчётах данных для ответа на этот вопрос не найдено.\n\n"
                + guidance
            ).strip(),
            "model":           "",
            "error":           None,
        }

    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key)

        if compact_context:
            messages = _build_general_messages(question, compact_context, history)
        else:
            messages = _build_payload_messages(question, answer_payload, history)

        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            temperature=settings.openai_temperature,
            max_tokens=2048,
        )
        answer_md  = response.choices[0].message.content or ""
        model_used = response.model or settings.openai_model

        return {
            "status":          "success",
            "answer_markdown": answer_md,
            "model":           model_used,
            "error":           None,
        }

    except Exception as exc:
        return {
            "status":          "error",
            "answer_markdown": _fallback_answer(answer_payload),
            "model":           settings.openai_model,
            "error":           f"Ошибка при обращении к OpenAI: {exc}",
        }
