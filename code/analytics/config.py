from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Settings:
    input_csv: Path
    output_dir: Path
    charts_dir: Path
    tables_dir: Path

def build_settings(input_csv: str, output_dir: str) -> Settings:
    out = Path(output_dir)
    return Settings(
        input_csv=Path(input_csv),
        output_dir=out,
        charts_dir=out / "charts",
        tables_dir=out / "tables",
    )
