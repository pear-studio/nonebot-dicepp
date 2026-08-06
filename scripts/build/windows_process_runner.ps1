function Get-DicePPProcessOutput {
    param([Parameter(Mandatory = $true)][string]$Path)

    $value = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
    if ($null -eq $value) {
        return ""
    }
    return $value.Trim()
}

function ConvertTo-DicePPWindowsCommandLineArgument {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Argument)

    if ($Argument.Length -gt 0 -and $Argument -notmatch '[\s"]') {
        return $Argument
    }

    # Start-Process ultimately supplies one native Windows command line. Encode
    # each argv element using the CommandLineToArgvW backslash/quote rules.
    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Argument.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes += 1
            continue
        }
        if ($character -eq '"') {
            if ($backslashes -gt 0) {
                [void]$builder.Append(('\' * ($backslashes * 2)))
            }
            [void]$builder.Append('\"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * ($backslashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Write-DicePPProcessDiagnostics {
    param(
        [Parameter(Mandatory = $true)][string]$Scenario,
        [System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$Reason,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Stdout,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Stderr,
        [Parameter(Mandatory = $true)][long]$DurationMs,
        [ValidateSet("success", "failure")][string]$Outcome = "failure",
        [string]$DiagnosticsRoot = ""
    )

    if (-not $DiagnosticsRoot) {
        return
    }
    $root = [System.IO.Path]::GetFullPath($DiagnosticsRoot)
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $safeScenario = $Scenario -replace '[^A-Za-z0-9_.-]', '_'
    if (-not $safeScenario) {
        $safeScenario = "unnamed"
    }
    $pidValue = if ($null -eq $Process) { $null } else { $Process.Id }
    $exitCode = $null
    if ($null -ne $Process -and $Process.HasExited) {
        $exitCode = $Process.ExitCode
    }
    $payload = [ordered]@{
        contract_version = 1
        scenario = $Scenario
        reason = $Reason
        process_id = $pidValue
        exit_code = $exitCode
        duration_ms = $DurationMs
        captured_at_utc = [DateTimeOffset]::UtcNow.ToString("O")
    }
    $json = $payload | ConvertTo-Json -Depth 3
    [System.IO.File]::WriteAllText(
        (Join-Path $root "$safeScenario.$Outcome.json"),
        "$json`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    if ($Outcome -eq "success") {
        return
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $root "$safeScenario.stdout.txt"),
        "$Stdout`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $root "$safeScenario.stderr.txt"),
        "$Stderr`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    try {
        $allProcesses = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        $ownedIds = [System.Collections.Generic.HashSet[uint32]]::new()
        if ($null -ne $pidValue) {
            [void]$ownedIds.Add([uint32]$pidValue)
        }
        do {
            $added = $false
            foreach ($candidate in $allProcesses) {
                if ($ownedIds.Contains([uint32]$candidate.ParentProcessId) -and
                    $ownedIds.Add([uint32]$candidate.ProcessId)) {
                    $added = $true
                }
            }
        } while ($added)
        $ownedProcesses = @(
            $allProcesses |
                Where-Object { $ownedIds.Contains([uint32]$_.ProcessId) } |
                Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine
        )
        $treeJson = ConvertTo-Json -InputObject $ownedProcesses -Depth 3
        [System.IO.File]::WriteAllText(
            (Join-Path $root "$safeScenario.process-tree.json"),
            "$treeJson`n",
            [System.Text.UTF8Encoding]::new($false)
        )
    } catch {
        [System.IO.File]::WriteAllText(
            (Join-Path $root "$safeScenario.process-tree-error.txt"),
            "$($_.Exception.Message)`n",
            [System.Text.UTF8Encoding]::new($false)
        )
    }
}

function Stop-DicePPProcessTree {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$Scenario
    )

    if ($Process.HasExited) {
        return
    }
    $taskkillExitCode = -1
    $taskkill = $null
    try {
        $taskkill = Start-Process `
            -FilePath "taskkill.exe" `
            -ArgumentList @("/PID", $Process.Id, "/T", "/F") `
            -PassThru `
            -WindowStyle Hidden
        if ($taskkill.WaitForExit(15000)) {
            $taskkillExitCode = $taskkill.ExitCode
        } else {
            $taskkill.Kill()
            $taskkill.WaitForExit()
        }
    } catch {
        $taskkillExitCode = -1
    } finally {
        if ($null -ne $taskkill) {
            $taskkill.Dispose()
        }
    }
    $Process.Refresh()
    if ($taskkillExitCode -ne 0 -and -not $Process.HasExited) {
        try {
            $Process.Kill($true)
        } catch {
            throw "Failed to terminate process tree for scenario '$Scenario': $($_.Exception.Message)"
        }
    }
    if (-not $Process.WaitForExit(10000)) {
        throw "Process tree for scenario '$Scenario' did not exit after forced termination"
    }
}

function Invoke-DicePPProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [ValidateRange(1, 3600)][int]$TimeoutSeconds = 60,
        [Parameter(Mandatory = $true)][string]$Scenario,
        [string]$DiagnosticsRoot = ""
    )

    $resolvedPath = (Resolve-Path -LiteralPath $FilePath).Path
    $stdoutPath = New-TemporaryFile
    $stderrPath = New-TemporaryFile
    $process = $null
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $nativeArgumentList = @(
            $Arguments | ForEach-Object {
                ConvertTo-DicePPWindowsCommandLineArgument -Argument $_
            }
        ) -join ' '
        $process = Start-Process `
            -FilePath $resolvedPath `
            -ArgumentList $nativeArgumentList `
            -PassThru `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -WindowStyle Hidden
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $stdout = Get-DicePPProcessOutput -Path $stdoutPath
            $stderr = Get-DicePPProcessOutput -Path $stderrPath
            Write-DicePPProcessDiagnostics `
                -Scenario $Scenario `
                -Process $process `
                -Reason "timeout" `
                -Stdout $stdout `
                -Stderr $stderr `
                -DurationMs $stopwatch.ElapsedMilliseconds `
                -DiagnosticsRoot $DiagnosticsRoot
            Stop-DicePPProcessTree -Process $process -Scenario $Scenario
            throw "Scenario '$Scenario' timed out after $TimeoutSeconds seconds. stdout: '$stdout' stderr: '$stderr'"
        }
        # A second parameterless wait flushes redirected asynchronous streams.
        $process.WaitForExit()
        $stdout = Get-DicePPProcessOutput -Path $stdoutPath
        $stderr = Get-DicePPProcessOutput -Path $stderrPath
        $stopwatch.Stop()
        if ($process.ExitCode -ne 0) {
            Write-DicePPProcessDiagnostics `
                -Scenario $Scenario `
                -Process $process `
                -Reason "exit-code" `
                -Stdout $stdout `
                -Stderr $stderr `
                -DurationMs $stopwatch.ElapsedMilliseconds `
                -DiagnosticsRoot $DiagnosticsRoot
            throw "Scenario '$Scenario' exited $($process.ExitCode). stdout: '$stdout' stderr: '$stderr'"
        }
        Write-DicePPProcessDiagnostics `
            -Scenario $Scenario `
            -Process $process `
            -Reason "success" `
            -Stdout $stdout `
            -Stderr $stderr `
            -DurationMs $stopwatch.ElapsedMilliseconds `
            -Outcome "success" `
            -DiagnosticsRoot $DiagnosticsRoot
        return $stdout
    } finally {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-DicePPProcessTree -Process $process -Scenario $Scenario
        }
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }
}
