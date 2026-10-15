# Manual de instalación y ejecución – ETL VIGIA (Python)

Proceso que migra tablas desde **iSeries/DB2** (ODBC) hacia **SQL Server** (base `vigia`, esquema `VIGIA`, tablas `TMP_*`). Reemplaza al proyecto SSIS `etl_vigia`. Cada carga es un *snapshot*: recarga completa e idempotente.

## 1. Tablas que migra

| Nombre en el comando | Tabla destino | Origen (iSeries) |
|---|---|---|
| `TERCEROS` | `VIGIA.TMP_TERCEROS` | `gnbweb_db.psa065` (estado `A`) |
| `DATOSADICIONALES` | `VIGIA.TMP_DATOSADICIONALESTERCERO` | `psa065` + `psa002` (flag `LISTAS_RESTRICTIVAS`) |
| `PRODUCTOS` | `VIGIA.TMP_PRODUCTOSTERCERO` | `gnbweb_db.psa066` (3 ramas por producto) |
| `TIPSTRAN` | `VIGIA.TMP_TIPSTRAN` | `GNBWEB_DB.psa068` (estado `A`) |
| `TRANSACCIONES` | `VIGIA.TMP_TRANSACCIONES` | `gnbweb_db.psa067` (3 ramas por producto; script PSA003) |

Orden de `todas`: TERCEROS → DATOSADICIONALES → PRODUCTOS → TIPSTRAN → TRANSACCIONES.

`TRANSACCIONES` (tabla grande) es una carga completa (*snapshot*) como las demás, con tres particularidades en `config.yaml`: `contar_origen: false` (no ejecuta el SELECT dos veces; el total es lo leído), conversión a texto de `PRODUCTO_NUMERO` (máx. 20) e `INTERMEDIARIO_CODIGO` (máx. 30) — si algún valor excede el máximo la carga **aborta** (código 3, destino sin cambios) en lugar de truncar — y sin revisión de claves duplicadas. Para volúmenes muy altos está pendiente el modo incremental (ver `PLAN_DESARROLLO.md`).

## 2. Requisitos del equipo

- Windows 64 bits, con permisos de administrador para instalar.
- Acceso de red al iSeries y al servidor SQL Server.
- Python 3.10 a 3.14 de 64 bits (el instalador lo instala con `winget` si falta, y necesita internet para eso).
- **ODBC Driver 17 (o 18) for SQL Server** (el instalador intenta instalarlo con `winget`).
- **Driver ODBC de IBM i Access (iSeries)** y un **DSN de sistema** llamado `Normativo` (o el nombre que ponga en `ORIGEN_DSN`). **No lo instala el instalador** (licencia IBM): instálelo y cree el DSN antes de ejecutar. El DSN debe ser del mismo tamaño (64 bits) que Python.
- Un usuario de iSeries con permiso de **solo lectura** y un usuario de SQL Server con permiso de escritura sobre el esquema `VIGIA` (`TRUNCATE`/`DELETE`, `INSERT`, `SELECT` sobre las tablas `TMP_*` y `STG_*`).

## 3. Instalación

Hay un instalador en `installer\dist\`: `Instalar_etl_vigia_py.ps1` (autocontenido). Instala en `C:\ETL\etl_vigia_py` y **no incluye bases de datos**.

### Instalar_etl_vigia_py.ps1
Abra PowerShell **como administrador**:

```powershell
powershell -ExecutionPolicy Bypass -File .\Instalar_etl_vigia_py.ps1
```

Parámetros opcionales:
- `-Destino D:\ETL\etl_vigia_py`: otra carpeta de instalación.
- `-SinWinget`: no instalar Python ni el driver ODBC por `winget` (equipos sin internet; instálelos antes).

### Qué hace el instalador
1. Busca Python 3.10+ (o lo instala con `winget`).
2. Copia el programa y crea el entorno virtual `.venv`.
3. Instala `pyyaml` y `pyodbc` desde las ruedas incluidas (sin internet); si falla, reintenta desde PyPI.
4. Revisa los drivers ODBC y avisa si falta alguno.
5. Crea `.env` con `ETL_ENTORNO=prod` y el lanzador `etl_vigia.cmd`.
6. Verifica con `main.py listar`.

Reinstalar es seguro: se conserva el `.env`; el `config.yaml` se reemplaza por el correcto y el anterior queda respaldado como `config.yaml.bak_<fecha>` (si cambiaste `parametros` u otras opciones, pásalas de nuevo).

## 4. Configuración

Todo lo siguiente se hace en `C:\ETL\etl_vigia_py`.

### 4.1 Credenciales (`.env`)
Edite `.env` (no se guarda en ningún otro archivo):

```
ETL_ENTORNO=prod
ORIGEN_DSN=Normativo
ORIGEN_USUARIO=<usuario iSeries>
ORIGEN_PASSWORD=<contraseña>
DESTINO_DRIVER=ODBC Driver 17 for SQL Server
DESTINO_SERVIDOR=<host o IP de SQL Server>
DESTINO_BASE=vigia
DESTINO_USUARIO=<usuario SQL Server>
DESTINO_PASSWORD=<contraseña>
```

**Origen sin DSN (como en NodeJS):** si el DSN no conecta, use el nombre del driver, igual que `DB_DRIVER` en las soluciones NodeJS. Agregue al `.env` (si `ORIGEN_DRIVER` está definido, `ORIGEN_DSN` se ignora):

```
ORIGEN_DRIVER=iSeries Access ODBC Driver
ORIGEN_SISTEMA=<host o IP del iSeries>
# ORIGEN_OPCIONES=NAM=1;DBQ=GNBWEB_DB     (opcional, atributos extra del driver)
```

Use el nombre exacto del driver instalado (`Get-OdbcDriver | ? Name -match 'iSeries|IBM i'` en PowerShell, o el valor de `DB_DRIVER` de NodeJS). El driver debe ser del mismo bitness que Python (64 bits).

Opcional: `PRODUCTOS_GRUPOA` y `PRODUCTOS_GRUPOB` (listas de códigos separadas por coma, sin espacios) sobreescriben los valores de `config.yaml`. Proteja `.env` para que solo lo lea la cuenta que ejecuta el proceso.

### 4.2 Tablas destino
Deben existir antes de la primera carga:
- `VIGIA.TMP_*`: las tablas reales de destino (ya existentes del SSIS).
- No hay que crear tablas de staging: el origen es un script SELECT cuyas columnas llevan el mismo nombre que las del destino (pueden ser menos; las demás quedan en NULL) y la carga intermedia usa una tabla temporal `#STG_*` con la estructura del destino. Para una tabla fija: `staging: VIGIA.STG_X` en `config.yaml`.
- `VIGIA.ERRORESPROCESO` con las columnas `PROCESO`, `TAREA`, `ERROR`, `DATOSERROR`, `FECADI`, `USUADI` (registro del proceso: PROCESO=`ETL_VIGIA_PY`, TAREA=tabla destino en ejecución, ERROR=estado, DATOSERROR=mensaje, FECADI=`GETDATE()`, USUADI=`SYSTEM_USER`; si no existe, la carga continúa y solo avisa en el log).

### 4.3 `config.yaml`
Opciones por tabla: `clave`, `fechas`, `a_texto`, `ignorar` (columnas que no se migran), `mapeo` (alias del origen → columna destino distinta), `lote`, `tolerancia_filas`, `max_caida_pct`, `vaciado` (`truncate` o `delete`; use `delete` si el usuario no tiene permiso `TRUNCATE`). Detalle en `README.md`.

## 5. Verificación antes de usar

Use `etl_vigia.cmd` desde la carpeta de instalación:

```bat
cd /d C:\ETL\etl_vigia_py
etl_vigia.cmd listar
etl_vigia.cmd conexiones
etl_vigia.cmd describir --tabla TIPSTRAN
etl_vigia.cmd ejecutar --tabla TIPSTRAN --dry-run
```

- `conexiones` debe mostrar `origen: OK` y `destino: OK`.
- `describir` muestra solo nombres de columna y tipos (nunca datos); avisa si un campo trae tipos mezclados.
- `--dry-run` lee el origen, carga el *staging* y valida, pero **no modifica la tabla destino**.

Orden recomendado para la primera vez: `TIPSTRAN` (la más simple) → `DATOSADICIONALES` → `PRODUCTOS` → `TERCEROS` → `TRANSACCIONES` (probar primero con `--dry-run` y medir el tiempo).

## 6. Ejecución

### Una tabla
```bat
etl_vigia.cmd ejecutar --tabla TERCEROS
```

### Todas las tablas (en orden)
```bat
etl_vigia.cmd todas
```
Se detiene en la primera tabla con error y muestra un resumen al final.

### Opciones
- `--dry-run`: valida sin tocar el destino.
- `--forzar`: omite las guardas de origen vacío y de caída brusca. Úselo solo si sabe que el origen realmente quedó vacío o redujo su tamaño.

### Cómo trabaja cada tabla
1. Cuenta las filas del origen.
2. Lee el origen por lotes y los carga en la tabla *staging*.
3. Valida (sin tocar el destino): columnas, conteos, origen no vacío, caída máxima y claves duplicadas (esto último solo avisa).
4. En **una sola transacción**: vacía el destino, inserta desde *staging* y verifica el conteo. Si algo falla, hace *rollback* y el destino conserva la carga anterior.

### Vaciar las tablas al terminar (`limpiar_al_final`)
Por defecto cada corrida **reemplaza** el contenido de las tablas `TMP_*` (no quedan registros de corridas anteriores) y las deja cargadas. Si el proceso real necesita además que queden **vacías al terminar**, se activa `limpiar_al_final`:

- **`config.yaml`** (`defaults` para todas las tablas, o dentro de una tabla para solo esa): `limpiar_al_final: true`.
- **Línea de comandos** (manda sobre `config.yaml`): `etl_vigia.cmd todas --limpiar-al-final` o `--no-limpiar-al-final`; igual con `ejecutar --tabla X`. Recomendado en la tarea programada: así cada tarea declara lo que hace.
- Con `todas`, la limpieza ocurre **al terminar todo el proceso** y solo si **todas** las tablas salieron bien y no es `--dry-run`. Con un error o aborto no se vacía nada (los datos quedan para revisarlos).
- Se usa el mismo modo de vaciado de cada tabla (`vaciado: truncate | delete`). Cada limpieza queda en `PROCESOS` (`LIMPIEZA ... filas=n`) y en `ERRORESPROCESO` (estado `LIMPIEZA`), y el `FIN` global lista las tablas limpiadas.
- Ojo: vaciar al final borra lo que se acaba de cargar; úselo solo si otro proceso consume las `TMP_*` antes de que termine `todas`, o si las lee en otra tarea que se ejecuta antes de la siguiente corrida.

### Códigos de salida
| Código | Significado |
|---|---|
| `0` | Carga correcta |
| `1` | Error durante la carga (el destino conserva los datos previos) |
| `2` | Ya hay otra ejecución de esa tabla en curso |
| `3` | Abortado por validación o configuración (el destino no se modificó) |

`todas` devuelve el peor código obtenido.

## 7. Agendado (Programador de tareas de Windows)

1. Abra el Programador de tareas → *Crear tarea*.
2. General: use una cuenta de servicio y marque *Ejecutar tanto si el usuario inició sesión como si no*.
3. Acción: *Iniciar un programa*
   - Programa: `C:\ETL\etl_vigia_py\etl_vigia.cmd`
   - Argumentos: `todas`
   - Iniciar en: `C:\ETL\etl_vigia_py`
4. Defina el horario. El Programador mostrará el código de salida en el historial (`0` = correcto).

La cuenta del servicio necesita leer `.env` y escribir en `logs\`, y el DSN `Normativo` debe ser un **DSN de sistema**.

## 8. Logs y monitoreo

- Archivo: `C:\ETL\etl_vigia_py\logs\etl_vigia.log` (no contiene datos personales: solo conteos, nombres de tablas y columnas; los literales de los errores del driver se enmascaran).
- Base de datos: `VIGIA.ERRORESPROCESO`, con los estados `INICIO`, `OK`, `DRY-RUN`, `ERROR`, `ABORTADO` y `FIN` (este último con la duración en minutos, formato ISO 8601, p. ej. `PT12.50M`) y el mensaje.
- `VIGIA.PROCESOS` (`NOMBRE`, `FECHA` en ISO 8601 como texto, `RESULTADO`): registro de ejecución. Una fila `INICIO` y otra `FIN` por tabla (`NOMBRE = ETL_VIGIA_PY.<TABLA>`) y por el proceso masivo `todas` (`ETL_VIGIA_PY.TODAS`, con estado, tablas OK y duración de cada tabla). Todas comparten el identificador `corrida=AAAAMMDD_HHMMSS`, también presente en `ERRORESPROCESO`, donde `PROCESO` = `NOMBRE` de `PROCESOS` (p. ej. `ETL_VIGIA_PY.TERCEROS`; el global es `ETL_VIGIA_PY.TODAS`). Unión: `SELECT * FROM VIGIA.PROCESOS p JOIN VIGIA.ERRORESPROCESO e ON e.PROCESO = p.NOMBRE AND e.DATOSERROR LIKE '%corrida=' + SUBSTRING(p.RESULTADO, CHARINDEX('corrida=', p.RESULTADO) + 8, 15) + '%'`. La tabla debe existir antes de ejecutar (si falta, la carga continúa y solo avisa en el log).
- Bloqueo: `logs\<TABLA>.lock` impide ejecuciones simultáneas de la misma tabla; se elimina solo al terminar (o a las 12 horas si quedó huérfano).

### Métricas exactas: VIGIA.uPROCESOS
`VIGIA.PROCESOS` no alcanza para medir con exactitud: guarda un texto por evento (`FECHA` varchar sin milisegundos y `RESULTADO` libre), sin duración numérica, sin filas ni tiempos por etapa, y el inicio y el fin son filas separadas que hay que emparejar leyendo texto. Por eso se agregó **`VIGIA.uPROCESOS`** (script `sql\destino\uprocesos.sql`, ejecutar una vez en la base `vigia`). `PROCESOS` sigue escribiéndose igual; no se modifica.

Una fila por proceso masivo (`TIPO=TODAS`) y una por tabla (`TIPO=TABLA`, con `ID_PADRE` = ID de la fila global). Se inserta al comenzar (`EN CURSO`) y se completa al terminar:
- `INICIO`, `FIN` (datetime2(3), hora del equipo del ETL) y `DURACION_MS` (reloj monotónico: no se altera con cambios de hora).
- Tiempo por etapa en ms: `MS_CONTAR_ORIGEN`, `MS_EJECUTAR_ORIGEN`, `MS_ESTRUCTURA_DESTINO`, `MS_CONTEO_DESTINO`, `MS_CARGAR_STAGING` (lectura del iSeries + conversión + inserción), `MS_VALIDAR_STAGING`, `MS_TRANSACCION_DESTINO`.
- `FILAS_ORIGEN`, `FILAS_CARGADAS`, `FILAS_DESTINO_ANTES` y `FILAS_POR_SEG` (calculada).
- `ESTADO`, `CODIGO_SALIDA`, `ETAPA_FINAL` (dónde falló), `DRY_RUN`, `FORZAR`, `USUARIO`, `EQUIPO`, `CORRIDA`, `PROCESO`, `MENSAJE`.

**Enlace con errores:** `uPROCESOS.PROCESO` = `ERRORESPROCESO.PROCESO` y `ERRORESPROCESO.DATOSERROR` siempre empieza por `corrida=<CORRIDA> `. La vista `VIGIA.V_uPROCESOS_ERRORES` (en el mismo script) hace la unión. Si la tabla no existe, la carga continúa y solo avisa en el log.

```sql
SELECT PROCESO, ESTADO, INICIO, FIN, DURACION_MS, MS_CARGAR_STAGING, MS_TRANSACCION_DESTINO, FILAS_CARGADAS, FILAS_POR_SEG
FROM VIGIA.uPROCESOS WHERE CORRIDA = '20261014_220000' ORDER BY ID;
SELECT * FROM VIGIA.V_uPROCESOS_ERRORES WHERE CORRIDA = '20261014_220000' ORDER BY ERROR_FECHA;
```

### Panel de seguimiento (HTML)
Muestra el progreso con los datos registrados en `VIGIA.PROCESOS` y `VIGIA.ERRORESPROCESO`: estado y avance del proceso masivo, tarjetas por tabla (pendiente / en curso / OK / error, con duración), corridas recientes y el registro filtrable.

```bat
etl_vigia.cmd panel                 (abrir luego http://127.0.0.1:8765/)
etl_vigia.cmd panel --puerto 9000 --max 500
```

Es un proceso aparte, de solo lectura (`SELECT` con `READ UNCOMMITTED`, sin bloqueos), que no comparte nada con la carga. Consulta la base cada 10 s como máximo mientras haya una pestaña abierta y guarda el resultado 5 s en caché (incluye la sección «Tiempos exactos» de uPROCESOS si existe), así que varias pestañas no multiplican las consultas. Solo escucha en `127.0.0.1`; use `--host 0.0.0.0` únicamente en una red de confianza (no tiene autenticación; muestra nombres de tabla, estados y mensajes sin datos personales). Para detenerlo: Ctrl+C.

## 9. Solución de problemas

| Síntoma | Causa probable y acción |
|---|---|
| Instalador: `No matching distribution found for pyyaml` | El Python del equipo no tiene ruedas incluidas y no hay internet. Use Python 3.10 a 3.14 de 64 bits (python.org) o habilite el acceso a PyPI y repita. |
| Instalador: no encuentra Python | Instale Python 3.10+ de 64 bits; ignore el atajo de la Microsoft Store. |
| `origen: FALLA` en `conexiones` | Si el DSN no conecta, pruebe sin DSN con `ORIGEN_DRIVER` + `ORIGEN_SISTEMA` (sección 4.1). Si no, verifique que existe el DSN de sistema `Normativo` (mismo bitness que Python), el driver de IBM i Access y las credenciales de `.env`. |
| `destino: FALLA` | Revise `DESTINO_SERVIDOR`, `DESTINO_DRIVER` (nombre exacto del driver instalado), puerto/firewall y credenciales. |
| Código `3`: columnas sin destino | El SELECT trae una columna que no existe en el destino. Agregue la columna al destino, o use `ignorar` / `mapeo` en `config.yaml`. |
| Código `3`: origen vacío o caída brusca | Revise el origen; si es correcto, ejecute con `--forzar`. |
| Código `2` | Hay una ejecución en curso. Si no la hay, borre `logs\<TABLA>.lock`. |
| Error de carga con línea `DIAGNOSTICO` | El mensaje de error indica, solo con nombres de columna y conteos (nunca valores), qué hay en el lote que falló: fechas fuera de rango, textos más largos que el destino (con el largo máximo y el permitido), `NULL` en columnas obligatorias o números que no caben en la precisión. Corregir el origen o ampliar la columna del destino. |
| Código `1` | Mire `logs\etl_vigia.log` y `ERRORESPROCESO`. Ejemplos: fila con `NULL` en columna `NOT NULL` del destino, permisos de `TRUNCATE` (use `vaciado: delete`). |
| Caracteres extraños (acentos) | Ajuste `encoding` en `config.yaml` (por defecto `cp1252`). |

## 10. Qué falta validar en el equipo real

Esta versión se probó con bases simuladas (SQLite), no contra iSeries ni SQL Server reales. Antes de apagar el SSIS:
- Comparar resultados con las consultas de `sql\paridad\*.sql` (diferencias en ambos sentidos sin filas y conteos iguales).
- Confirmar los `CREATE TABLE` reales de destino, el mapeo de columnas y las columnas `NOT NULL` sin origen.
- Validar tipos del driver ODBC (fechas `AAAAMMDD`, decimales) y `CHAR` con relleno de DB2.
- Medir tiempos y confirmar permisos (`TRUNCATE`, `TABLOCK`) y la estructura de `ERRORESPROCESO`.

El detalle está en `PLAN_DESARROLLO.md`.

