<#
.SYNOPSIS
  azd postprovision hook. Writes measure/.env from the azd outputs. Windows PowerShell 5.1 compatible.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Get-AzdValue {
    # azd writes "key not found" to stderr; under PS 5.1 with ErrorActionPreference=Stop that is a
    # terminating NativeCommandError, so probe with SilentlyContinue.
    param([string]$Name)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    $value = azd env get-value $Name 2>$null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($code -ne 0) { return '' }
    return $value
}

$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root 'measure\.env'
$lines = @()
foreach ($name in 'GATEWAY_URL', 'API_NAME', 'API_HOSTNAME', 'WORKERS_NAME', 'WORKERS_HOSTNAME', 'SERVICE_BUS_NAMESPACE',
                  'STORAGE_ACCOUNT', 'APPINSIGHTS_NAME', 'LOG_ANALYTICS_WORKSPACE_ID', 'APIM_NAME', 'AZURE_RESOURCE_GROUP') {
    $lines += ('{0}={1}' -f $name, (Get-AzdValue -Name $name))
}
Set-Content -Path $envFile -Value $lines -Encoding ASCII
Write-Host "Wrote $envFile"
Write-Host ''
Write-Host 'Provisioned. Next:'
Write-Host '  1. azd deploy'
Write-Host ('  2. Open {0}/   (console through the gateway)' -f (Get-AzdValue -Name 'GATEWAY_URL'))
Write-Host '  3. python measure\drive.py all'
Write-Host '  4. For the sampling experiment: azd env set TRACING_SAMPLING_RATIO 0.1; azd provision; python measure\drive.py burst 100'
