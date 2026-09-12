[CmdletBinding()]
param(
    [switch]$Lan
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-RequiredCommand {
    param(
        [Parameter(Mandatory)]
        [string[]]$Names,

        [Parameter(Mandatory)]
        [string]$InstallHint
    )

    foreach ($name in $Names) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1

        if ($null -ne $command) {
            return $command.Source
        }
    }

    throw "Required command '$($Names[0])' was not found in PATH. $InstallHint"
}

function Get-LikelyLanIPv4 {
    try {
        $configurations = @(Get-NetIPConfiguration -ErrorAction Stop | Where-Object {
            $null -ne $_.NetAdapter -and
            $_.NetAdapter.Status -eq "Up" -and
            $null -ne $_.IPv4Address
        })
    }
    catch {
        return $null
    }

    $candidates = foreach ($configuration in $configurations) {
        foreach ($address in @($configuration.IPv4Address)) {
            $ip = $address.IPAddress
            if (
                [string]::IsNullOrWhiteSpace($ip) -or
                $ip.StartsWith("127.") -or
                $ip.StartsWith("169.254.")
            ) {
                continue
            }

            $isPhysicalLan = $configuration.InterfaceAlias -match "^(Wi-Fi|WiFi|Wireless|Ethernet)(\s|$)"
            $hasGateway = $null -ne $configuration.IPv4DefaultGateway
            $priority = if ($isPhysicalLan -and $hasGateway) {
                0
            }
            elseif ($isPhysicalLan) {
                1
            }
            elseif ($hasGateway) {
                2
            }
            else {
                3
            }

            [PSCustomObject]@{
                Address  = $ip
                Priority = $priority
                Metric   = $configuration.NetIPInterface.InterfaceMetric
            }
        }
    }

    return $candidates |
        Sort-Object Priority, Metric |
        Select-Object -ExpandProperty Address -First 1
}

function Start-DevProcess {
    param(
        [Parameter(Mandatory)]
        [string]$Name,

        [Parameter(Mandatory)]
        [string]$Executable,

        [Parameter(Mandatory)]
        [string[]]$ArgumentList,

        [Parameter(Mandatory)]
        [string]$WorkingDirectory
    )

    Write-Host "Starting $Name..."
    $childProcess = Start-Process `
        -FilePath $Executable `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -NoNewWindow `
        -PassThru

    Start-Sleep -Milliseconds 300
    $childProcess.Refresh()
    if ($childProcess.HasExited) {
        throw "$Name exited during startup with code $($childProcess.ExitCode)."
    }

    return [PSCustomObject]@{
        Name    = $Name
        Process = $childProcess
    }
}

function Stop-DevProcessTree {
    param(
        [Parameter(Mandatory)]
        [PSCustomObject]$Child
    )

    $childProcess = $Child.Process
    try {
        $childProcess.Refresh()
        if ($childProcess.HasExited) {
            return
        }

        Write-Host "Stopping $($Child.Name)..."
        & "$env:SystemRoot\System32\taskkill.exe" /PID $childProcess.Id /T /F 2>$null |
            Out-Null
        $null = $childProcess.WaitForExit(5000)
    }
    catch {
        Write-Warning "Could not fully stop $($Child.Name): $($_.Exception.Message)"
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDirectory = Join-Path $repoRoot "backend"
$frontendDirectory = Join-Path $repoRoot "frontend"

if (-not (Test-Path (Join-Path $backendDirectory "pyproject.toml") -PathType Leaf)) {
    throw "Backend project not found at '$backendDirectory'."
}

if (-not (Test-Path (Join-Path $frontendDirectory "package.json") -PathType Leaf)) {
    throw "Frontend project not found at '$frontendDirectory'."
}

$uv = Resolve-RequiredCommand -Names @("uv.exe", "uv") `
    -InstallHint "Install uv and open a new terminal before running this script again."
$npm = Resolve-RequiredCommand -Names @("npm.cmd", "npm.exe", "npm") `
    -InstallHint "Install Node.js (which includes npm) and open a new terminal before running this script again."

$bindAddress = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }
$children = [System.Collections.Generic.List[object]]::new()

Write-Host "Psychology Roulette development environment"
Write-Host "Bind address: $bindAddress"
Write-Host "Frontend: http://127.0.0.1:5173"
Write-Host "API docs: http://127.0.0.1:8000/docs"

if ($Lan) {
    $lanAddress = Get-LikelyLanIPv4
    if ($null -ne $lanAddress) {
        Write-Host "Remote device: http://${lanAddress}:5173" -ForegroundColor Cyan
    }
    else {
        Write-Warning "No active Wi-Fi/Ethernet IPv4 address was detected. Run 'ipconfig' and use this computer's IPv4 address with port 5173."
    }
}

Write-Host "Press Ctrl+C to stop both servers."

try {
    $backend = Start-DevProcess `
        -Name "backend" `
        -Executable $uv `
        -ArgumentList @(
            "run",
            "uvicorn",
            "psychology_roulette.api:app",
            "--host",
            $bindAddress,
            "--port",
            "8000"
        ) `
        -WorkingDirectory $backendDirectory
    $children.Add($backend)

    $frontend = Start-DevProcess `
        -Name "frontend" `
        -Executable $npm `
        -ArgumentList @(
            "run",
            "dev",
            "--",
            "--host",
            $bindAddress,
            "--port",
            "5173",
            "--strictPort"
        ) `
        -WorkingDirectory $frontendDirectory
    $children.Add($frontend)

    while ($true) {
        foreach ($child in $children) {
            $child.Process.Refresh()
            if ($child.Process.HasExited) {
                throw "$($child.Name) exited unexpectedly with code $($child.Process.ExitCode)."
            }
        }

        Start-Sleep -Milliseconds 500
    }
}
finally {
    for ($index = $children.Count - 1; $index -ge 0; $index--) {
        Stop-DevProcessTree -Child $children[$index]
    }

    Write-Host "Development servers stopped."
}
