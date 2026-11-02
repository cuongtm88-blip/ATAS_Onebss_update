from __future__ import annotations

import re
import unicodedata


def _key(value: str) -> str:
    value = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


# These names are evaluated against Vietnam's pre-merger provincial units.
# New provincial names are included only when all constituent former units
# have the same North/non-North classification.
_NORTH = {
    "ha noi", "hanoi", "hni", "hai phong", "tp hai phong", "hpg",
    "bac giang", "bgg", "bac kan", "bkn", "bac ninh", "bn", "bnh",
    "cao bang", "cbg", "dien bien", "dbn", "ha giang", "hgg",
    "ha nam", "hnm", "hai duong", "hdg", "hoa binh", "hbh",
    "hung yen", "hyn", "lai chau", "lch", "lang son", "lsn",
    "lao cai", "lci", "nam dinh", "ndh", "ninh binh", "nbh",
    "phu tho", "pto", "quang ninh", "qnh", "qni", "son la", "sla",
    "thai binh", "tbh", "thai nguyen", "tnn", "tuyen quang", "tqg",
    "vinh phuc", "vpc", "yen bai", "ybi", "thanh hoa", "tha",
    "nghe an", "nan", "ha tinh", "hth", "tinh ha noi", "thanh pho ha noi",
    "thanh pho bac ninh", "tp bac ninh", "thanh pho quang ninh",
    "thanh pho hai phong", "tinh bac giang", "tinh bac kan", "tinh bac ninh",
    "tinh cao bang", "tinh dien bien", "tinh ha giang", "tinh ha nam",
    "tinh hai duong", "tinh hoa binh", "tinh hung yen", "tinh lai chau",
    "tinh lang son", "tinh lao cai", "tinh nam dinh", "tinh ninh binh",
    "tinh phu tho", "tinh quang ninh", "tinh son la", "tinh thai binh",
    "tinh thai nguyen", "tinh tuyen quang", "tinh vinh phuc", "tinh yen bai",
    "tinh thanh hoa", "tinh nghe an", "tinh ha tinh", "tp ha noi",
    "tinh tuyen quang", "tinh phu tho", "tinh bac ninh", "tinh hung yen",
    "tinh ninh binh", "tinh thai nguyen", "tp hai duong",
}

_NON_NORTH = {
    "tp ho chi minh", "ho chi minh", "hcm", "ba ria vung tau", "binh duong",
    "binh phuoc", "dong nai", "tay ninh", "long an", "tien giang", "ben tre",
    "tra vinh", "vinh long", "dong thap", "an giang", "kien giang", "can tho",
    "hau giang", "soc trang", "bac lieu", "ca mau", "quang binh", "quang tri",
    "thua thien hue", "hue", "da nang", "quang nam", "quang ngai", "kon tum",
    "binh dinh", "phu yen", "khanh hoa", "ninh thuan", "binh thuan", "gia lai",
    "dak lak", "dak nong", "lam dong", "tinh ba ria vung tau", "tinh binh duong",
    "tinh binh phuoc", "tinh dong nai", "tinh tay ninh", "tinh long an",
    "tinh tien giang", "tinh ben tre", "tinh tra vinh", "tinh vinh long",
    "tinh dong thap", "tinh an giang", "tinh kien giang", "tp can tho",
    "thanh pho can tho", "tinh hau giang", "tinh soc trang", "tinh bac lieu",
    "tinh ca mau", "tinh quang binh", "tinh quang tri", "thanh pho hue",
    "tinh thua thien hue", "thanh pho da nang", "tinh quang nam", "tinh quang ngai",
    "tinh kon tum", "tinh binh dinh", "tinh phu yen", "tinh khanh hoa",
    "tinh ninh thuan", "tinh binh thuan", "tinh gia lai", "tinh dak lak",
    "tinh dak nong", "tinh lam dong", "tinh dak nong", "tinh bac lieu",
    "thanh pho ho chi minh", "tp ho chi minh", "tinh ca mau", "tinh can tho",
    "brv", "bdg", "bpc", "dni", "tni", "lan", "tgg", "btr", "tvh",
    "vlg", "dtp", "agg", "kgg", "cto", "hgi", "stg", "blu", "cmu",
    "qbh", "qti", "tth", "dng", "qnm", "qng", "ktm", "bdh", "pyu",
    "kha", "nth", "bth", "gli", "dlk", "dno", "ldg",
    "quang tri", "thanh pho hue", "thanh pho da nang", "tinh quang ngai",
    "tinh gia lai", "tinh dak lak", "tinh khanh hoa", "tinh lam dong",
    "tinh dong nai", "tinh tay ninh", "thanh pho ho chi minh",
    "thanh pho can tho", "tinh vinh long", "tinh dong thap",
}


def province_region(value: str) -> str:
    """Return north, non_north, or unknown for a province name/code."""
    key = _key(value)
    if key in _NORTH:
        return "north"
    if key in _NON_NORTH:
        return "non_north"
    return "unknown"


def ticket_region(address: str, labor_province: str) -> str:
    """Resolve location from installation address, then labor province."""
    normalized = f" {_key(address)} "
    matches: list[tuple[int, int, str]] = []
    for region, names in (("north", _NORTH), ("non_north", _NON_NORTH)):
        for name in names:
            if len(name) < 3:
                continue
            position = normalized.rfind(f" {name} ")
            if position >= 0:
                matches.append((position, len(name), region))
    if matches:
        return max(matches)[2]
    return province_region(labor_province)


def sheet_count_status(address: str, labor_province: str) -> str:
    region = ticket_region(address, labor_province)
    if region == "north":
        return ""
    if region == "non_north":
        return "Không tính"
    return "Chưa xác định"
