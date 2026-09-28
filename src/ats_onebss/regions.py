from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


class RegionError(ValueError):
    """Raised when the regional application catalog is invalid."""


@dataclass(frozen=True)
class Region:
    key: str
    name: str
    enabled: bool
    config_path: Path
    description: str = ""
    update_repository: str = ""


@dataclass(frozen=True)
class RegionCatalog:
    root: Path
    default_region: str
    regions: tuple[Region, ...]

    def get(self, key: str) -> Region:
        normalized = key.strip().casefold()
        for region in self.regions:
            if region.key.casefold() == normalized:
                return region
        available = ", ".join(region.key for region in self.regions)
        raise RegionError(f"Không có miền '{key}'. Các miền đã khai báo: {available}")


def load_regions(path: str | Path) -> RegionCatalog:
    catalog_path = Path(path).resolve()
    if not catalog_path.exists():
        raise RegionError(f"Không tìm thấy file phân miền: {catalog_path}")
    with catalog_path.open("rb") as handle:
        data = tomllib.load(handle)

    raw_regions = data.get("regions")
    if not isinstance(raw_regions, dict) or not raw_regions:
        raise RegionError("regions.toml phải có ít nhất một mục [regions.<mã miền>]")

    root = catalog_path.parent
    regions: list[Region] = []
    for key, raw in raw_regions.items():
        if not isinstance(raw, dict):
            raise RegionError(f"Cấu hình miền '{key}' không hợp lệ")
        name = str(raw.get("name", key)).strip()
        config_value = str(raw.get("config", "")).strip()
        if not name or not config_value:
            raise RegionError(f"Miền '{key}' phải có name và config")
        region = Region(
            key=str(key),
            name=name,
            enabled=bool(raw.get("enabled", False)),
            config_path=(root / config_value).resolve(),
            description=str(raw.get("description", "")).strip(),
            update_repository=str(raw.get("update_repository", "")).strip(),
        )
        if region.enabled and not region.config_path.exists():
            raise RegionError(
                f"Miền '{region.name}' đang bật nhưng thiếu {region.config_path}"
            )
        regions.append(region)

    default_region = str(data.get("default_region", regions[0].key)).strip()
    catalog = RegionCatalog(root, default_region, tuple(regions))
    catalog.get(default_region)
    return catalog
