from pathlib import Path

from agent.tools import InputFile

DATA = Path(__file__).resolve().parents[1] / "data"


def load_case(folder: Path) -> list[InputFile]:
    return [
        InputFile(path.stem, path.name, path.read_bytes())
        for path in sorted(folder.iterdir())
        if path.stem in {"PO", "CI", "PL"} and path.suffix in {".csv", ".xlsx", ".pdf"}
    ]
