"""Validate the knowledge base structure and content."""
from pathlib import Path

from app.services.knowledge_base_service import load_yaml_file

KB_ROOT = Path("knowledge_base")


def validate_required_files(kb_root: Path, manifest: dict) -> list[str]:
    errors: list[str] = []
    for relative_path in manifest.get("required_files", []):
        file_path = kb_root / relative_path
        if not file_path.exists():
            errors.append(f"Missing required file: {relative_path}")
    return errors


def validate_yaml_files(kb_root: Path) -> list[str]:
    errors: list[str] = []
    for yaml_path in kb_root.rglob("*.yaml"):
        try:
            load_yaml_file(yaml_path)
        except Exception as exc:
            errors.append(f"Invalid YAML {yaml_path.relative_to(kb_root)}: {exc}")
    return errors


def validate_mapping_rules(mapping: dict) -> list[str]:
    errors: list[str] = []
    for mapping_name, rules in mapping.items():
        if mapping_name == "ignore":
            continue
        if not isinstance(rules, dict):
            errors.append(f"Mapping '{mapping_name}' is not a dict")
            continue
        for key, rule in rules.items():
            if not isinstance(rule, dict):
                errors.append(f"Rule {mapping_name}.{key} is not a dict")
                continue
            aliases = rule.get("aliases", [])
            if not isinstance(aliases, list):
                errors.append(f"Rule {mapping_name}.{key} aliases is not a list")
            elif not aliases:
                errors.append(f"Rule {mapping_name}.{key} has empty aliases")
    return errors


def validate_knowledge_base(kb: dict, kb_root: Path = KB_ROOT) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    manifest = kb.get("manifest", {})
    if not manifest:
        errors.append("kb_manifest.yaml not loaded or empty")

    errors.extend(validate_required_files(kb_root, manifest))
    errors.extend(validate_yaml_files(kb_root))
    errors.extend(validate_mapping_rules(kb.get("mapping", {})))

    status = "success" if not errors else "error"
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
    }
