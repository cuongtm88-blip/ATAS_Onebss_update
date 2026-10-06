import json
import stat
import zipfile

import pytest

from ats_onebss.updater import (
    InstallTarget,
    UpdateError,
    UpdateRelease,
    _safe_extract,
    latest_update,
    stage_update,
)


class FakeResponse:
    def __init__(self, payload: bytes, url="https://api.github.com/response"):
        self.payload = payload
        self.url = url
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit=-1):
        return self.payload

    def geturl(self):
        return self.url


def test_latest_update_selects_platform_asset_and_requires_digest(monkeypatch):
    payload = {
        "tag_name": "v0.10.0",
        "html_url": "https://github.com/example/app/releases/tag/v0.10.0",
        "body": "Bug fixes",
        "assets": [{
            "name": "ATS-OneBSS-macOS-arm64-0.10.0.zip",
            "browser_download_url": "https://github.com/example/app/releases/download/v0.10.0/app.zip",
            "size": 100,
            "digest": "sha256:" + "a" * 64,
        }],
    }
    monkeypatch.setattr(
        "ats_onebss.updater.urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse(json.dumps(payload).encode()),
    )

    result = latest_update("example/app", "0.9.16", "darwin", "arm64")

    assert result is not None
    assert result.version == "0.10.0"
    assert result.asset.sha256 == "a" * 64

    payload["assets"][0]["digest"] = ""
    with pytest.raises(UpdateError, match="SHA-256"):
        latest_update("example/app", "0.9.16", "darwin", "arm64")


def test_latest_update_returns_none_for_current_version(monkeypatch):
    payload = {"tag_name": "v0.9.16", "assets": []}
    monkeypatch.setattr(
        "ats_onebss.updater.urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse(json.dumps(payload).encode()),
    )

    assert latest_update("example/app", "0.9.16", "darwin", "arm64") is None


def test_latest_update_rejects_invalid_repository_before_network():
    with pytest.raises(UpdateError, match="không hợp lệ"):
        latest_update("https://evil.invalid/owner/repo", "0.9.16", "darwin", "arm64")


def test_safe_extract_rejects_zip_slip(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../escape.txt", "bad")

    with pytest.raises(UpdateError, match="đường dẫn.*không an toàn"):
        _safe_extract(archive, tmp_path / "extract")


def test_safe_extract_restores_internal_symlinks_and_executable_modes(tmp_path):
    archive = tmp_path / "safe-links.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        executable = zipfile.ZipInfo("App/Contents/MacOS/ATS-OneBSS")
        executable.create_system = 3
        executable.external_attr = (stat.S_IFREG | 0o755) << 16
        zipped.writestr(executable, "binary")
        link = zipfile.ZipInfo("App/Contents/Resources/ATS-OneBSS")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(link, "../MacOS/ATS-OneBSS")

    destination = tmp_path / "extract"
    paths = _safe_extract(archive, destination)

    restored_link = destination / "App/Contents/Resources/ATS-OneBSS"
    restored_executable = destination / "App/Contents/MacOS/ATS-OneBSS"
    assert restored_link.is_symlink()
    assert restored_link.resolve() == restored_executable.resolve()
    assert restored_executable.stat().st_mode & 0o111
    assert len(paths) == 2


def test_safe_extract_rejects_symlink_escape(tmp_path):
    archive = tmp_path / "unsafe-link.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        link = zipfile.ZipInfo("App/Contents/Resources/outside")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(link, "../../../../outside")

    with pytest.raises(UpdateError, match="liên kết tượng trưng không an toàn"):
        _safe_extract(archive, tmp_path / "extract")


def test_safe_extract_rejects_entries_nested_under_symlink(tmp_path):
    archive = tmp_path / "unsafe-nested-link.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        link = zipfile.ZipInfo("App/Contents/Resources/Frameworks")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(link, "../Frameworks")
        zipped.writestr("App/Contents/Resources/Frameworks/evil", "bad")

    with pytest.raises(UpdateError, match="bên trong liên kết tượng trưng"):
        _safe_extract(archive, tmp_path / "extract")


def test_stage_update_requires_writable_existing_install_parent(tmp_path):
    target = InstallTarget("darwin", tmp_path / "missing" / "ATS.app", "ATS")
    with pytest.raises(UpdateError, match="Không có quyền ghi"):
        stage_update(tmp_path / "app.zip", target, "1.2.3")
