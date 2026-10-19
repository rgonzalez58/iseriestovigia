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

Orden de `todas`: TIPSTRAN → TERCEROS → DATOSADICIONALES → PRODUCTOS → TRANSACCIONES.

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

### 4.3 `config.yaml` y `config.local.yaml`
- **`config.yaml`** es de la versión: el instalador lo reemplaza en cada actualización (el anterior queda en `config.yaml.bak_<fecha>`). **No lo edite.**
- **`config.local.yaml`** son los ajustes propios del equipo (orden de `todas`, `parametros`, `limpiar_al_final`, `lote`, opciones de una tabla…). El instalador lo crea desde `config.local.yaml.ejemplo` si no existe y **nunca lo sobrescribe**. Se mezcla sobre `config.yaml`: los mapas se combinan clave por clave y las listas (como `orden`) se reemplazan completas. Ejemplo: `orden: [TIPSTRAN, TERCEROS, TRANSACCIONES]`.
- Una opción o sección mal escrita (p. ej. `limpiar_al_fin`) o un `orden` con una tabla inexistente se rechaza al arrancar con un mensaje claro, en lugar de ignorarse en silencio. `etl_vigia.cmd listar` valida la configuración y muestra el orden efectivo.

Opciones por tabla (además de las de abajo, `escritura: parametros | valores`, que elige cómo se escribe el staging; ver "Carga lenta"):
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

### Promoción TMP → definitiva: `promover`
Después de cargar `TMP_TRANSACCIONES` (`ejecutar --tabla TRANSACCIONES` o `todas`):

```
.\etl_vigia.cmd promover --tabla TRANSACCIONES --dry-run     (primero: solo valida y cuenta)
.\etl_vigia.cmd promover --tabla TRANSACCIONES
```
Reemplaza al procedimiento `VIGIA.PR_CARGATRANSACCIONES` (no lo modifica). Valida con las funciones `VIGIA.VAL*` existentes, inserta en `VIGIA.TRANSACCIONES` por lotes, genera las alertas por listas restrictivas, registra los rechazados en `ERRORESPROCESO` (`PROCESO='Carga Datos Base'`, `TAREA='Carga Transacciones'`) y borra de `TMP_TRANSACCIONES` lo promovido. Es idempotente: repetirlo no duplica. Opciones (`promociones:` en `config.yaml` o `config.local.yaml`): `lote`, `alertas`, `borrar_promovidas`. Necesita permiso de `INSERT` en `TRANSACCIONES` y `RESULTADOALERTASPROGRAMADAS` y `EXECUTE` sobre `PR_GENMAIL` y las funciones `VAL*`.
Consulta de resultado: `SELECT * FROM VIGIA.uPROCESOS WHERE PROCESO = 'ETL_VIGIA_PY.PROMO_TRANSACCIONES' ORDER BY ID DESC;` (el `MENSAJE` trae pendientes, insertadas, rechazadas, alertas y tiempo por etapa).

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

### Escritura en paralelo (`hilos_escritura`)
Con `hilos_escritura: N` (1 a 16; por defecto 1 = como siempre) la carga al staging usa N conexiones a SQL Server que escriben a la vez, mientras el hilo principal lee el origen: se aprovecha que cada lote espera la respuesta del servidor (latencia). El staging pasa a ser una tabla temporal **global** (`##STG_<TABLA>`, en `tempdb`), que se borra sola al terminar. La transacción final de `TMP_*` no cambia (un solo `commit`), y ante cualquier error el destino conserva la carga anterior.

```yaml
# config.local.yaml
tablas:
  TRANSACCIONES:
    hilos_escritura: 4
```
Primero medir con `etl_vigia.cmd medir --tabla TERCEROS --hilos 8` (cuánto escala la escritura) y subir de a poco: 4, 8. La lectura del iSeries sigue siendo de una sola conexión (~5.500 filas/s), que pasa a ser el límite. El log muestra cada 30 s filas leídas, escritas y el estado de la cola (`cola 8/8` = los escritores no alcanzan; `cola 0/8` = el límite es la lectura). Con 11 millones de filas el staging y la inserción final necesitan varios GB libres en `tempdb` y en el log de `vigia`.

### Fechas de la corrida y lectura en paralelo (`TRANSACCIONES`)
Si **no se indica ninguna fecha** (por ejemplo, `todas` a secas), se lee **toda la tabla de origen**: se asume que `psa067` contiene solo los registros que hay que transmitir. Para acotar (reproceso de un día, un mes, etc.) se usan las opciones de fecha. El filtro se aplica sobre `psa067fc` (columna `FECHA`) directamente en el iSeries.

```bat
etl_vigia.cmd todas                                              (sin fecha: toda la tabla de origen)
etl_vigia.cmd ejecutar --tabla TRANSACCIONES --fecha 2026-10-15   (una fecha concreta)
etl_vigia.cmd ejecutar --tabla TRANSACCIONES --mes 2026-09         (reproceso: mes completo)
etl_vigia.cmd ejecutar --tabla TRANSACCIONES --desde 2026-09-01 --hasta 2026-09-15
etl_vigia.cmd ejecutar --tabla TRANSACCIONES --todas-las-fechas    (toda la tabla de origen)
```
- Las opciones de fecha solo afectan a las tablas con `particion` en `config.yaml` (hoy `TRANSACCIONES`); en `todas` las demás tablas se cargan completas como siempre. `fecha_defecto` es `ninguna` (toda la tabla) en `config.yaml`; si algún equipo quiere que por defecto cargue solo ayer u hoy, pone `ayer` u `hoy` en `config.local.yaml`.
- Sigue valiendo la regla de que la tabla destino contiene solo lo de la corrida: cada carga **reemplaza** el contenido de `TMP_TRANSACCIONES` por las fechas pedidas.
- Con rango de fechas no se aplica la guarda de "caída brusca" (un día después de un mes de reproceso tiene muchas menos filas). `permitir_origen_vacio: true` (valor de `config.yaml` para `TRANSACCIONES`) permite que un día sin transacciones deje la tabla vacía en lugar de abortar; con `false` aborta (código 3, destino sin cambios) y `--forzar` lo permite una vez.
- El `FIN` de cada tabla y el log indican las fechas cargadas (`fechas=2026-10-15`).

**Lectura paralela.** `lectores: R` conexiones al iSeries leen a la vez: con rango, una partición por **día** (un mes = 31 particiones repartidas entre los lectores); sin rango (toda la tabla), una por mes más las de fechas anteriores, posteriores y nulas, de modo que ninguna fila queda fuera. Se combina con `hilos_escritura: N` (ver arriba):

```yaml
# config.local.yaml
tablas:
  TRANSACCIONES:
    lectores: 4
    hilos_escritura: 8
```
- **Una sola fecha con varios lectores:** con pocas fechas (menos de 2 × `lectores`, p. ej. la corrida diaria) cada fecha se reparte además en `lectores` trozos por el resto de `psa067co` (`subcolumna` en `particion`), más un trozo con lo que ningún resto cubre (nulos, fracciones, negativos), de modo que ninguna fila queda fuera. Es lo que permite leer en paralelo un solo día. Medición (fecha con 829.830 filas): el iSeries resuelve la consulta en 2 a 4 s (`COUNT`); lo que tarda (65 a 118 s con una conexión) es traer las filas por ODBC, y eso sí escala con más conexiones.
- El SQL de origen lleva el marcador `@@FILTRO_FECHA@@` en el `WHERE` de cada rama; vacío no cambia nada. Las filas repetidas tienen la misma fecha, así que el `UNION` conserva su resultado.
- Con rango o lectores no se hace el `COUNT(*)` previo del origen; el total es lo leído.
- Se crean hasta 16 conexiones al iSeries y 16 a SQL Server: subir de a poco (2 y 4, luego 4 y 8). El log informa cada 30 s las filas leídas y escritas, las particiones hechas y el estado de la cola.

### Índices del origen y velocidad de la lectura de `TRANSACCIONES`
Índices de `GNBWEB_DB.PSA067` (consulta de `QSYS2.SYSINDEXES`/`SYSKEYS`): `PSA06701` = `PA, TD, DO, MC, NC, SO, TX, FC, CO` y `PSA06702` = `CI`. **Ninguno empieza por la fecha (`PSA067FC`)**, así que un filtro por fecha obliga al iSeries a recorrer la tabla (o el índice) completa: una consulta por fecha cuesta un recorrido, y cada partición paralela cuesta otro.

Consecuencias y recomendaciones:
- **Corrida diaria (una fecha):** un solo recorrido. Conviene que sea **uno** y no tres: el script actual tiene tres ramas con `UNION`; `sql/origen/transacciones_optimizado.sql` hace lo mismo en una sola lectura. Antes de adoptarlo, compararlo sobre una fecha real (solo lectura, no imprime datos):
  ```bat
  etl_vigia.cmd comparar --tabla TRANSACCIONES --sql sql/origen/transacciones_optimizado.sql --fecha 2026-10-15
  ```
  Muestra filas de cada script, filas que solo están en uno u otro, y el tiempo de cada consulta; debe decir `IGUALES`. Si el alterno es igual y más rápido, se activa con `sql: sql/origen/transacciones_optimizado.sql` en `tablas: TRANSACCIONES:` de `config.local.yaml`.
- **Reproceso de un mes:** con `lectores: 1` (valor por defecto) es una sola consulta para todo el rango, es decir, un recorrido. **No** subir `lectores` mientras no exista un índice por fecha: cada día sería otro recorrido completo.
- **Mejora de fondo (la decide el administrador del iSeries):** un índice cuya primera columna sea la fecha, p. ej. `CREATE INDEX GNBWEB_DB.PSA06703 ON GNBWEB_DB.PSA067 (PSA067FC)`. Con él, una fecha se resuelve en segundos y las particiones diarias en paralelo pasan a ser útiles. El ETL no crea índices (el origen es de solo lectura); el costo es el mantenimiento del índice en la tabla productiva.
- Para medir cuánto tarda la primera fila de cada partición: `etl_vigia.cmd medir --tabla TRANSACCIONES --lectores 4`.

### Velocidad de la lectura por ODBC (iSeries)
La lectura de filas por una conexión ronda 5.000 a 15.000 filas/s según el día. Además de `lectores`, el driver de IBM i Access tiene opciones de rendimiento (bloques de registros más grandes, compresión, optimizar para todas las filas) que se pueden probar con `ORIGEN_OPCIONES` en el `.env`, separadas por `;`. **Los nombres exactos de las palabras clave dependen de la versión del driver**: confírmelos en la pestaña de rendimiento de la configuración del DSN o en la documentación de IBM antes de usarlos, y mida con `etl_vigia.cmd medir --tabla TRANSACCIONES` (línea `origen: ... lectura`) antes y después.

### Índices del destino en la carga final (`reconstruir_indices`)
El `INSERT … SELECT` final actualiza los índices no agrupados fila por fila (en `TMP_TRANSACCIONES` hay 2; con 830 mil filas tarda 38 a 58 s). Con `reconstruir_indices: true` se **desactivan** antes de insertar y se **reconstruyen** al terminar, dentro de la misma transacción (si algo falla, el rollback los deja como estaban). El log informa `indices_off`, `insertar` e `indices_rebuild`, y el `FIN` los incluye en `fases=`. Requiere permiso `ALTER` sobre la tabla (el mismo que `TRUNCATE`). Apagado por defecto: medir las dos formas y quedarse con la más rápida (suma de `indices_off` + `insertar` + `indices_rebuild` frente a `insertar`).
```yaml
tablas:
  TRANSACCIONES:
    reconstruir_indices: true
```

### Fase final lenta (vaciar, insertar, contar, commit)
El log y el `FIN` (`fases=`) desglosan la transacción final: `vaciar`, `insertar` (con las filas que el servidor dice haber insertado), `contar` y `commit`, en segundos. Si una tarda mucho, mientras ocurre ejecute en SQL Server la consulta de bloqueos de "Carga lenta" (el `wait_type` indica la causa: `WRITELOG` o `LOGBUFFER` = escritura del log, `PAGEIOLATCH_*`/`IO_COMPLETION` = disco, `LCK_*` = bloqueo) y revise el modelo de recuperación y el crecimiento de los archivos:
```sql
SELECT d.name, d.recovery_model_desc, f.name AS archivo, f.type_desc, f.size * 8 / 1024 AS mb, f.growth, f.is_percent_growth
FROM sys.databases d JOIN sys.master_files f ON f.database_id = d.database_id
WHERE d.name IN ('vigia', 'tempdb') ORDER BY d.name, f.type;
```
Con el modelo `SIMPLE` y `WITH (TABLOCK)` el `INSERT` se registra de forma mínima y SQL Server vuelca todas las páginas nuevas al disco en el `commit`: con millones de filas y disco lento el `commit` puede tardar minutos. Para comparar sin ese hint: `tablas: {TRANSACCIONES: {tablock: false}}` en `config.local.yaml` (se escribe más log, pero el `commit` no fuerza el volcado). Probar ambos y quedarse con el más rápido. Un log pequeño con crecimiento en pasos chicos (1 MB o 10 %) obliga a crecer muchas veces durante una transacción grande, y cada crecimiento espera.

### Bloqueos en la carga final (`espera_bloqueo_seg`)
La transacción final (vaciar `TMP_*` e insertar desde el staging) espera como máximo `espera_bloqueo_seg` segundos (por defecto 120) a que otra sesión suelte la tabla. Si se agota, la carga **falla con un mensaje claro** (`tiempo de espera de bloqueo agotado ... otra sesión mantiene bloqueada VIGIA.TMP_X`), el destino conserva la carga anterior y no queda colgada. Ajustable en `config.local.yaml` (`defaults` o por tabla). El log registra cuánto tarda el vaciado y el `INSERT` final.

### Carga lenta: cómo medir dónde se va el tiempo
```bat
etl_vigia.cmd medir --tabla TERCEROS --filas 20000
```
Lee una muestra del origen y la escribe en una tabla temporal (no toca las tablas reales ni imprime datos). Informa filas/s de **lectura del origen**, **conversión en Python** y **escritura** (con y sin `fast_executemany`), y cuántos índices y triggers tiene el destino. Compara también la escritura con `INSERT` multi-fila sin parámetros (`escritura: valores`), que manda miles de filas por viaje de red y conviene cuando el servidor de SQL Server está lejos (latencia alta). En una medición real con el servidor a 7 ms de latencia fue más lenta que `fast_executemany` (299 frente a 1.729 filas/s), así que solo conviene si `medir` la muestra más rápida. Para adoptarla en una tabla: `tablas: {TERCEROS: {escritura: valores}}` en `config.local.yaml`. La fase con menos filas/s es el cuello de botella; el tiempo total estimado es `filas_totales / filas_por_segundo`.

Durante una carga real, el log muestra cada 30 s `n filas en staging | origen x f/s, conversión y f/s, escritura z f/s`, y el registro `FIN` (en `ERRORESPROCESO`) incluye `etapas=` y `fases=` con los segundos de cada una. Si una corrida parece detenida, en SQL Server:
```sql
SELECT r.session_id, r.status, r.command, r.wait_type, r.wait_time, r.blocking_session_id, r.cpu_time, r.reads, r.writes
FROM sys.dm_exec_requests r WHERE r.session_id <> @@SPID AND r.database_id = DB_ID('vigia');
```
`blocking_session_id` distinto de 0 = bloqueada por otra sesión (p. ej. una corrida anterior interrumpida que dejó una transacción abierta); Mientras corre `medir` o una carga, la misma consulta indica qué espera el servidor: `ASYNC_NETWORK_IO` = espera al cliente/red; `WRITELOG` o `PAGEIOLATCH_*` = disco del servidor; `wait_type` vacío con CPU alta = trabajo del servidor. `wait_type` `ASYNC_NETWORK_IO` = SQL Server espera al cliente (lectura del origen o Python lentos).

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



## Panel de seguimiento al iniciar Windows
El instalador (ejecutado **como administrador**) registra la tarea programada `ETL_VIGIA_Panel`: se dispara **al iniciar el sistema**, sin
sesión de usuario (cuenta SYSTEM), ejecuta `etl_vigia.cmd panel` y se reinicia hasta 5 veces si falla. Escucha solo en `127.0.0.1:8765`
(`http://127.0.0.1:8765/`) y su salida queda en `logs\panel.log`. Es de solo lectura y liviano. La cuenta SYSTEM no tiene credenciales de
Windows hacia SQL Server: use usuario SQL en `.env`.

```
Instalar_etl_vigia_py.ps1 -PuertoPanel 9000   (otro puerto)
Instalar_etl_vigia_py.ps1 -SinPanel           (no registrar la tarea)
Instalar_etl_vigia_py.ps1 -QuitarPanel        (eliminar la tarea)
```
Al actualizar, el instalador detiene el panel antes de copiar y lo vuelve a iniciar. Para verlo desde otro equipo hay que ejecutar el panel
con `--host 0.0.0.0` solo si la red es de confianza.
