# Instalador de etl_vigia_py (sin bases de datos). Se ejecuta desde Setup_etl_vigia_py.exe o directamente.
# Uso: powershell -ExecutionPolicy Bypass -File instalar.ps1 [-Destino C:\ETL\etl_vigia_py] [-SinWinget] [-SinPanel] [-PuertoPanel 8765] [-QuitarPanel]
param(
    [string]$Destino = 'C:\ETL\etl_vigia_py',
    [int]$PuertoPanel = 8765,
    [switch]$SinPanel,
    [switch]$QuitarPanel,
    [switch]$SinWinget
)
$ErrorActionPreference = 'Stop'
$aqui = Split-Path -Parent $MyInvocation.MyCommand.Path

$tareaPanel = 'ETL_VIGIA_Panel'
function Es-Admin { ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) }
function Detener-Panel {
    if (Get-ScheduledTask -TaskName $tareaPanel -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $tareaPanel -ErrorAction SilentlyContinue }
}

if ($QuitarPanel) {
    Detener-Panel
    Unregister-ScheduledTask -TaskName $tareaPanel -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Tarea $tareaPanel eliminada"
    return
}

function Paso($m) { Write-Host "`n== $m" -ForegroundColor Cyan }
function Aviso($m) { Write-Host "AVISO: $m" -ForegroundColor Yellow }

function Buscar-Python {
    foreach ($c in @(@('py', '-3'), @('python'))) {
        $exe = Get-Command $c[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        try {
            $args2 = @() + $c[1..($c.Count - 1)] + @('-c', 'import sys;print(sys.version_info[0]*100+sys.version_info[1]);print(sys.executable)')
            $out = & $c[0] @args2 2>$null
            if ($LASTEXITCODE -eq 0 -and [int]$out[0] -ge 310 -and $out[1] -notmatch 'WindowsApps') { return $out[1] }
        } catch { }
    }
    return $null
}

Paso 'Python 3.10+'
$py = Buscar-Python
if (-not $py -and -not $SinWinget -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host 'Instalando Python 3.12 con winget...'
    winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    $py = Buscar-Python
}
if (-not $py) { throw 'No se encontro Python 3.10 o superior. Instale Python desde python.org (64 bits) y repita.' }
Write-Host "Python: $py"
& $py -c "import sys,struct;print('Version', sys.version.split()[0], '-', struct.calcsize('P')*8, 'bits')"

Detener-Panel      # libera los archivos del entorno virtual si el panel estaba en marcha
Paso "Copiando programa a $Destino"
$zip = Join-Path $aqui 'payload.zip'
if (-not (Test-Path $zip)) { throw "Falta payload.zip junto al instalador" }
$tmp = Join-Path $env:TEMP ('etl_vigia_' + [guid]::NewGuid().ToString('N'))
Expand-Archive -Path $zip -DestinationPath $tmp -Force
New-Item -ItemType Directory -Force $Destino | Out-Null
# .env y config.local.yaml (ajustes propios del equipo) se conservan siempre. config.yaml es de la version y se
# reemplaza; si el existente es distinto queda respaldado como config.yaml.bak_<fecha>.
$dst = Join-Path $Destino 'config.yaml'
if (Test-Path $dst) {
    $h1 = (Get-FileHash $dst).Hash; $h2 = (Get-FileHash (Join-Path $tmp 'config.yaml')).Hash
    if ($h1 -ne $h2) {
        $bak = "$dst.bak_" + (Get-Date -Format 'yyyyMMdd_HHmmss')
        Copy-Item $dst $bak -Force
        Write-Host "config.yaml anterior respaldado en $bak (se instala el config.yaml correcto)"
    }
}
Copy-Item -Path (Join-Path $tmp '*') -Destination $Destino -Recurse -Force
$wz = Join-Path $aqui 'wheels.zip'
if (Test-Path $wz) { Expand-Archive -Path $wz -DestinationPath $Destino -Force }
Remove-Item $tmp -Recurse -Force
New-Item -ItemType Directory -Force (Join-Path $Destino 'logs') | Out-Null

Paso 'Entorno virtual y dependencias (pyyaml, pyodbc)'
$venv = Join-Path $Destino '.venv'
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) { & $py -m venv $venv; if ($LASTEXITCODE) { throw 'Fallo al crear el entorno virtual' } }
$vpy = Join-Path $venv 'Scripts\python.exe'
$wh = Join-Path $Destino 'wheels'
$req = Join-Path $Destino 'requirements.txt'
$ErrorActionPreference = 'Continue'
& $vpy -m ensurepip --upgrade 2>&1 | Out-Null
& $vpy -m pip --version 2>&1 | Out-Host
& $vpy -m pip install --no-index --find-links $wh -r $req --disable-pip-version-check 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) {
    Aviso 'Instalacion sin conexion fallo; reintentando desde PyPI...'
    Write-Host ('Ruedas disponibles: ' + ((Get-ChildItem $wh -Filter *.whl -ErrorAction SilentlyContinue | Measure-Object).Count))
    & $vpy -c "import sys,struct;print('Python', sys.version.split()[0], struct.calcsize('P')*8, 'bits')" | Out-Host
    & $vpy -m pip install -r $req --disable-pip-version-check 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar pyyaml/pyodbc: la version/arquitectura de Python no tiene ruedas incluidas y no hay acceso a PyPI. Use Python 3.10-3.13 de 64 bits (python.org) o habilite internet.' }
}
$ErrorActionPreference = 'Stop'

Paso 'Controladores ODBC'
$drv = Get-OdbcDriver -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'ODBC Driver (17|18) for SQL Server' }
if (-not $drv -and -not $SinWinget -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host 'Instalando Microsoft ODBC Driver 17 for SQL Server...'
    winget install --id Microsoft.msodbcsql.17 -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    $drv = Get-OdbcDriver -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'ODBC Driver (17|18) for SQL Server' }
}
if ($drv) { Write-Host ('SQL Server: ' + (($drv | Select-Object -Expand Name) -join ', ')) }
else { Aviso 'No hay ODBC Driver 17/18 for SQL Server. Instalelo (o ajuste DESTINO_DRIVER en .env).' }
$ibm = Get-OdbcDriver -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'iSeries|IBM i Access|Client Access' }
if ($ibm) { Write-Host ('iSeries: ' + (($ibm | Select-Object -Expand Name) -join ', ')) }
else { Aviso 'No se detecto el driver ODBC de IBM i Access (iSeries). Instalelo (licencia IBM) y cree el DSN indicado en ORIGEN_DSN (por defecto Normativo).' }

Paso 'Configuracion'
$loc = Join-Path $Destino 'config.local.yaml'
if (-not (Test-Path $loc)) { Copy-Item (Join-Path $Destino 'config.local.yaml.ejemplo') $loc; Write-Host "config.local.yaml creado (ajustes propios del equipo: orden, parametros...; nunca se sobrescribe)" }
elseif (Test-Path (Join-Path $Destino 'config.yaml.bak_*')) { Aviso 'Si habia cambios propios en el config.yaml anterior (config.yaml.bak_*), pasarlos a config.local.yaml' }
$envf = Join-Path $Destino '.env'
if (-not (Test-Path $envf)) {
    (Get-Content (Join-Path $Destino '.env.example')) -replace '^ETL_ENTORNO=.*', 'ETL_ENTORNO=prod' | Set-Content $envf -Encoding ASCII
    Write-Host ".env creado (completar usuarios/contrasenas): $envf"
} else { Write-Host '.env existente conservado' }
@"
@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py %*
exit /b %ERRORLEVEL%
"@ | Set-Content (Join-Path $Destino 'etl_vigia.cmd') -Encoding ASCII

Paso 'Verificacion'
Push-Location $Destino
& $vpy main.py listar
$rc = $LASTEXITCODE
Pop-Location
if ($rc -ne 0) { throw 'La verificacion fallo' }

Paso 'Panel de seguimiento al iniciar Windows'
if ($SinPanel) { Write-Host 'Omitido (-SinPanel)' }
elseif (-not (Es-Admin)) { Aviso 'Se requieren permisos de administrador para registrar la tarea de inicio. Ejecute de nuevo como administrador (o use -SinPanel).' }
else {
    # Tarea al iniciar el sistema, sin sesion de usuario (SYSTEM), solo escucha en 127.0.0.1 y se reinicia si falla.
    $accion = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"`"$Destino\etl_vigia.cmd`" panel --puerto $PuertoPanel >> `"$Destino\logs\panel.log`" 2>&1`"" -WorkingDirectory $Destino
    $disparador = New-ScheduledTaskTrigger -AtStartup
    $ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $tareaPanel -Action $accion -Trigger $disparador -Settings $ajustes -Principal $principal `
        -Description 'Panel de seguimiento de etl_vigia_py (solo lectura, 127.0.0.1)' -Force | Out-Null
    Start-ScheduledTask -TaskName $tareaPanel
    Write-Host "Tarea $tareaPanel registrada y iniciada: http://127.0.0.1:$PuertoPanel/  (registro: $Destino\logs\panel.log)"
    Write-Host 'Nota: corre como SYSTEM; si el destino usa autenticacion de Windows, use usuario SQL en .env.'
}

Write-Host "`nInstalacion completa en $Destino" -ForegroundColor Green
Write-Host "Siguientes pasos: 1) editar $envf  2) etl_vigia.cmd conexiones  3) etl_vigia.cmd ejecutar --tabla TIPSTRAN --dry-run"
