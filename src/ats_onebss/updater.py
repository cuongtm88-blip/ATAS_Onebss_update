from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from plistlib import load as load_plist


MAX_DOWNLOAD_SIZE = 1024 * 1024 * 1024
MAX_EXPANDED_SIZE = 2 * 1024 * 1024 * 1024
GITHUB_API = "https://api.github.com"


class UpdateError(RuntimeError):
    """A safe, user-presentable updater error."""


@dataclass(frozen=True)
class UpdateAsset:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class UpdateRelease:
    version: str
    tag_name: str
    page_url: str
    notes: str
    asset: UpdateAsset


@dataclass(frozen=True)
class InstallTarget:
    platform_name: str
    target_path: Path
    executable_name: str


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if not match:
        raise UpdateError(f"Phiên bản không đúng định dạng x.y.z: {value!r}")
    return tuple(int(item) for item in match.groups())


def _platform_asset(platform_name: str, machine: str) -> str:
    machine = machine.casefold()
    if platform_name == "darwin":
        if machine in {"arm64", "aarch64"}:
            return "macOS-arm64"
        if machine in {"x86_64", "amd64"}:
            return "macOS-x64"
    elif platform_name == "win32":
        if machine in {"amd64", "x86_64", "arm64", "aarch64"}:
            return "Windows-x64"
    raise UpdateError(
        f"Chưa có gói cập nhật cho hệ điều hành/kiến trúc này: "
        f"{platform_name}/{machine}."
    )


def latest_update(
    repository: str,
    current_version: str,
    platform_name: str | None = None,
    machine: str | None = None,
) -> UpdateRelease | None:
    """Find the latest stable release asset for this platform, if newer."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise UpdateError("Repository cập nhật không hợp lệ.")
    platform_name = platform_name or sys.platform
    machine = machine or platform.machine()
    current = _version_tuple(current_version)
    endpoint = f"{GITHUB_API}/repos/{repository}/releases/latest"
    request = urllib.request.Request(
        endpoint,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ATS-OneBSS-Updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read(2_000_001))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise UpdateError(f"GitHub trả về HTTP {error.code} khi kiểm tra bản mới.") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise UpdateError(f"Không kết nối được GitHub để kiểm tra bản mới: {error}") from error

    tag = str(payload.get("tag_name", ""))
    latest = _version_tuple(tag)
    if payload.get("draft") or payload.get("prerelease") or latest <= current:
        return None
    asset_prefix = _platform_asset(platform_name, machine)
    expected_name = f"ATS-OneBSS-{asset_prefix}-{tag.removeprefix('v')}.zip"
    raw_asset = next(
        (
            item for item in payload.get("assets", [])
            if item.get("name") == expected_name
        ),
        None,
    )
    if raw_asset is None:
        raise UpdateError(
            f"Release {tag} chưa có gói {asset_prefix} phù hợp."
        )
    download_url = str(raw_asset.get("browser_download_url", ""))
    parsed_url = urllib.parse.urlparse(download_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "github.com":
        raise UpdateError("GitHub Release trả về địa chỉ tải không an toàn.")
    digest = str(raw_asset.get("digest", ""))
    digest_match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", digest)
    if not digest_match:
        raise UpdateError("Release thiếu mã SHA-256 để xác minh gói cập nhật.")
    size = int(raw_asset.get("size", 0))
    if size <= 0 or size > MAX_DOWNLOAD_SIZE:
        raise UpdateError("Kích thước gói cập nhật không hợp lệ hoặc vượt giới hạn.")
    return UpdateRelease(
        version=tag.removeprefix("v"),
        tag_name=tag,
        page_url=str(payload.get("html_url", "")),
        notes=str(payload.get("body", "")).strip(),
        asset=UpdateAsset(
            name=expected_name,
            url=download_url,
            size=size,
            sha256=digest_match.group(1).lower(),
        ),
    )


def download_asset(release: UpdateRelease, directory: Path) -> Path:
    """Download an update archive and verify GitHub's SHA-256 digest."""
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / release.asset.name
    temporary = destination.with_name(destination.name + ".part")
    request = urllib.request.Request(
        release.asset.url,
        headers={"Accept": "application/octet-stream", "User-Agent": "ATS-OneBSS-Updater"},
    )
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme != "https" or final.hostname not in {
                "github.com", "release-assets.githubusercontent.com",
                "objects.githubusercontent.com",
            }:
                raise UpdateError("Tệp được chuyển hướng đến máy chủ tải không hợp lệ.")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) != release.asset.size:
                raise UpdateError("Kích thước gói tải xuống khác với GitHub Release.")
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_DOWNLOAD_SIZE:
                    raise UpdateError("Gói cập nhật vượt quá giới hạn tải xuống.")
                digest.update(chunk)
                output.write(chunk)
        if total != release.asset.size:
            raise UpdateError("Gói tải xuống chưa đầy đủ.")
        if digest.hexdigest() != release.asset.sha256:
            raise UpdateError("Mã SHA-256 không khớp; đã từ chối cài đặt gói.")
        temporary.replace(destination)
        return destination
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, UpdateError) as error:
        temporary.unlink(missing_ok=True)
        if isinstance(error, UpdateError):
            raise
        raise UpdateError(f"Không tải được gói cập nhật: {error}") from error


def detect_install_target(
    executable: str | Path | None = None,
    platform_name: str | None = None,
) -> InstallTarget:
    platform_name = platform_name or sys.platform
    executable_path = Path(executable or sys.executable).resolve()
    if platform_name == "darwin":
        bundle = next(
            (parent for parent in executable_path.parents if parent.suffix == ".app"),
            None,
        )
        if bundle is None:
            raise UpdateError("Cập nhật tự động chỉ dùng được trong bản ứng dụng đã đóng gói.")
        return InstallTarget(platform_name, bundle, "ATS-OneBSS")
    if platform_name == "win32":
        if not getattr(sys, "frozen", executable_path.suffix.casefold() == ".exe"):
            raise UpdateError("Cập nhật tự động chỉ dùng được trong bản ứng dụng đã đóng gói.")
        return InstallTarget(platform_name, executable_path.parent, "ATS-OneBSS.exe")
    raise UpdateError("Cập nhật tự động hiện hỗ trợ macOS và Windows.")


def _safe_extract(archive: Path, destination: Path) -> list[Path]:
    extracted: list[Path] = []
    expanded_size = 0
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise UpdateError("Gói cập nhật chứa đường dẫn tệp không an toàn.")
            mode = member.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise UpdateError("Gói cập nhật chứa liên kết tượng trưng không được hỗ trợ.")
            expanded_size += member.file_size
            if expanded_size > MAX_EXPANDED_SIZE:
                raise UpdateError("Gói cập nhật giải nén vượt quá giới hạn.")
        zipped.extractall(destination)
        extracted = [destination / item.filename for item in zipped.infolist()]
    return extracted


def stage_update(archive: Path, target: InstallTarget, version: str) -> tuple[Path, Path]:
    """Extract and validate a platform package beside the installed app."""
    parent = target.target_path.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK):
        raise UpdateError(
            f"Không có quyền ghi vào thư mục ứng dụng: {parent}. "
            "Hãy cài ATS OneBSS vào thư mục người dùng hoặc cập nhật thủ công."
        )
    try:
        with tempfile.NamedTemporaryFile(dir=parent, prefix=".ats-update-test-", delete=True):
            pass
    except OSError as error:
        raise UpdateError(
            f"Không có quyền ghi vào thư mục ứng dụng: {parent}. "
            "Hãy cài ATS OneBSS vào thư mục người dùng hoặc cập nhật thủ công."
        ) from error
    staging_root = Path(tempfile.mkdtemp(prefix=f".ats-onebss-update-{version}-", dir=parent))
    try:
        paths = _safe_extract(archive, staging_root)
        if target.platform_name == "darwin":
            bundles = [
                path for path in staging_root.iterdir()
                if path.is_dir() and path.suffix == ".app"
            ]
            if len(bundles) != 1:
                raise UpdateError("Gói macOS không chứa đúng một ứng dụng .app.")
            bundle = bundles[0]
            info_path = bundle / "Contents" / "Info.plist"
            try:
                with info_path.open("rb") as stream:
                    executable_name = str(load_plist(stream)["CFBundleExecutable"])
            except (OSError, KeyError, ValueError) as error:
                raise UpdateError("Gói macOS thiếu metadata ứng dụng hợp lệ.") from error
            if not (bundle / "Contents" / "MacOS" / executable_name).is_file():
                raise UpdateError("Gói macOS thiếu chương trình thực thi.")
            staged_app = bundle
        else:
            candidates = [
                path for path in staging_root.iterdir()
                if path.is_dir() and (path / target.executable_name).is_file()
            ]
            if len(candidates) != 1:
                raise UpdateError("Gói Windows không chứa đúng một thư mục ứng dụng hợp lệ.")
            staged_app = candidates[0]
        backup = target.target_path.with_name(
            f"{target.target_path.name}.backup-{version}-{uuid.uuid4().hex[:8]}"
        )
        return staged_app, backup
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise


def launch_installer(
    staged_app: Path,
    backup_path: Path,
    target: InstallTarget,
    parent_pid: int | None = None,
    script_directory: Path | None = None,
) -> Path:
    """Start an OS helper that swaps the app after this process exits."""
    parent_pid = parent_pid or os.getpid()
    script_directory = script_directory or Path(tempfile.gettempdir())
    script_directory.mkdir(parents=True, exist_ok=True)
    script = script_directory / f"ats-onebss-update-{uuid.uuid4().hex}.{'ps1' if target.platform_name == 'win32' else 'sh'}"
    if target.platform_name == "darwin":
        content = "\n".join([
            "#!/bin/sh",
            f"parent_pid={shlex.quote(str(parent_pid))}",
            f"staged={shlex.quote(str(staged_app))}",
            f"target={shlex.quote(str(target.target_path))}",
            f"backup={shlex.quote(str(backup_path))}",
            'while kill -0 "$parent_pid" 2>/dev/null; do sleep 0.5; done',
            'if [ -e "$target" ]; then mv "$target" "$backup" || exit 1; fi',
            'if ! mv "$staged" "$target"; then',
            '  if [ -e "$backup" ]; then mv "$backup" "$target"; fi',
            '  exit 2',
            'fi',
            'open "$target"',
            'rmdir "$(dirname "$staged")" 2>/dev/null || true',
            "",
        ])
        script.write_text(content, encoding="utf-8")
        script.chmod(0o700)
        subprocess.Popen(
            ["/bin/sh", str(script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    elif target.platform_name == "win32":
        content = r'''param([int]$ParentPid, [string]$Staged, [string]$Target, [string]$Backup)
while (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 500 }
if (Test-Path -LiteralPath $Target) { Move-Item -LiteralPath $Target -Destination $Backup }
try {
    Move-Item -LiteralPath $Staged -Destination $Target
} catch {
    if (Test-Path -LiteralPath $Backup) { Move-Item -LiteralPath $Backup -Destination $Target }
    exit 2
}
Remove-Item -LiteralPath (Split-Path -Parent $Staged) -Force -ErrorAction SilentlyContinue
Start-Process -FilePath (Join-Path $Target 'ATS-OneBSS.exe')
'''
        script.write_text(content, encoding="utf-8")
        subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(script),
                "-ParentPid", str(parent_pid), "-Staged", str(staged_app),
                "-Target", str(target.target_path), "-Backup", str(backup_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
    else:
        raise UpdateError("Hệ điều hành này chưa hỗ trợ cài gói cập nhật.")
    return script
