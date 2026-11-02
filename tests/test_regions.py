from pathlib import Path

import pytest

from ats_onebss.regions import RegionError, load_regions
from ats_onebss.ticket_region import sheet_count_status, ticket_region


def test_north_only_catalog():
    catalog = load_regions(Path(__file__).parents[1] / "regions.toml")

    assert catalog.default_region == "north"
    assert catalog.get("north").enabled is True
    assert catalog.get("north").config_path.name == "config.toml"
    assert catalog.get("north").update_repository == "cuongtm88-blip/ATAS_Onebss_update"
    assert [region.key for region in catalog.regions] == ["north"]


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


@pytest.mark.parametrize(
    ("address", "labor_province", "expected"),
    [
        ("Số 1 phố Huế, Hai Bà Trưng, Hà Nội", "HNI", ""),
        ("", "Hải Phòng", ""),
        ("Tầng 3, Quận 1, TP Hồ Chí Minh", "HNI", "Không tính"),
        ("", "Đà Nẵng", "Không tính"),
        ("Địa chỉ khách hàng chưa rõ", "", "Chưa xác định"),
    ],
)
def test_sheet_count_status_uses_installation_address_then_labor_province(
    address, labor_province, expected
):
    assert sheet_count_status(address, labor_province) == expected


def test_recognized_installation_address_takes_precedence_over_labor_province():
    assert ticket_region("Văn phòng tại Hà Nội", "TP Hồ Chí Minh") == "north"
