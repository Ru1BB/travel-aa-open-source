param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$')]
    [string]$ServerHost,
    [ValidatePattern('^[A-Za-z_][A-Za-z0-9_-]{0,31}$')]
    [string]$SshUser = 'root',
    [ValidateRange(1024,65535)]
    [int]$BackendPort = 5182,
    [string]$KeyPath = $env:TRAVEL_AA_SSH_KEY
)
$ErrorActionPreference = 'Stop'
$knownHosts = Join-Path $PSScriptRoot 'known_hosts'
if (-not $KeyPath -or -not (Test-Path -LiteralPath $KeyPath)) { throw 'SSH key not found.' }
if (-not (Test-Path -LiteralPath $knownHosts)) { throw 'Verified deploy/known_hosts is required. Do not disable host key checking.' }
Write-Host 'Keep this terminal open. Open http://localhost:5182/ and set your administrator account yourself.'
Write-Host 'After setup, press Ctrl+C here. Use your configured HTTPS site for normal login.'
& ssh -N -i $KeyPath -o BatchMode=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$knownHosts" -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L "127.0.0.1:5182:127.0.0.1:$BackendPort" "$SshUser@$ServerHost"
if ($LASTEXITCODE -ne 0) { throw "SSH tunnel exited: $LASTEXITCODE" }
