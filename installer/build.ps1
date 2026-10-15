# Prepara payload.zip y wheels.zip en installer\build\pkg (los usa build_ps1.ps1). No genera .exe ni incluye bases de datos.
$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$inst = Join-Path $raiz 'installer'
$stage = Join-Path $inst 'build'
$dist = Join-Path $inst 'dist'
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$stage\app", "$stage\pkg\wheels", $dist | Out-Null

# 1) Programa: solo lo necesario para ejecutar (sin dev/, tests/, dev_data/, logs/, .env, *.db, zips).
Copy-Item "$raiz\main.py", "$raiz\config.yaml", "$raiz\requirements.txt", "$raiz\.env.example", "$raiz\README.md", "$raiz\PLAN_DESARROLLO.md" "$stage\app"
Copy-Item "$raiz\etl" "$stage\app\etl" -Recurse
Copy-Item "$raiz\sql" "$stage\app\sql" -Recurse
Get-ChildItem "$stage\app" -Recurse -Include '__pycache__' -Directory | Remove-Item -Recurse -Force
Get-ChildItem "$stage\app" -Recurse -Include '*.db', '*.pyc', '.env' -File | Remove-Item -Force

# 2) Ruedas (wheels) para instalar sin conexion: Python 3.10 a 3.14, 64 y 32 bits.
foreach ($plat in 'win_amd64', 'win32') {
    foreach ($v in '3.10', '3.11', '3.12', '3.13', '3.14') {
        python -m pip download -r "$raiz\requirements.txt" -d "$stage\pkg\wheels" --only-binary=:all: --platform $plat --python-version $v --disable-pip-version-check -q
        if ($LASTEXITCODE) { Write-Warning "Sin ruedas para $plat / $v (se omite)" }
    }
}

# 3) Paquete
Compress-Archive -Path "$stage\app\*" -DestinationPath "$stage\pkg\payload.zip" -Force
Copy-Item "$inst\instalar.ps1" "$stage\pkg"

# 4) Ruedas empaquetadas
Compress-Archive -Path "$stage\pkg\wheels" -DestinationPath "$stage\pkg\wheels.zip" -Force
Remove-Item "$stage\pkg\wheels" -Recurse -Force
Get-Item "$stage\pkg\payload.zip", "$stage\pkg\wheels.zip" | Select-Object Name

