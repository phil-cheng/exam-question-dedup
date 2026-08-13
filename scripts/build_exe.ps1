# 打单文件 exe。文件名必须用 ASCII：PyInstaller 在 Windows 控制台下会把中文名写成乱码。
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$pyi = Join-Path $env:APPDATA "Python\Python314\Scripts\pyinstaller.exe"
if (-not (Test-Path $pyi)) {
    $pyi = "pyinstaller"
}

# 清掉上次中文名被写坏的产物
Get-ChildItem -Path . -Filter "*.spec" -File -ErrorAction SilentlyContinue | Remove-Item -Force
Get-ChildItem -Path .\dist -Filter "*.exe" -File -ErrorAction SilentlyContinue | Remove-Item -Force

& $pyi --noconfirm --clean --onefile --windowed `
    --name "QuestionDedup" `
    --specpath . `
    --add-data "template.xls;." `
    --collect-all customtkinter `
    --collect-all bm25s `
    --hidden-import xlrd `
    --hidden-import openpyxl `
    --hidden-import numpy `
    --hidden-import httpx `
    main.py

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "已生成 dist\QuestionDedup.exe"
