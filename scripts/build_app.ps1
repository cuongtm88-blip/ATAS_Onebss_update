$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$ruleWorkbook = Get-ChildItem -LiteralPath . -File -Filter "*.xlsx" |
  Where-Object {
    $_.Name.Normalize([Text.NormalizationForm]::FormC) -eq "Giao phiếu_demo2.xlsx"
  } |
  Select-Object -First 1
if (-not $ruleWorkbook) {
  throw "Không tìm thấy file Giao phiếu_demo2.xlsx trong thư mục dự án."
}

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
  --add-data "$($ruleWorkbook.FullName);." `
  --add-data "assets/vinaphone-logo.png;assets" `
  src/ats_onebss/gui_main.py

Write-Host "Đã tạo ứng dụng trong dist/ATS-OneBSS"
