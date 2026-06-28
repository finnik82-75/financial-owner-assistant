import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent


class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_temperature: float = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
    openai_model_fast: str = os.getenv("OPENAI_MODEL_FAST", "gpt-4o-mini")
    openai_model_strong: str = os.getenv("OPENAI_MODEL_STRONG", "gpt-4o")
    max_files: int = int(os.getenv("MAX_FILES", "10"))
    max_file_size_mb: int = int(os.getenv("MAX_FILE_SIZE_MB", "20"))
    max_total_size_mb: int = int(os.getenv("MAX_TOTAL_SIZE_MB", "100"))

    upload_dir: Path = BASE_DIR / "data" / "uploads"
    parsed_dir: Path = BASE_DIR / "data" / "parsed"
    output_dir: Path = BASE_DIR / "data" / "outputs"
    knowledge_base_dir: Path = BASE_DIR / "knowledge_base"

    allowed_extensions: tuple = (".xlsx", ".xls", ".pdf")
    debug_mode: bool = os.getenv("DEBUG_MODE", "false").lower() == "true"


settings = Settings()

for _dir in (settings.upload_dir, settings.parsed_dir, settings.output_dir):
    _dir.mkdir(parents=True, exist_ok=True)
