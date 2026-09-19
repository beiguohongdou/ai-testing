# Hanako AI Testing - Cursor installer
# Usage:
#   install.ps1 -ProjectRoot "D:\project"
#   install.ps1 -SkillsOnly
#   install.ps1  (default: parent folder as project)

param(
    [string]$ProjectRoot = "",
    [switch]$SkillsOnly
)

$CursorRoot = $PSScriptRoot
if (-not (Test-Path (Join-Path $CursorRoot "skills"))) {
    Write-Error "Run from AI测试 folder. skills/ not found."
    exit 1
}

$skillsSrc  = Join-Path $CursorRoot "skills"
$rulesSrc   = Join-Path $CursorRoot "rules"
# .agents = Cursor 全局；.claude = Claude Code / Hanako（Cursor 也会兼容扫描）
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
        Write-Host "[OK] Skill: $($_.Name) -> $target"
    }
}

if ($skillCount -eq 0) {
    Write-Warning "No skills found in skills/"
}

if (-not $SkillsOnly) {
    if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
        $ProjectRoot = Split-Path $CursorRoot -Parent
    }
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
}

Write-Host ""
Write-Host "Done. Next steps:"
Write-Host "  1. Paste AI测试/user-rules.txt into Cursor Settings -> Rules"
Write-Host "  2. Restart Cursor or open new Agent chat"
Write-Host "  3. Test: ask for a four-part test prompt"
