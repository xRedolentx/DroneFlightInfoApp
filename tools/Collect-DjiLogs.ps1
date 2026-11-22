param(
    [Parameter(Mandatory = $true)][string]$StagingRoot,
    [int]$TimeoutSeconds = 60,
    # Stop after this many copied files and report `truncated = true`. The app
    # then imports what arrived and the next scan continues with the rest, so a
    # controller with a large backlog makes progress in every pass instead of
    # losing a whole 200 s window at the end. 0 means no limit: a measured pass
    # over 259 logs takes 58 s, well inside the window, so the default changes
    # nothing for real hardware.
    [int]$MaxFilesPerRun = 0,
    # A JSON array of { name, size } that the archive already holds. Skipping
    # these before copying is not an optimisation: with a controller left
    # plugged in, the watcher rescans every 45 s, and without this each pass
    # would re-copy all 182 MB of a Smart Controller.
    [string]$SkipListPath = ''
)

$ErrorActionPreference = 'Stop'
$result = [ordered]@{ status = 'ok'; devices = @(); errors = @(); skipped_known = 0; truncated = $false }
$files = New-Object System.Collections.ArrayList
# Two keys, and the pair is what decides. A name in the list triggers ONE size
# read; the file is skipped only when that exact name+size is already held. A
# name alone is not proof of an identical copy, and skipping on it meant an
# incomplete transfer on the controller could never be replaced.
$skipNames = @{}
$skipPairs = @{}
if ($SkipListPath -and (Test-Path -LiteralPath $SkipListPath)) {
    try {
        # Assign first, then iterate. `@($raw | ConvertFrom-Json)` in Windows
        # PowerShell 5.1 wraps the whole emitted array into ONE element, so the
        # loop ran once with `$e.name` being a list of every name joined by
        # spaces - a skip list that matched nothing, silently. Measured: 418
        # names loaded, 0 skipped, 259 files re-copied.
        $parsed = Get-Content -LiteralPath $SkipListPath -Raw | ConvertFrom-Json
        foreach ($e in $parsed) {
            if ($null -eq $e) { continue }
            $entryName = [string]$e.name
            if (-not $entryName) { continue }
            $skipNames[$entryName] = $true
            if ($null -ne $e.size) { $skipPairs["$entryName|$([int64]$e.size)"] = $true }
        }
    } catch {
        $result.errors += @{ device = 'collector'; code = 'skip_list_unreadable' }
    }
}
$seen = @{}
$foundDevice = $false
$seenDevices = New-Object System.Collections.ArrayList

function Get-MinimumPlausibleLength([string]$path) {
    # A copy that stops growing is only finished if it is long enough to hold
    # the data its own header points at. A v6-v12 log stores a details offset
    # and length in the first 10 bytes; a v13+ log keeps an auxiliary block at
    # byte 100. Without this, a transfer that stalled for 0.7 s was accepted as
    # complete and saved truncated — the file looked stable because it was dead.
    try {
        $fs = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
        try {
            $buf = New-Object byte[] 128
            $n = $fs.Read($buf, 0, 128)
            if ($n -lt 12) { return 0 }
            $version = [int]$buf[10]
            if ($version -ge 13) {
                if ($n -lt 103) { return 0 }
                $infoSize = [int]([BitConverter]::ToUInt16($buf, 101))
                return [int64](100 + 1 + 2 + 1 + $infoSize)
            }
            $offset = [int64]([BitConverter]::ToUInt64($buf, 0))
            $dlen = [int64]([BitConverter]::ToUInt16($buf, 8))
            return ($offset + $dlen)
        } finally { $fs.Dispose() }
    } catch { return 0 }
}

function Get-ItemSize($item) {
    # An MTP item reports .Size = 0, not $null. Taking that as a real size made
    # every copied log look like a size mismatch and get rejected — measured on a
    # Smart Controller holding 250 logs (ExtendedProperty reported 968945 bytes
    # where .Size said 0). A zero here is "unknown", never a claim.
    try {
        if ($null -ne $item.Size -and [int64]$item.Size -gt 0) { return [int64]$item.Size }
    } catch { }
    try {
        $v = $item.ExtendedProperty('System.Size')
        if ($null -ne $v -and [int64]$v -gt 0) { return [int64]$v }
    } catch { }
    return $null
}

function Get-Folder($item) {
    # Shell.Application hands out FolderItems from a parent Folder. A FolderItem
    # has no Items(); .GetFolder is what yields the Folder. Passing the wrong one
    # is exactly how this collector silently reported "no files" with 250 logs
    # attached: the throw was caught, no error recorded, and the loop moved on.
    if ($null -eq $item) { return $null }
    try { if ($item.IsFolder) { return $item.GetFolder } } catch { }
    return $item
}

function Find-Subfolder($start, [string[]]$segments) {
    $cur = Get-Folder $start
    if ($null -eq $cur) { return $null }
    foreach ($seg in $segments) {
        $next = @($cur.Items() | Where-Object { $_.Name -eq $seg } | Select-Object -First 1)
        if ($next.Count -eq 0) { return $null }
        $cur = Get-Folder $next[0]
        if ($null -eq $cur) { return $null }
    }
    return $cur
}

Add-Type -AssemblyName System.Windows.Forms | Out-Null

try {
    $shell = New-Object -ComObject Shell.Application
    $pc = $shell.NameSpace(17)
    $deviceItems = @($pc.Items() | Where-Object {
        $_.Name -match 'Smart Controller' -or $_.Name -match 'DJI RC 2'
    })
    foreach ($device in $deviceItems) {
        $foundDevice = $true
        $deviceName = $device.Name
        # After the assignment, not before: the first version added $deviceName
        # while it was still $null, so the collector reported no devices at all
        # after reading one successfully.
        if (-not $seenDevices.Contains($deviceName)) { [void]$seenDevices.Add($deviceName) }
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
            $storageFolder = Get-Folder $storage
            if ($null -eq $storageFolder) {
                $result.errors += @{ device = $deviceName; code = 'storage_unreadable' }
                continue
            }
            foreach ($target in $targets) {
                $folder = $null
                try { $folder = Find-Subfolder $storageFolder $target.segments }
                catch {
                    $result.errors += @{ device = $deviceName; code = 'folder_lookup_failed' }
                    $folder = $null
                }
                if ($null -eq $folder) { continue }
                $items = @()
                try { $items = @($folder.Items()) } catch {
                    $result.errors += @{ device = $deviceName; code = 'folder_enumeration_failed' }
                    continue
                }
                $consecutiveFailures = 0
                $abort = $false
                foreach ($item in $items) {
                    $name = [string]$item.Name
                    if (-not ($name -like 'DJIFlightRecord_*' -or $name -like 'FlightRecord_*')) {
                        continue
                    }
                    # The same volume can appear as two storage handles on one
                    # controller (measured on an RC 2: two "Internal shared
                    # storage" entries, 17 logs each). Counting a file twice makes
                    # "scanned" read 34 for 17 logs. Dedupe by name per device
                    # before anything else is counted.
                    $identity = "$deviceName|$name"
                    if ($seen.ContainsKey($identity)) { continue }
                    $seen[$identity] = $true
                    if ($skipNames.ContainsKey($name)) {
                        # Already archived under this name — but a name is not a
                        # copy. Read the size and skip only if this exact
                        # name+size is held: an incomplete copy on the
                        # controller was previously skipped forever, because
                        # the name matched and the size was never asked for.
                        $size = Get-ItemSize $item
                        if ($null -ne $size -and $skipPairs.ContainsKey("$name|$size")) {
                            $result.skipped_known = [int]$result.skipped_known + 1
                            continue
                        }
                        if ($null -eq $size) {
                            # The device will not report a size, so this cannot be
                            # proven equal to the archived copy. Copy it: the
                            # importer dedupes by content hash, which is cheaper
                            # than silently keeping a truncated file.
                            $result.errors += @{ device = $deviceName; name = $name; code = 'size_unreadable_recopy' }
                        }
                    } else {
                        $size = Get-ItemSize $item
                    }
                    $key = "$deviceName|$name|$size"
                    if ($seen.ContainsKey($key)) { continue }                    $seen[$key] = $true
                    $dest = Join-Path $StagingRoot ([guid]::NewGuid().ToString('N'))
                    New-Item -ItemType Directory -Force -Path $dest | Out-Null
                    # CopyHere copies INTO the Folder it is called on. Calling it
                    # on the source folder — as this collector did — is a silent
                    # no-op: it returns in ~20 ms and nothing is written. Measured
                    # on a Smart Controller: 0 bytes staged after 800 s. The
                    # destination must be a Folder object for the new directory.
                    $destFolder = $shell.NameSpace($dest)
                    if ($null -eq $destFolder) {
                        $result.errors += @{ device = $deviceName; name = $name; code = 'staging_folder_unavailable' }
                        continue
                    }
                    try {
                        $destFolder.CopyHere($item, 0x14)
                    } catch {
                        $result.errors += @{ device = $deviceName; name = $name; code = 'copy_start_failed' }
                        continue
                    }
                    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
                    $copied = $null
                    $unverified = $false
                    $stableLen = -1
                    $stableSince = Get-Date
                    while ((Get-Date) -lt $deadline) {
                        # CopyHere is asynchronous and needs its thread to pump
                        # messages; without this the transfer never advances.
                        try { [System.Windows.Forms.Application]::DoEvents() } catch { }
                        Start-Sleep -Milliseconds 150
                        $candidates = @(Get-ChildItem -LiteralPath $dest -File -ErrorAction SilentlyContinue)
                        if ($candidates.Count -eq 0) { continue }
                        $candidate = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
                        if ($null -ne $size) {
                            # The size is known, so the copy is finished when it
                            # matches. Without this the collector waited two
                            # seconds per file to prove stability, which on a
                            # 250-log controller is ten minutes for nothing.
                            try { $candidate.Refresh() } catch { }
                            if ($candidate.Length -eq $size) { $copied = $candidate; break }
                            continue
                        }
                        # Size unknown: require the length to stop changing for
                        # three seconds AND to be long enough to hold the block
                        # its own header points at.
                        try { $candidate.Refresh() } catch { }
                        $lenNow = [int64]$candidate.Length
                        if ($lenNow -ne $stableLen) {
                            $stableLen = $lenNow
                            $stableSince = Get-Date
                        }
                        if ($stableLen -gt 0 -and ((Get-Date) - $stableSince).TotalSeconds -ge 3) {
                            $minLen = Get-MinimumPlausibleLength $candidate.FullName
                            if ($minLen -gt 0 -and $stableLen -ge $minLen) {
                                $copied = $candidate
                                break
                            }
                            # Stable but too short to contain its own details
                            # block: a stalled copy, not a finished one.
                            $unverified = $true
                            break
                        }
                    }
                    if ($null -eq $copied) {
                        if ($unverified -or ($null -eq $size -and $stableLen -gt 0)) {
                            # The device would not report a size and the staged
                            # bytes cannot be shown to be complete. Say so rather
                            # than hand the archive a truncated file.
                            $result.errors += @{ device = $deviceName; name = $name; code = 'copy_unverified' }
                        } else {
                            $result.errors += @{ device = $deviceName; name = $name; code = 'copy_timeout_or_empty' }
                        }
                        $consecutiveFailures++
                        # One stuck copy must not cost 60 s x 250 files. Three in
                        # a row means the transfer path itself is broken, and the
                        # honest answer is to stop and say so.
                        if ($consecutiveFailures -ge 3) {
                            $result.errors += @{ device = $deviceName; code = 'copy_not_progressing' }
                            $abort = $true
                            break
                        }
                        continue
                    }
                    $consecutiveFailures = 0
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
                    if ($abort) { break }
                    if ($MaxFilesPerRun -gt 0 -and $files.Count -ge $MaxFilesPerRun) {
                        # Bounded progress: report what was copied and let the
                        # next pass take the rest.
                        $result.truncated = $true
                        $abort = $true
                        break
                    }
                }
                if ($abort) { break }
            }
            if ($abort) { break }
        }
        if ($abort) { break }
    }
} catch {
    $result.errors += @{ device = 'collector'; code = 'collector_exception' }
    $result.status = 'error'
}

if (-not $foundDevice) {
    $result.status = 'none'
} elseif ($files.Count -eq 0 -and [int]$result.skipped_known -eq 0) {
    $result.status = 'no_files'
} elseif ($files.Count -eq 0) {
    # The device holds logs; all of them are already archived.
    $result.status = 'all_known'
}
$result.devices = @($seenDevices | Sort-Object -Unique)
$result | Add-Member -NotePropertyName files -NotePropertyValue @($files)
$result | ConvertTo-Json -Depth 6 -Compress
