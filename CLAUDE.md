# Handoff para Claude Code: etl_vigia_py

Copiar este archivo a la raíz del proyecto (puede renombrarse a `CLAUDE.md` o `AGENTS.md`) y pedir a Claude Code que continúe con las tareas de la sección "Tareas pendientes".

## 1. Contexto

- **Sistema:** VIGIA. Hoy los datos se cargan con un proyecto SSIS (`etl_vigia`, ver `AGENTS.md` del repo SSIS) desde **iSeries/DB2** (ODBC, DSN `Normativo`) hacia **SQL Server** (base `vigia`, esquema `VIGIA`, tablas `TMP_*`).
- **Objetivo:** reemplazar el SSIS por este proyecto Python, independiente, rápido en alto volumen (hasta 10M+ filas en transacciones), idempotente y fácil de agendar.
- **Dos equipos:**
  - **Equipo A (desarrollo):** no tiene iSeries ni SQL Server. Se desarrolla con `ETL_ENTORNO=dev` (SQLite simulado en `dev_data/`).
  - **Equipo B:** tiene iSeries y SQL Server. Se ejecuta con `ETL_ENTORNO=prod`. Lo que no se puede validar en A queda anotado en la sección 7.
- **Idioma:** documentación, mensajes y comentarios en español (mantener).

## 2. Estado actual

Implementada la tabla **TERCEROS** (`VIGIA.TMP_TERCEROS`), carga tipo *snapshot* (recarga completa e idempotente). `python -m pytest` pasa **18 pruebas**.

```
main.py                    CLI: listar | conexiones | describir | ejecutar [--destino] [--dry-run] [--forzar]
config.yaml                tablas + defaults (credenciales NO van aquí: van en .env)
etl/config.py              carga config.yaml + .env; dataclasses Tabla y Config; cadenas ODBC
etl/db.py                  conectar_origen / conectar_destino (pyodbc en prod, SQLite en dev)
etl/dialecto.py            diferencias SQL Server vs SQLite (TRUNCATE, TABLOCK, columnas, GETDATE)
etl/snapshot.py            lógica de carga snapshot (ver sección 3)
etl/runner.py              lock + conexiones + snapshot + log; devuelve código de salida
etl/logger.py              logging a archivo y registro en VIGIA.ERRORESPROCESO
etl/lock.py                lock por archivo (sin os.kill: en Windows termina el proceso)
etl/diagnostico.py         comandos conexiones y describir
sql/origen/terceros.sql    SELECT de origen, copiado tal cual del SSIS
sql/destino/               DDL: tmp_terceros.sql (existente), stg_terceros.sql (staging, 39 columnas)
sql/paridad/terceros.sql   comparación EXCEPT entre TMP del SSIS y TMP_*_PY de Python
dev/seed_dev.py            genera dev_data/ (iSeries y SQL Server simulados, con casos borde)
tests/                     test_terceros.py (integración sobre SQLite) y test_unidades.py
```

Códigos de salida (los lee el Programador de tareas): `0` OK, `1` error, `2` ya en ejecución, `3` abortado por validación (el destino no se modificó).

## 3. Cómo funciona un *snapshot* (`etl/snapshot.py`)

1. `COUNT(*)` del origen (envolviendo el mismo SELECT).
2. Lectura en streaming (`fetchmany`) y carga por lotes a una tabla **staging** (heap, se vacía en cada corrida). `fast_executemany` solo en SQL Server.
3. Validaciones sin tocar el destino: columnas del SELECT existen en staging y destino; conteo staging == enviadas; conteo origen == cargadas (`tolerancia_filas`); guardas de origen vacío y caída brusca (`max_caida_pct`, `--forzar` las omite).
4. Aviso (no error) de claves duplicadas.
5. `--dry-run` se detiene aquí.
6. **Una transacción:** vaciar destino + `INSERT ... SELECT` desde staging + verificar conteo → commit; ante cualquier error → rollback (el destino conserva la carga anterior).

Mapeo actual: **por nombre**. El alias de cada columna del SELECT debe ser igual al nombre de la columna en staging y destino. Las columnas del destino sin origen quedan en `NULL`. Una columna del origen sin destino **aborta** (código 3) a propósito.

## 4. Reglas y restricciones (respetar)

- **No modificar el SQL de origen** (`sql/origen/*.sql`) salvo pedido explícito: debe dar el mismo resultado que el SSIS para poder hacer paridad. Los `UNION` (sin `ALL`) no se cambian sin validar con negocio.
- **No tocar el proyecto SSIS.** Convive en paralelo hasta la paridad.
- **No imprimir ni registrar valores de datos personales** (nombres, documentos, teléfonos). Los diagnósticos muestran solo nombres de columna y tipos.
- **Secretos solo en `.env`** (ya está en `.gitignore`). Los nombres de variables replican `Project.params` del SSIS (`ORIGEN_*`, `DESTINO_*`).
- **El destino solo se modifica dentro de la transacción final.** Ninguna tabla nueva puede cambiar esto.
- **Origen de iSeries en solo lectura** (`readonly=True`, `autocommit=True`).
- **`dev` debe seguir funcionando** sin pyodbc ni servidores: `pyodbc` se importa solo en `prod`.
- **Todo cambio lleva su prueba** en `tests/` y debe dejar `python -m pytest` en verde. Cada tabla nueva amplía `dev/seed_dev.py` con datos falsos y casos borde.
- SQLite **no** reproduce el comportamiento de `CHAR` de DB2 (`''` = `'   '`) ni los tipos reales del driver ODBC; no afirmar que algo está validado contra producción si solo pasó en SQLite.

## 5. Tareas pendientes (en este orden)

### T1. Columnas que no se migran y nombres distintos (`ignorar` y `mapeo`)
Agregar a cada tabla en `config.yaml` (y a la dataclass `Tabla`):
- `ignorar: [COL1, COL2]`: columnas del SELECT que **no** se migran. No se cargan a staging ni al destino (se descartan al leer cada fila). Staging no necesita tenerlas.
- `mapeo: {ALIAS_ORIGEN: COLUMNA_DESTINO}`: el alias del origen se carga en staging con su propio nombre y el `INSERT ... SELECT` final lo escribe en `COLUMNA_DESTINO`.

Criterios de aceptación:
- Validar en `_validar_columnas` que toda entrada de `ignorar`/`mapeo` exista en el SELECT, y que los destinos de `mapeo` existan en la tabla destino.
- Una columna del origen que no esté en `ignorar`, ni exista en el destino (directo o por `mapeo`) **sigue abortando** con mensaje claro. *Decisión abierta con el usuario:* mantener el aborto (valor por defecto) o permitir "ignorar con aviso"; no cambiar el comportamiento sin confirmarlo.
- `fechas`, `a_texto` y `clave` siguen refiriéndose a alias del origen.
- Pruebas nuevas: columna ignorada no llega al destino; columna renombrada llega con el nombre del destino; error al mencionar columnas inexistentes.

### T2. Siguiente tabla: `TMP_TIPSTRAN` (la más simple)
Pendiente de que el usuario entregue el **SQL de origen** y el **`CREATE TABLE` del destino**. Origen conocido: `GNBWEB_DB.psa068 where psa068st='A'`. Seguir el procedimiento del `README.md` ("Agregar otra tabla"): SQL en `sql/origen/`, DDL de staging en `sql/destino/`, entrada en `config.yaml`, datos falsos en `dev/seed_dev.py`, pruebas, y archivo de paridad en `sql/paridad/`.

### T3. `TMP_DATOSADICIONALESTERCERO`
`UNION` de `gnbweb_db.psa065` + `psa002` (flag `LISTAS_RESTRICTIVAS` 1/0). Snapshot. El SSIS tiene un `Truncate_TMP_PT` deshabilitado sobre esta tabla; la nueva carga es idempotente por diseño.

### T4. `TMP_PRODUCTOSTERCERO`
`gnbweb_db.psa066` en 3 ramas `UNION` por `psa066mo`, con listas de productos parametrizables (`Productos_GrupoA/B` en el SSIS, sustituyendo `@@PROD_A@@` y `@@PROD_B@@`). Implementar sustitución de parámetros en el SQL de origen desde `config.yaml` o `.env`, sin editar el archivo SQL.

### T5. `TMP_TRANSACCIONES` (crítica, 10M+ filas): nuevo modo `incremental`
`gnbweb_db.psa067`, 3 ramas `UNION` por `psa067mo`, con `Data Conversion` y conteo en `User::CantidadRegistros`. Diseño propuesto:
- Lectura particionada por fecha (rangos mensuales) y hasta N hilos en paralelo (parámetro, por defecto 3 o 4, para no saturar el iSeries).
- Por partición: vaciar su rango en staging, cargar, validar conteo, y `DELETE` del rango + `INSERT` en el destino dentro de una transacción.
- Tabla de control `VIGIA.ETL_CONTROL` (watermark y estado por partición) para reanudar solo lo fallido. Reintentos por partición.
- Antes de implementar, necesita del usuario: volumen, columna de fecha o ID creciente, y si hay bajas o actualizaciones en el origen.
- Valorar escritura con `bcp` o `mssql-python` (bulk copy) si `fast_executemany` no alcanza al medir en el equipo B.

### T6. Orquestador `todas`
Comando que ejecute todas las tablas respetando dependencias (equivalente a `MASTER_ETL`: TERCEROS → DATOSADICIONALES → PRODUCTOS → TIPSTRAN → TRANSACCIONES), con resumen final y código de salida.

### T7. Operación
Alertas (correo o Teams) cuando el código de salida sea distinto de 0; documentar el agendado con Task Scheduler/PM2 (ya hay un ejemplo en `README.md`).

## 6. Cómo probar

```
pip install -r requirements-dev.txt
python dev/seed_dev.py --filas 5000          # crea dev_data/ (ya no hace falta si se usan los fixtures de pytest)
python -m pytest                             # debe quedar en verde
python main.py ejecutar --tabla TERCEROS     # contra las bases simuladas (ETL_ENTORNO=dev es el valor por defecto)
python main.py describir --tabla TERCEROS
```

Los fixtures de `tests/conftest.py` crean un `dev_data` temporal por prueba; usarlos para toda prueba nueva (`cfg`, `correr`, `consultar`, `origen_rw`).

## 7. Pendiente de validar en el equipo B (no se puede aquí)

- **Paridad contra el SSIS** (`sql/paridad/terceros.sql`): `EXCEPT` en ambos sentidos sin filas y conteos iguales.
- Comparaciones `''` vs `'   '` en DB2 (`CHAR` con relleno).
- Tipos reales que devuelve el driver ODBC (fechas numéricas `AAAAMMDD`, `Decimal`); `describir` los lista y detecta tipos mezclados (p. ej. `SUCURSAL_CODIGO`, que se convierte a texto con `a_texto`).
- Codificación (`origen.encoding: cp1252`), permisos (`TRUNCATE`/`ALTER`, si no: `vaciado: delete`), `TABLOCK`, `fast_executemany` y rendimiento real.
- **Supuesto del log:** `VIGIA.ERRORESPROCESO` recibe `ERROR` ← estado (`INICIO`, `OK`, `ERROR`, `ABORTADO`) y `DATOSERROR` ← mensaje. Confirmar contra el DDL real.

## 8. Datos que faltan del usuario

1. Por tabla: SQL de origen y `CREATE TABLE` del destino.
2. El mapeo real del SSIS (el `.dtsx` del Data Flow o captura de *Mappings*) y las columnas calculadas (*Derived Column*, *Data Conversion*, valores fijos).
3. Columnas `NOT NULL` del destino sin origen: valor por defecto o regla.
4. Tablas grandes: filas aproximadas, columna de fecha o ID creciente, bajas/actualizaciones, frecuencia.
5. Valores de `Productos_GrupoA/B`.
6. DDL de `VIGIA.ERRORESPROCESO`.
7. Salida de `describir` y `--dry-run` de TERCEROS en el equipo B, y resultado de la paridad.

## 9. Referencia: mapa origen → destino del SSIS actual

| Destino | Origen (iSeries) | Tipo de carga prevista |
|---|---|---|
| `TMP_TERCEROS` | `gnbweb_db.psa065 where psa065es='A'` (39 columnas; el destino tiene 79) | snapshot (**hecha**) |
| `TMP_DATOSADICIONALESTERCERO` | `gnbweb_db.psa065` + `psa002` (`UNION`, flag `LISTAS_RESTRICTIVAS`) | snapshot |
| `TMP_PRODUCTOSTERCERO` | `gnbweb_db.psa066`, 3 ramas `UNION` por `psa066mo` | snapshot con parámetros |
| `TMP_TIPSTRAN` | `GNBWEB_DB.psa068 where psa068st='A'` | snapshot |
| `TMP_TRANSACCIONES` | `gnbweb_db.psa067`, 3 ramas `UNION` por `psa067mo` | incremental particionado |
| `TMP_FUNCIONARIOS` (deshabilitada en el SSIS) | `bantotaldb.fst010 where pgcod=1` | por confirmar si se necesita |

Orden de carga del SSIS (`MASTER_ETL` → `TRX_Transacciones`): TERCEROS → DATOSADICIONALES → PRODUCTOS → TIPSTRAN → TRANSACCIONES.

## 10. Definición de terminado (por tabla)

- Entrada en `config.yaml`, SQL de origen, DDL de staging y SQL de paridad.
- Pruebas en verde, incluida idempotencia, rollback ante fila inválida y `--dry-run` sin tocar el destino.
- Datos falsos con casos borde en `dev/seed_dev.py`.
- `README.md` y `PLAN_DESARROLLO.md` actualizados.
- Lista de lo que queda por validar en el equipo B.
