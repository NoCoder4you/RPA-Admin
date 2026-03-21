from pathlib import Path


UPDATER_SCRIPT = Path(__file__).resolve().parent.parent / "RPAAdminUpdater.sh"


def test_updater_script_preserves_environment_files() -> None:
    """The updater should protect host-specific environment files from rsync updates."""

    script = UPDATER_SCRIPT.read_text()

    for guarded_entry in (
        "--filter='P .env'",
        "--filter='P .env.*'",
        "--filter='P env/'",
        "--filter='P .venv/'",
        "--filter='P venv/'",
        "--exclude='.env'",
        "--exclude='.env.*'",
        "--exclude='env/'",
        "--exclude='.venv/'",
        "--exclude='venv/'",
    ):
        assert guarded_entry in script


def test_updater_script_still_preserves_bot_py() -> None:
    """The updater should continue protecting the host-managed bot entrypoint."""

    script = UPDATER_SCRIPT.read_text()

    assert "--filter='P bot.py'" in script
    assert "--exclude='bot.py'" in script
