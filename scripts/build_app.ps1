$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

uv sync --extra packaging
uv run pyinstaller `
  --noconfirm `
  --clean `
  --onedir `
  --windowed `
  --name ATS-OneBSS `
  --collect-all playwright `
  --collect-submodules encodings `
  --add-data "regions.toml;." `
  --add-data "config.toml;." `
  --add-data "project_rules.toml;." `
  --add-data "Giao phiếu.xlsx;." `
  --add-data "assets/vinaphone-logo.png;assets" `
  src/ats_onebss/gui_main.py

Write-Host "Đã tạo ứng dụng trong dist/ATS-OneBSS"
