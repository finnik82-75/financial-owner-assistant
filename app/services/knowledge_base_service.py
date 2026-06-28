"""Load and expose the knowledge base content."""
from pathlib import Path

import yaml

KB_ROOT = Path("knowledge_base")


def read_markdown_file(path: str | Path) -> str:
    path = Path(path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_yaml_file(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def load_manifest() -> dict:
    return load_yaml_file(KB_ROOT / "kb_manifest.yaml")


def load_knowledge_base() -> dict:
    return {
        "manifest": load_manifest(),
        "role": read_markdown_file(KB_ROOT / "00_role" / "cfo_owner_role.md"),
        "report_templates": {
            "owner":    read_markdown_file(KB_ROOT / "01_report_templates" / "owner_report_template.md"),
            "extended": read_markdown_file(KB_ROOT / "01_report_templates" / "extended_report_template.md"),
        },
        "methodology": {
            "pnl":               read_markdown_file(KB_ROOT / "02_financial_methodology" / "pnl_structure.md"),
            "cashflow":          read_markdown_file(KB_ROOT / "02_financial_methodology" / "cashflow_structure.md"),
            "profit_vs_cash":    read_markdown_file(KB_ROOT / "02_financial_methodology" / "profit_vs_cash.md"),
            "owner_kpi":         read_markdown_file(KB_ROOT / "02_financial_methodology" / "owner_kpi.md"),
            "data_limitations":  read_markdown_file(KB_ROOT / "02_financial_methodology" / "data_limitations.md"),
            "calculation_rules": load_yaml_file(KB_ROOT / "02_financial_methodology" / "calculation_rules.yaml"),
        },
        "mapping": {
            "pnl":              load_yaml_file(KB_ROOT / "03_mapping" / "pnl_mapping_rules.yaml"),
            "cashflow":         load_yaml_file(KB_ROOT / "03_mapping" / "cashflow_mapping_rules.yaml"),
            "cashflow_details": load_yaml_file(KB_ROOT / "03_mapping" / "cashflow_details_mapping_rules.yaml"),
            "ignore":           load_yaml_file(KB_ROOT / "03_mapping" / "ignore_rules.yaml"),
        },
        "language": {
            "owner_language_rules": read_markdown_file(KB_ROOT / "04_language" / "owner_language_rules.md"),
            "forbidden_phrases":    read_markdown_file(KB_ROOT / "04_language" / "forbidden_phrases.md"),
            "explanation_examples": read_markdown_file(KB_ROOT / "04_language" / "explanation_examples.md"),
        },
        "industry_cases": {
            "media_group":       read_markdown_file(KB_ROOT / "05_industry_cases" / "media_group.md"),
            "management_company": read_markdown_file(KB_ROOT / "05_industry_cases" / "management_company.md"),
            "rental_business":   read_markdown_file(KB_ROOT / "05_industry_cases" / "rental_business.md"),
        },
        "quality_control": {
            "data_quality_rules":   read_markdown_file(KB_ROOT / "06_quality_control" / "data_quality_rules.md"),
            "contradiction_rules":  read_markdown_file(KB_ROOT / "06_quality_control" / "contradiction_rules.md"),
            "source_priority_rules": load_yaml_file(KB_ROOT / "06_quality_control" / "source_priority_rules.yaml"),
            "fallback_messages":    read_markdown_file(KB_ROOT / "06_quality_control" / "fallback_messages.md"),
        },
        "examples": {
            "zmg_good_report":      read_markdown_file(KB_ROOT / "07_examples" / "zmg_good_report.md"),
            "owner_report_example": read_markdown_file(KB_ROOT / "07_examples" / "owner_report_example.md"),
        },
    }


def count_aliases(mapping_rules: dict) -> int:
    total = 0
    for rule in mapping_rules.values():
        if isinstance(rule, dict):
            total += len(rule.get("aliases", []))
    return total
