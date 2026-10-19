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

Ver `PLAN_DESARROLLO.md` (estado, resultados medidos y pendientes) y `MANUAL_INSTALACION_Y_EJECUCION.md`. Resumen: las 5 tablas
(TERCEROS, DATOSADICIONALES, PRODUCTOS, TIPSTRAN, TRANSACCIONES) estan implementadas y probadas (`python -m pytest`, 94 pruebas en SQLite)
y validadas en el equipo B; TRANSACCIONES lee toda la tabla de origen salvo que se indique fecha (`--fecha/--desde/--hasta/--mes`) con lectura y escritura en paralelo.

Modulos: `main.py` (CLI), `etl/config.py` (config.yaml + config.local.yaml + .env), `etl/snapshot.py` (carga), `etl/carga_paralela.py`
(lectores/escritores), `etl/particiones.py` (filtros por fecha), `etl/runner.py` (lock, conexiones, registro, `todas`), `etl/logger.py`,
`etl/dialecto.py`, `etl/db.py`, `etl/diagnostico.py` (`conexiones`, `describir`, `medir`, `comparar`), `etl/panel.py` + `panel.html`.

Codigos de salida: `0` OK, `1` error, `2` ya en ejecucion, `3` abortado por validacion/configuracion (el destino no se modifico).

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

## 5. Tareas pendientes

Ver `PLAN_DESARROLLO.md` ("Pendiente"). Las tareas T1-T6 originales estan hechas; quedan T7 (alertas), la decision de T1 y lo medible en el
equipo B (TERCEROS real, indice por fecha en el origen, opciones del driver ODBC, paridad con el SSIS).

## Directrices acordadas (usuario)
- **Regla de datos:** en cada corrida `TMP_*` contiene solo lo de esa corrida (se reemplaza todo el contenido). Proceso diario; TRANSACCIONES
  sin parametros se lee TODA la tabla de origen (se asume que solo contiene lo que hay que transmitir); con `--fecha/--desde/--hasta/--mes` se acota (`fecha_defecto: ayer|hoy` opcional en config.local.yaml).
- `limpiar_al_final` (vaciar TMP al terminar bien) existe pero esta apagado; vaciado por defecto `truncate` (confirmado por el usuario).
- Ajustes propios de cada equipo van en `config.local.yaml` (nunca se sobrescribe); `config.yaml` es de la version.
- Registro en `VIGIA.ERRORESPROCESO` (PROCESO = NOMBRE de `PROCESOS`, TAREA, FECADI=GETDATE(), USUADI=SYSTEM_USER; mensajes largos en
  varios registros de 500) y `VIGIA.PROCESOS`; inicio y fin con tiempo ISO por tabla y global.
- Optimizar de forma **medida**: usar `medir`, `comparar` y el desglose de fases del log antes de cambiar algo; no afirmar mejoras sin dato.
- Si una solicitud afecta el rendimiento o la eficiencia, avisar y dar alternativas.
- Copiar lo desarrollado y probado a `C:\Users\usuario\OneDrive\ETL` (el equipo B ejecuta desde ahi) y regenerar el instalador
  (`installer/build_ps1.ps1`, que siempre reconstruye el payload); no incluir bases de datos ni `.env`.

## 6. Cómo probar

```
pip install -r requirements-dev.txt
python dev/seed_dev.py --filas 5000          # crea dev_data/ (ya no hace falta si se usan los fixtures de pytest)
python -m pytest                             # debe quedar en verde
python main.py ejecutar --tabla TERCEROS     # contra las bases simuladas (ETL_ENTORNO=dev es el valor por defecto)
python main.py describir --tabla TERCEROS
```

Los fixtures de `tests/conftest.py` crean un `dev_data` temporal por prueba; usarlos para toda prueba nueva (`cfg`, `correr`, `consultar`, `origen_rw`).

## 7. Validado y pendiente en el equipo B

Validado: conexion al iSeries (DSN o `ORIGEN_DRIVER`/`ORIGEN_SISTEMA`), cargas de las tablas, fechas (`DATEFORMAT ymd` en la sesion y formato
`AAAA-MM-DD 00:00:00`), `fast_executemany`, escritura/lectura en paralelo, registro en `ERRORESPROCESO`/`PROCESOS`. Pendiente: ver
`PLAN_DESARROLLO.md`. SQLite no reproduce `CHAR` de DB2 ni los tipos reales del driver: no afirmar que algo esta validado contra produccion si
solo paso en SQLite.

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
