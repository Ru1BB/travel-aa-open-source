param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$')]
    [string]$ServerHost,
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$')]
    [string]$PublicHost,
    [ValidatePattern('^[A-Za-z_][A-Za-z0-9_-]{0,31}$')]
    [string]$SshUser = 'root',
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')]
    [string]$CertificateName = 'travel-aa-ip',
    [ValidateRange(1024,65535)]
    [int]$BackendPort = 5182,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')]
    [string]$ReleaseName = ([DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-web'),
    [string]$KeyPath = $env:TRAVEL_AA_SSH_KEY
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$knownHosts = Join-Path $PSScriptRoot 'known_hosts'
if (-not (Test-Path -LiteralPath $knownHosts)) { throw 'Verified deploy/known_hosts is required.' }
if (-not $KeyPath -or -not (Test-Path -LiteralPath $KeyPath)) { throw 'SSH key not found.' }
function Invoke-Native([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed: $LASTEXITCODE" }
}
$sshOptions = @('-i',$KeyPath,'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o',"UserKnownHostsFile=$knownHosts")
$sshTarget = "$SshUser@$ServerHost"
$archive = Join-Path $projectRoot 'verification\travel-aa-release.tar.gz'
$releaseDir = "/srv/travel-aa/releases/$ReleaseName"
Push-Location $projectRoot
try {
    $env:PYTHONUTF8='1'
    Invoke-Native 'python' @('-m','unittest','test_backend')
    Invoke-Native 'python' @('-m','unittest','discover','-s','deploy','-p','test_*.py')
    Invoke-Native 'node' @('--test')
    Invoke-Native 'node' @('--check','app.js')
    Invoke-Native 'python' @('deploy/package_release.py',$archive)
    Invoke-Native 'ssh' ($sshOptions + @($sshTarget,"test ! -e '$releaseDir' && mkdir -m 755 '$releaseDir'"))
    Invoke-Native 'scp' ($sshOptions + @($archive,"${sshTarget}:/srv/travel-aa/releases/$ReleaseName.tar.gz"))
    Invoke-Native 'ssh' ($sshOptions + @($sshTarget,"tar -xzf '/srv/travel-aa/releases/$ReleaseName.tar.gz' -C '$releaseDir' && cd '$releaseDir' && python3 -m unittest test_backend && bash deploy/activate.sh '$releaseDir' '$PublicHost' '$CertificateName' '$BackendPort'"))
    Write-Host "Manual deployment complete. Verify https://$PublicHost/ before using real bills."
} finally { Pop-Location }
