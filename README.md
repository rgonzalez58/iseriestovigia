# etl_vigia_py

Reemplazo en Python del proyecto SSIS `etl_vigia`: iSeries/DB2 (ODBC, DSN `Normativo`) → SQL Server (`vigia`, esquema `VIGIA`, tablas `TMP_*`). Cargas *snapshot* idempotentes.

## Uso

```
pip install -r requirements-dev.txt
python dev/seed_dev.py --filas 5000     # crea dev_data/ (bases simuladas con SQLite)
python -m pytest
python main.py listar
python main.py ejecutar --tabla TERCEROS [--dry-run] [--forzar]
python main.py todas                    # TIPSTRAN -> TERCEROS -> DATOSADICIONALES -> PRODUCTOS -> TRANSACCIONES
python main.py promover --tabla TRANSACCIONES [--dry-run]   # TMP_TRANSACCIONES -> TRANSACCIONES (reemplaza PR_CARGATRANSACCIONES)
python main.py describir --tabla TERCEROS
python main.py conexiones
```

`ETL_ENTORNO=dev` (por defecto) usa SQLite; `ETL_ENTORNO=prod` usa pyodbc. Credenciales solo en `.env` (ver `.env.example`).

Códigos de salida: `0` OK, `1` error, `2` ya en ejecución, `3` abortado por validación (destino sin cambios). `todas` devuelve el peor código y se detiene en la primera tabla con error.

## Cómo funciona
Origen → staging (por lotes) → validaciones → **una transacción** (vaciar destino + `INSERT…SELECT` + verificar conteo). Ver `etl/snapshot.py`.

## Promoción TMP_* → tablas definitivas (`promover`)
Segundo grupo de programas: copia lo cargado en `TMP_*` a la tabla definitiva validando. Primero está **TRANSACCIONES**, que reemplaza al procedimiento `VIGIA.PR_CARGATRANSACCIONES` (que sigue existiendo, sin cambios).

```
python main.py promover --tabla TRANSACCIONES [--dry-run]
```
- **Pendientes:** filas distintas de `TMP_TRANSACCIONES` que no están en `TRANSACCIONES` (clave de 7 columnas, mismas comparaciones `COLLATE` del procedimiento).
- **Validación:** las funciones `VIGIA.VAL*` (tercero, producto del tercero, tipo de transacción, estado, sisgeo, sucursal, moneda, intermediario, medio, forma de pago, dispositivo) se evalúan **una vez por combinación distinta de argumentos**, no por fila. Los motivos de rechazo tienen los mismos textos del procedimiento.
- **Inserción:** por lotes (`lote` en `config.yaml`), una transacción por lote. Si un lote falla por datos (clave duplicada, truncamiento…) se parte por mitades hasta aislar la fila culpable, que se rechaza; un error de servidor (permisos, conexión) detiene el proceso y se puede repetir: es idempotente.
- **Alertas:** si `DEFINICIONALERTAS` código 42 = `S`, por cada transacción insertada cuyo número de identificación esté en una lista activa con `PROCESA_CARGATRNSACC='S'` se crea la alerta inicial y se envía el correo al responsable (`PR_GENMAIL`). Se apaga con `alertas: false`.
- **Rechazos:** a `VIGIA.ERRORESPROCESO` con `PROCESO='Carga Datos Base'` y `TAREA='Carga Transacciones'` (igual que el procedimiento; `DATOSERROR` lleva la clave de la fila, no se escribe en el log de archivo). El inicio, fin y tiempos van a `PROCESOS`, `ERRORESPROCESO` (`ETL_VIGIA_PY.PROMO_TRANSACCIONES`) y `uPROCESOS`.
- **TMP:** al final se borra de `TMP_TRANSACCIONES` lo ya promovido (`borrar_promovidas`); los rechazados quedan para revisión.
- `--dry-run` calcula pendientes y rechazos (en tablas temporales) sin escribir en tablas reales. Códigos de salida como el resto: 0, 1, 2 (también si la carga de `TMP_TRANSACCIONES` está en curso), 3.

Diferencias deliberadas con el procedimiento (revisar en el equipo B): se procesa por conjuntos y no fila a fila; no se llama a `PR_GENMAIL` cuando el responsable no tiene correo (el procedimiento lo llamaba con destinatario vacío); `TELEFONOREMITE` se lee pero **no se inserta** (igual que el procedimiento: probable omisión); `USUADI` de `TRANSACCIONES` usa `USER`, como el procedimiento.

## Configuración
`config.yaml` es de la versión (el instalador lo reemplaza); los ajustes propios de cada equipo van en `config.local.yaml` (plantilla `config.local.yaml.ejemplo`), que se mezcla encima y nunca se sobrescribe. Las opciones mal escritas se rechazan al arrancar.

## Opciones por tabla (`config.yaml`)
- `clave`, `fechas` (numérico/texto `AAAAMMDD` → fecha; `00000000` → NULL), `a_texto`.
- `ignorar: [COL]`: columnas del SELECT que no se migran (no llegan a staging ni destino).
- `mapeo: {ALIAS_ORIGEN: COLUMNA_DESTINO}`: el alias se carga a staging con su nombre y se escribe en otra columna del destino.
- Una columna del SELECT sin staging/destino (ni ignorada ni mapeada) **aborta** con código 3 (decisión pendiente: ¿permitir "ignorar con aviso"?).
- `particion: {tabla, columna}` + `fecha_defecto: ayer|hoy|ninguna`: filtro por fecha en el origen (el SQL lleva `@@FILTRO_FECHA@@`). Sin parametros = toda la tabla (por defecto `ninguna`); para acotar: `--fecha`, `--desde/--hasta`, `--mes`, `--todas-las-fechas`. `lectores: R`: R conexiones de lectura por particiones de fecha. `permitir_origen_vacio`: un día sin filas deja la tabla vacía.
- `hilos_escritura: N` (1-16): escribe el staging con N conexiones en paralelo (tablas grandes; ver manual). `espera_bloqueo_seg`: tope de espera de bloqueo en la carga final. `escritura: valores`: INSERT multi-fila (solo si `medir` lo muestra más rápido).
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

