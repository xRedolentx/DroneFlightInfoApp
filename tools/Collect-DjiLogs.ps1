param(
    [Parameter(Mandatory = $true)][string]$StagingRoot,
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = 'Stop'
$result = [ordered]@{ status = 'ok'; devices = @(); errors = @() }
$files = New-Object System.Collections.ArrayList
$seen = @{}
$foundDevice = $false

function Get-ItemSize($item) {
    try {
        if ($null -ne $item.Size) { return [int64]$item.Size }
    } catch { }
    try {
        $v = $item.ExtendedProperty('System.Size')
        if ($null -ne $v) { return [int64]$v }
    } catch { }
    return $null
}

function Find-Subfolder($start, [string[]]$segments) {
    $cur = $start
    foreach ($seg in $segments) {
        $next = @($cur.Items() | Where-Object { $_.Name -eq $seg } | Select-Object -First 1)
        if ($next.Count -eq 0) { return $null }
        try { $cur = $next[0].GetFolder } catch { return $null }
    }
    return $cur
}

try {
    $shell = New-Object -ComObject Shell.Application
    $pc = $shell.NameSpace(17)
    $deviceItems = @($pc.Items() | Where-Object {
        $_.Name -match 'Smart Controller' -or $_.Name -match 'DJI RC 2'
    })
    foreach ($device in $deviceItems) {
        $foundDevice = $true
        $deviceName = $device.Name
        $storages = @()
        try { $storages = @($device.GetFolder.Items()) } catch {
            $result.errors += @{ device = $deviceName; code = 'storage_enumeration_failed' }
            continue
        }
        $targets = @(
            @{ label = 'Smart Controller'; segments = @('DJI', 'dji.go.v4', 'FlightRecord') },
            @{ label = 'DJI RC 2'; segments = @('Android', 'data', 'dji.go.v5', 'files', 'FlightRecord') }
        )
        foreach ($storage in $storages) {
            foreach ($target in $targets) {
                $folder = $null
                try { $folder = Find-Subfolder $storage $target.segments } catch { $folder = $null }
                if ($null -eq $folder) { continue }
                $items = @()
                try { $items = @($folder.Items()) } catch {
                    $result.errors += @{ device = $deviceName; code = 'folder_enumeration_failed' }
                    continue
                }
                foreach ($item in $items) {
                    $name = [string]$item.Name
                    if (-not ($name -like 'DJIFlightRecord_*' -or $name -like 'FlightRecord_*')) {
                        continue
                    }
                    $size = Get-ItemSize $item
                    $key = "$deviceName|$name|$size"
                    if ($seen.ContainsKey($key)) { continue }
                    $seen[$key] = $true
                    $dest = Join-Path $StagingRoot ([guid]::NewGuid().ToString('N'))
                    New-Item -ItemType Directory -Force -Path $dest | Out-Null
                    try {
                        $folder.CopyHere($item, 0x14)
                    } catch {
                        $result.errors += @{ device = $deviceName; name = $name; code = 'copy_start_failed' }
                        continue
                    }
                    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
                    $copied = $null
                    while ((Get-Date) -lt $deadline) {
                        Start-Sleep -Milliseconds 500
                        $candidates = @(Get-ChildItem -LiteralPath $dest -File -ErrorAction SilentlyContinue)
                        if ($candidates.Count -eq 0) { continue }
                        $candidate = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
                        $first = $candidate.Length
                        Start-Sleep -Seconds 2
                        try { $candidate.Refresh() } catch { }
                        if ($candidate.Length -eq $first -and $candidate.Length -gt 0) {
                            $copied = $candidate
                            break
                        }
                    }
                    if ($null -eq $copied) {
                        $result.errors += @{ device = $deviceName; name = $name; code = 'copy_timeout_or_empty' }
                        continue
                    }
                    if ($null -ne $size -and $copied.Length -ne $size) {
                        $result.errors += @{ device = $deviceName; name = $name; code = 'size_mismatch' }
                        continue
                    }
                    $hash = (Get-FileHash -LiteralPath $copied.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                    [void]$files.Add([ordered]@{
                        device = $deviceName
                        target = $target.label
                        storage = [string]$storage.Name
                        source_name = $name
                        staged_path = $copied.FullName
                        size = [int64]$copied.Length
                        sha256 = $hash
                    })
                }
            }
        }
    }
} catch {
    $result.errors += @{ device = 'collector'; code = 'collector_exception' }
    $result.status = 'error'
}

if (-not $foundDevice) {
    $result.status = 'none'
} elseif ($files.Count -eq 0) {
    $result.status = 'no_files'
}
$result | Add-Member -NotePropertyName files -NotePropertyValue @($files)
$result | ConvertTo-Json -Depth 6 -Compress
