from pathlib import Path

import pytest

from ats_onebss.regions import RegionError, load_regions


def test_current_catalog_enables_only_north():
    catalog = load_regions(Path(__file__).parents[1] / "regions.toml")

    assert catalog.default_region == "north"
    assert catalog.get("north").enabled is True
    assert catalog.get("north").config_path.name == "config.toml"
    assert catalog.get("central").enabled is False
    assert catalog.get("south").enabled is False


def test_enabled_region_requires_existing_config(tmp_path):
    catalog_path = tmp_path / "regions.toml"
    catalog_path.write_text(
        """
default_region = "north"
[regions.north]
name = "Miền Bắc"
enabled = true
config = "missing.toml"
""",
        encoding="utf-8",
    )

    with pytest.raises(RegionError, match="đang bật nhưng thiếu"):
        load_regions(catalog_path)
