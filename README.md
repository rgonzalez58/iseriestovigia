# etl_vigia_py

Reemplazo en Python del proyecto SSIS `etl_vigia`: iSeries/DB2 (ODBC, DSN `Normativo`) → SQL Server (`vigia`, esquema `VIGIA`, tablas `TMP_*`). Cargas *snapshot* idempotentes.

## Uso

```
pip install -r requirements-dev.txt
python dev/seed_dev.py --filas 5000     # crea dev_data/ (bases simuladas con SQLite)
python -m pytest
python main.py listar
python main.py ejecutar --tabla TERCEROS [--dry-run] [--forzar]
python main.py todas                    # TERCEROS -> DATOSADICIONALES -> PRODUCTOS -> TIPSTRAN -> TRANSACCIONES
python main.py describir --tabla TERCEROS
python main.py conexiones
```

`ETL_ENTORNO=dev` (por defecto) usa SQLite; `ETL_ENTORNO=prod` usa pyodbc. Credenciales solo en `.env` (ver `.env.example`).

Códigos de salida: `0` OK, `1` error, `2` ya en ejecución, `3` abortado por validación (destino sin cambios). `todas` devuelve el peor código y se detiene en la primera tabla con error.

## Cómo funciona
Origen → staging (por lotes) → validaciones → **una transacción** (vaciar destino + `INSERT…SELECT` + verificar conteo). Ver `etl/snapshot.py`.

## Opciones por tabla (`config.yaml`)
- `clave`, `fechas` (numérico/texto `AAAAMMDD` → fecha; `00000000` → NULL), `a_texto`.
- `ignorar: [COL]`: columnas del SELECT que no se migran (no llegan a staging ni destino).
- `mapeo: {ALIAS_ORIGEN: COLUMNA_DESTINO}`: el alias se carga a staging con su nombre y se escribe en otra columna del destino.
- Una columna del SELECT sin staging/destino (ni ignorada ni mapeada) **aborta** con código 3 (decisión pendiente: ¿permitir "ignorar con aviso"?).
- `texto_max: {COL: n}`: la carga aborta si un valor excede n caracteres (no trunca en silencio). `contar_origen: false`: no cuenta el origen aparte (tablas grandes).
- `parametros` (`PROD_A`, `PROD_B`): sustituyen `@@PROD_A@@`/`@@PROD_B@@` en el SQL de origen (solo enteros separados por coma; se pueden sobreescribir con `PRODUCTOS_GRUPOA/B`).

## Agregar otra tabla
1. SQL de origen en `sql/origen/<tabla>.sql` (copiado del SSIS, sin cambios; se normalizan solo espacios Unicode al leerlo).
2. El destino real ya existe; el staging es una tabla temporal automática (opcional: `staging: VIGIA.STG_X`).
3. Entrada en `config.yaml` (y en `orden` si corresponde a `todas`).
4. Datos falsos con casos borde en `dev/seed_dev.py`; pruebas en `tests/`.
5. `sql/paridad/<tabla>.sql`.

## Instalador (sin bases de datos)
`installer\build_ps1.ps1` genera `installer\dist\Instalar_etl_vigia_py.ps1` (un solo .ps1 autocontenido; ya no se genera .exe). Ejecutar en PowerShell como administrador. Instala en `C:\ETL\etl_vigia_py`: programa, entorno virtual con `pyyaml`/`pyodbc` (ruedas incluidas para Python 3.10-3.13, sin internet), Python 3.12 y ODBC Driver 17 vía `winget` si faltan, `.env` con `ETL_ENTORNO=prod` y `etl_vigia.cmd`. No incluye `dev_data`, `tests`, `dev`, `.env` ni archivos `.db`. El driver ODBC de IBM i Access (iSeries) y el DSN `Normativo` no se instalan (licencia IBM): el instalador avisa si faltan. Una reinstalación conserva `.env` y reemplaza `config.yaml` por el correcto (respaldo en `config.yaml.bak_<fecha>`).

## Agendado (Task Scheduler)
Acción: `python C:\HelpDesk\etl_vigia_py\main.py todas` con "Iniciar en" la carpeta del proyecto; el Programador lee el código de salida (≠0 = fallo).

## Estado de las tablas
| Tabla | Estado |
|---|---|
| TERCEROS, DATOSADICIONALES, PRODUCTOS, TIPSTRAN, TRANSACCIONES | implementadas (snapshot), probadas en SQLite |
| TRANSACCIONES incremental | pendiente (T5): hoy es snapshot con `contar_origen: false` y `texto_max` |
| FUNCIONARIOS | deshabilitada en el SSIS; no incluida |

Los `CREATE TABLE` de `sql/destino/` son **borradores** derivados del SELECT; reemplazarlos por los reales. Lo pendiente de validar en el equipo B está en `PLAN_DESARROLLO.md`.

