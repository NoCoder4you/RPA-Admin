from pathlib import Path

from common_paths import PROJECT_ROOT, cogs_dir, cogs_file, json_dir, json_file


def test_project_root_matches_repo_root() -> None:
    """PROJECT_ROOT should resolve to the repository root where this module lives."""

    assert PROJECT_ROOT == Path(__file__).resolve().parent.parent


def test_json_helpers_point_to_json_directory() -> None:
    """JSON helper utilities should consistently build paths inside JSON/."""

    assert json_dir() == PROJECT_ROOT / "JSON"
    assert json_file("VerifiedUsers.json") == PROJECT_ROOT / "JSON" / "VerifiedUsers.json"


def test_cogs_helpers_point_to_cogs_directory() -> None:
    """COGS helper utilities should consistently build paths inside COGS/."""

    assert cogs_dir() == PROJECT_ROOT / "COGS"
    assert cogs_file("BanCog.py") == PROJECT_ROOT / "COGS" / "BanCog.py"
