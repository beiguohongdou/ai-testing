# 双击 安装aigen.bat 时调用。弹出文件夹选择，把 aigen 装进选中的项目。
$ErrorActionPreference = "Stop"
$KitRoot = $PSScriptRoot

Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = "选择要安装 aigen 的项目文件夹"
$dialog.ShowNewFolderButton = $false
if (Test-Path "D:\PyProject") { $dialog.SelectedPath = "D:\PyProject" }

if ($dialog.ShowDialog($owner) -ne [System.Windows.Forms.DialogResult]::OK) {
    Write-Host "已取消。"
    exit 0
}

$project = $dialog.SelectedPath
$python = Join-Path $project ".venv\Scripts\python.exe"
Write-Host ""
Write-Host "项目：$project"

if (-not (Test-Path $python)) {
    Write-Host "没找到 $python"
    Write-Host "请先在 PyCharm 里用「项目 venv」建好这个项目。"
    exit 1
}

& (Join-Path $KitRoot "install.ps1") -ProjectRoot $project -WithAigen

$yaml = Join-Path $project "aigen.yaml"
if (-not (Test-Path $yaml)) {
    Copy-Item (Join-Path $project "aigen\aigen.yaml.example") $yaml
    Write-Host "已生成 aigen.yaml。登录地址还要按这个项目改，脚本不会替你填。"
}

Push-Location $project
try {
    & $python -m pip install -r (Join-Path $project "aigen\requirements.txt")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python -m aigen selftest
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "装好了。selftest 通过只表示生成器本身能跑。"
Write-Host "下一步是改项目根目录的 aigen.yaml，再写 cases。"
exit 0
