# AI Testing kit - installer (Windows PowerShell)
# Usage:
#   install.ps1                                         global skill only
#   install.ps1 -ProjectRoot "D:\project"               + project rule (.cursor/rules)
#   install.ps1 -ProjectRoot "D:\project" -WithAigen    + aigen/ framework into the project

param(
    [string]$ProjectRoot = "",
    [switch]$SkillsOnly,  # 旧参数，保留兼容：不写 -ProjectRoot 时本来就只装技能
    [switch]$WithAigen
)

$KitRoot = $PSScriptRoot
if (-not (Test-Path (Join-Path $KitRoot "skills"))) {
    Write-Error "skills/ not found. Run install.ps1 from the kit folder."
    exit 1
}
if ($WithAigen -and [string]::IsNullOrWhiteSpace($ProjectRoot)) {
    Write-Error "-WithAigen needs -ProjectRoot."
    exit 1
}

$skillsSrc = Join-Path $KitRoot "skills"
$rulesSrc  = Join-Path $KitRoot "rules"
$refSrc    = Join-Path $KitRoot "reference"
# .agents = Cursor 全局；.claude = Claude Code（Cursor 也会兼容扫描）
$skillDests = @(
    (Join-Path $env:USERPROFILE ".agents\skills"),
    (Join-Path $env:USERPROFILE ".claude\skills")
)

$skillCount = 0
Get-ChildItem $skillsSrc -Directory | ForEach-Object {
    $skillCount++
    foreach ($skillsDest in $skillDests) {
        New-Item -ItemType Directory -Force -Path $skillsDest | Out-Null
        $target = Join-Path $skillsDest $_.Name
        if (Test-Path $target) { Remove-Item $target -Recurse -Force }
        Copy-Item $_.FullName $target -Recurse -Force
        if (Test-Path $refSrc) { Copy-Item $refSrc (Join-Path $target "reference") -Recurse -Force }
        Write-Host "[OK] Skill: $($_.Name) -> $target"
    }
}
if ($skillCount -eq 0) {
    Write-Warning "No skills found in skills/"
}

if (-not [string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path $ProjectRoot).Path
    $rulesDestDir = Join-Path $ProjectRoot ".cursor\rules"

    $ruleCount = 0
    if (Test-Path $rulesSrc) {
        New-Item -ItemType Directory -Force -Path $rulesDestDir | Out-Null
        Get-ChildItem $rulesSrc -Filter "*.mdc" | ForEach-Object {
            Copy-Item $_.FullName (Join-Path $rulesDestDir $_.Name) -Force
            Write-Host "[OK] Rule: $($_.Name) -> $rulesDestDir"
            $ruleCount++
        }
    }
    if ($ruleCount -eq 0) {
        Write-Warning "No rules/*.mdc found"
    }

    if ($WithAigen) {
        # 只覆盖框架代码；项目自己的 aigen.yaml / .env / cases 不动
        $aigenDest = Join-Path $ProjectRoot "aigen"
        New-Item -ItemType Directory -Force -Path $aigenDest | Out-Null
        Copy-Item (Join-Path $KitRoot "aigen\*") $aigenDest -Recurse -Force
        Get-ChildItem $aigenDest -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
        Write-Host "[OK] aigen -> $aigenDest"
        if (-not (Test-Path (Join-Path $ProjectRoot "aigen.yaml"))) {
            Write-Host "     Next: copy aigen\aigen.yaml.example to project root as aigen.yaml and edit it"
        }
        if (-not (Test-Path (Join-Path $ProjectRoot ".env"))) {
            Write-Host "     Optional: copy aigen\.env.example to project root as .env (add .env to .gitignore)"
        }
        Write-Host "     Verify: pip install -r aigen\requirements.txt; python -m aigen selftest"
    }
}

Write-Host ""
Write-Host "Done. Next steps:"
Write-Host "  1. (Optional) paste user-rules.txt into Cursor Settings -> Rules"
Write-Host "  2. Restart Cursor or open a new Agent chat"
