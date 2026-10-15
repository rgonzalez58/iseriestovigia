# Genera installer\dist\Instalar_etl_vigia_py.ps1: un solo .ps1 autocontenido (payload y ruedas embebidos en base64).
$ErrorActionPreference = 'Stop'
$inst = Split-Path -Parent $MyInvocation.MyCommand.Path
$pkg = Join-Path $inst 'build\pkg'
& (Join-Path $inst 'build.ps1') | Out-Null    # siempre reconstruye el payload con el codigo actual
$dist = Join-Path $inst 'dist'
New-Item -ItemType Directory -Force $dist | Out-Null
$out = Join-Path $dist 'Instalar_etl_vigia_py.ps1'

$cuerpo = Get-Content (Join-Path $inst 'instalar.ps1') -Raw
$fin = $cuerpo.IndexOf(')', $cuerpo.IndexOf('[switch]$SinWinget')) + 1      # fin del bloque param(...)
$param = $cuerpo.Substring(0, $fin)
$resto = $cuerpo.Substring($fin) -replace '(?m)^\$aqui = .*\r?\n', ''

$b64p = [Convert]::ToBase64String([IO.File]::ReadAllBytes("$pkg\payload.zip"))
$b64w = [Convert]::ToBase64String([IO.File]::ReadAllBytes("$pkg\wheels.zip"))

$cabecera = @'

# Instalador autocontenido de etl_vigia_py (sin bases de datos). Ejecutar desde PowerShell como administrador:
#   powershell -ExecutionPolicy Bypass -File .\Instalar_etl_vigia_py.ps1 [-Destino C:\ETL\etl_vigia_py] [-SinWinget]
$aqui = Join-Path $env:TEMP ('etl_vigia_setup_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $aqui | Out-Null
$esAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $esAdmin) { Write-Host 'AVISO: no es administrador; winget y la escritura en C:\ pueden fallar.' -ForegroundColor Yellow }
'@

$datos = "`n[IO.File]::WriteAllBytes((Join-Path `$aqui 'payload.zip'), [Convert]::FromBase64String('$b64p'))`n" +
         "[IO.File]::WriteAllBytes((Join-Path `$aqui 'wheels.zip'), [Convert]::FromBase64String('$b64w'))`n"

$final = "$param`n$cabecera$datos$resto`nRemove-Item `$aqui -Recurse -Force -ErrorAction SilentlyContinue`n"
Set-Content -Path $out -Value $final -Encoding UTF8
Get-Item $out | Select-Object FullName, @{n = 'KB'; e = { [int]($_.Length / 1KB) } }
