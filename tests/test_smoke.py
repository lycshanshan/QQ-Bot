from pathlib import Path


def test_required_project_files_exist() -> None:
    root = Path(__file__).parents[1]
    assert (root / "bot" / "__main__.py").is_file()
    assert (root / "bot" / "plugins" / "basic.py").is_file()
    assert (root / ".env.example").is_file()

