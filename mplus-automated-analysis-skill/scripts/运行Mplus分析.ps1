$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Venv = Join-Path $Root ".mplusflow-venv"

function Get-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return $python.Source }
    return $null
}

function Test-SupportedPython([string]$Python) {
    & $Python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
    return $LASTEXITCODE -eq 0
}

function Test-RuntimeReady([string]$Python) {
    if (-not $Python -or -not (Test-Path $Python) -or -not (Test-SupportedPython $Python)) {
        return $false
    }
    & $Python -c "import numpy, openpyxl, pandas, pyreadstat" 2>$null
    return $LASTEXITCODE -eq 0
}

if ($args.Count -gt 0 -and $args[0] -eq "bootstrap") {
    if ($args.Count -lt 2 -or $args[1] -ne "--yes") {
        Write-Error "环境尚未改动。请在用户明确同意后运行：.\scripts\运行Mplus分析.ps1 bootstrap --yes"
        exit 2
    }

    $Python = Get-PythonCommand
    if (-not $Python -or -not (Test-SupportedPython $Python)) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if (-not $winget) {
            Write-Error "未找到 Python 3.10+，且未找到 winget。请先安装 Python 3.10+，再重新运行 bootstrap --yes。"
            exit 2
        }
        Write-Host "正在通过 winget 安装 Python 3.12..."
        winget install --id Python.Python.3.12 --exact --accept-package-agreements --accept-source-agreements
        $Python = Get-PythonCommand
    }
    if (-not $Python -or -not (Test-SupportedPython $Python)) {
        Write-Error "Python 3.10+ 安装后仍不可用。请重新打开 PowerShell 后再运行 bootstrap --yes。"
        exit 2
    }

    & $Python -m venv $Venv
    & (Join-Path $Venv "Scripts\python.exe") -m pip install --upgrade pip
    & (Join-Path $Venv "Scripts\python.exe") -m pip install -r (Join-Path $Root "runtime\requirements.txt")
    $env:PYTHONPATH = (Join-Path $Root "runtime") + ";" + $env:PYTHONPATH
    & (Join-Path $Venv "Scripts\python.exe") -m mplusflow doctor
    exit $LASTEXITCODE
}

$VenvPython = Join-Path $Venv "Scripts\python.exe"
if (Test-RuntimeReady $VenvPython) {
    $Python = $VenvPython
} else {
    $Candidate = Get-PythonCommand
    if (Test-RuntimeReady $Candidate) {
        $Python = $Candidate
    } else {
        $Python = $null
    }
}
if (-not $Python) {
    if (Get-PythonCommand) {
        Write-Error "已找到 Python，但本 Skill 需要的数据读取依赖不齐。环境尚未改动。请让 Agent 说明用途并征得你的同意后运行：.\scripts\运行Mplus分析.ps1 bootstrap --yes"
    } else {
        Write-Error "未找到 Python 3.10+。环境尚未改动。请让 Agent 征得你的同意后运行：.\scripts\运行Mplus分析.ps1 bootstrap --yes"
    }
    exit 2
}

$env:PYTHONPATH = (Join-Path $Root "runtime") + ";" + $env:PYTHONPATH
& $Python -m mplusflow @args
exit $LASTEXITCODE
