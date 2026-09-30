from pathlib import Path

from ats_onebss.config import load_config


def test_legacy_runtime_config_adds_reassignment_column_without_replacing_settings(
    tmp_path,
):
    source = Path(__file__).parents[1] / "config.toml"
    content = source.read_text(encoding="utf-8")
    content = content.replace(
        '"project_name", "reassignment",',
        '"project_name",',
    )
    runtime_config = tmp_path / "config.toml"
    runtime_config.write_text(content, encoding="utf-8")

    config = load_config(runtime_config)

    assert config.sheet_columns[-2:] == ("project_name", "reassignment")
    assert config.sheet_name == "Tháng 8/2026"
