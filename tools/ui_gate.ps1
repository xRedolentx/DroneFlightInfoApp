# Run the browser gate in every mode it needs, against throwaway roots.
#
#   powershell -ExecutionPolicy Bypass -File tools\ui_gate.ps1
#
# Four passes, because the rules make one root insufficient:
#   1. demo root, no ZIP      the full UI regression (demo data provides tracks)
#   2. ordinary root, ZIP     the real import path (a demo root refuses it)
#   3. demo root, ZIP         that refusal, clicked, with its plain-language reason
#   4. ordinary root, forged  the "Review identity" flow on an unreadable log
#
# Each pass starts its own server and headless Edge on its own ports, and stops
# both. Nothing touches the owner's live data root. Passes run one at a time so a
# failure is attributable.
param(
    [string]$Base = (Join-Path $env:TEMP "fd-ui-gate"),
    [string]$Edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    [int]$FirstPort = 8820,
    [int]$FirstDebugPort = 9420
)

$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$results = @()
$port = $FirstPort
$debug = $FirstDebugPort

function Wait-Url([string]$url, [int]$tries = 80) {
    for ($i = 0; $i -lt $tries; $i++) {
        try {
            $r = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { return $true }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    return $false
}

function Invoke-Pass([string]$name, [string]$seed, [string]$zip, [string[]]$flags) {
    $script:port += 1
    $script:debug += 1
    $dir = Join-Path $Base $name
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $dir | Out-Null
    $root = Join-Path $dir "root"
    if ($seed -eq "demo") {
        python -m flightdesk seed-demo --root $root | Out-Null
    } else {
        New-Item -ItemType Directory -Force $root | Out-Null
    }
    $url = "http://127.0.0.1:$($script:port)/"
    $server = Start-Process python -ArgumentList @(
        "-m", "flightdesk", "serve", "--root", $root,
        "--port", "$($script:port)", "--no-browser") -PassThru -WindowStyle Hidden
    try {
        if (-not (Wait-Url $url)) { throw "server did not start for $name" }
        $browser = Start-Process $Edge -ArgumentList @(
            "--headless=new", "--remote-debugging-port=$($script:debug)",
            "--disable-extensions", "--disable-sync", "--no-first-run",
            "--user-data-dir=$(Join-Path $dir 'edge')", $url) -PassThru -WindowStyle Hidden
        try {
            $cdp = $false
            for ($i = 0; $i -lt 80; $i++) {
                try {
                    $null = Invoke-RestMethod "http://127.0.0.1:$($script:debug)/json/version" -TimeoutSec 2
                    $cdp = $true; break
                } catch { Start-Sleep -Milliseconds 500 }
            }
            if (-not $cdp) { throw "CDP did not open for $name" }
            $argList = @("tools\ui_check.mjs", "$($script:debug)",
                         (Join-Path $dir "shots"), $zip, $url) + $flags
            $out = & node @argList 2>&1
            $out | Tee-Object -FilePath (Join-Path $dir "gate.log") | Out-Null
            $code = $LASTEXITCODE
            $summary = ($out | Select-String -Pattern "SUMMARY_FINAL" | Select-Object -Last 1)
            $summaryLine = ""
            if ($summary) { $summaryLine = $summary.Line }
            if (-not $summaryLine) { $summaryLine = "no summary" }
            Write-Host ("[{0}] {1}" -f $name, $summaryLine)
            $script:results += [pscustomobject]@{ Pass = $name; Exit = $code; Line = $summaryLine }
        } finally {
            Stop-Process -Id $browser.Id -Force -ErrorAction SilentlyContinue
        }
    } finally {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}

$zipDir = Join-Path $Base "_fixtures"
New-Item -ItemType Directory -Force $zipDir | Out-Null
$handoff = Join-Path $zipDir "handoff.zip"
python tools\make_synthetic_handoff.py $handoff | Out-Null

# A v11 log whose details offset is zero: an unreadable original, kept as a
# record and offered for identity review.
$forge = Join-Path $zipDir "forge.py"
@'
import datetime as dt, sys
from pathlib import Path
sys.path.insert(0, ".")
from flightdesk.synth import make_log_bytes
data = bytearray(make_log_bytes(version=11, start=dt.datetime(2025, 5, 16, 12, 0, 0,
                                                             tzinfo=dt.timezone.utc)))
data[0:8] = b"\x00" * 8
Path(sys.argv[1]).write_bytes(bytes(data))
'@ | Set-Content -Path $forge -Encoding utf8
$unreadable = Join-Path $zipDir "unreadable.txt"
python $forge $unreadable | Out-Null

Invoke-Pass "demo-full" "demo" "-" @()
Invoke-Pass "ordinary-import" "ordinary" $handoff @("--import-only", "--ordinary")
Invoke-Pass "demo-refusal" "demo" $handoff @("--import-only", "--expect-refusal")
$env:FD_IDENTITY_FILE = $unreadable
try {
    Invoke-Pass "identity" "ordinary" "-" @("--identity-only")
} finally {
    Remove-Item Env:\FD_IDENTITY_FILE -ErrorAction SilentlyContinue
}

Write-Host ""
$results | Format-Table -AutoSize | Out-String | Write-Host
$failed = @($results | Where-Object { $_.Exit -ne 0 }).Count
if ($failed -gt 0) { exit 1 }
exit 0
