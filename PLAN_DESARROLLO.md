# Plan de desarrollo

Estado al 2026-10-16 (94 pruebas en SQLite: `python -m pytest`). Todo lo que depende de iSeries o SQL Server reales se ha ido
validando en el equipo B con las salidas de `medir`, `comparar` y las corridas `--dry-run` / reales; lo pendiente está abajo.

## Hecho
- **Tablas:** TERCEROS, DATOSADICIONALES, PRODUCTOS, TIPSTRAN y TRANSACCIONES (snapshot: staging temporal -> validaciones -> una
  transaccion final). `todas` con orden configurable (`orden`, hoy TIPSTRAN primero) y resumen; codigos de salida 0/1/2/3.
- **Opciones por tabla** (`config.yaml` + `config.local.yaml`, que se mezcla encima y el instalador nunca pisa; las claves mal
  escritas se rechazan): `ignorar`, `mapeo`, `fechas`, `a_texto`, `texto_max`, `clave`, `lote`, `vaciado`, `staging` (auto =
  tabla temporal con la estructura del destino), `contar_origen`, `limpiar_al_final`, `escritura`, `espera_bloqueo_seg`,
  `tablock`, `reconstruir_indices`, `hilos_escritura`, `lectores`, `particion`, `fecha_defecto`, `permitir_origen_vacio`.
- **TRANSACCIONES por fecha:** sin parametros se lee toda la tabla de origen (`fecha_defecto: ninguna`; `ayer|hoy` opcionales); para acotar `--fecha`, `--desde/--hasta`,
  `--mes`, `--todas-las-fechas`. El SQL de origen lleva `@@FILTRO_FECHA@@` (vacio = sin efecto). Lectura paralela por particiones
  de fecha y, para un solo dia, por el resto de `psa067co`; escritura paralela a una tabla temporal global (`##STG_*`).
- **Registro:** `VIGIA.ERRORESPROCESO` (PROCESO, TAREA, ERROR, DATOSERROR en trozos de 500, FECADI, USUADI), `VIGIA.PROCESOS`
  (INICIO/FIN por tabla y global, duracion ISO) y la tabla de ejecuciones; vinculadas por `corrida=`. Panel HTML de solo lectura
  (`main.py panel`).
- **Diagnostico:** `conexiones`, `describir`, `medir` (lectura, conversion, escritura, indices y triggers), `comparar` (dos SQL de
  origen sobre las mismas fechas); errores con etapa, tiempos por fase y diagnostico de columnas (fechas, largos, NULL, precision).
- **Instaladores:** `installer/build_ps1.ps1` genera `Instalar_etl_vigia_py.ps1` (autocontenido, sin bases de datos; ya no hay .exe).
  Manual: `MANUAL_INSTALACION_Y_EJECUCION.md`.

## Resultados medidos (equipo B; fecha con 829.830 filas)
7,5 min (1 hilo, en serie) -> 3,0 (4 escritores) -> 2,2 (4 lectores + 4 escritores) -> 1,9 (+ `reconstruir_indices`, diferencia dentro
de la variacion normal). Lectura ODBC con un techo de ~12.000 filas/s (red o driver; el iSeries resuelve la consulta en 2-4 s);
`INSERT` final 38-58 s con los 2 indices del destino. `escritura: valores` (INSERT multi-fila) y tipos explicitos de parametros
NO mejoran; `TABLOCK` no cambia el tiempo. Sin `fast_executemany` es ~15 veces mas lento.

## Corrida completa del 2026-10-16 (equipo B, `todas`, TRANSACCIONES toda la tabla = 7.282.604 filas)
Tiempo real ~21 min (TRANSACCIONES ~17: staging 805 s, INSERT 19 s, indices 194 s). El FIN decia 81 min por un hueco de 64 min
entre dos lineas de log: el proceso quedo detenido escribiendo en la consola (modo seleccion de Windows) con la transaccion abierta.
Corregido: el log por consola va por una cola con hilo propio y se desactiva la seleccion rapida. Ademas, con pocos meses en el origen
(aqui uno solo) las particiones pasan de mensuales a diarias para que los 4 lectores trabajen hasta el final (antes leia 1 solo al final,
~5.000 f/s). Por medir en el equipo B: tiempo de staging con particiones diarias y `reconstruir_indices` si/no con 7 M filas.

## Promoción TMP_* → definitivas (nuevo grupo, `promover`)
- Hecho: `etl/promocion.py` + `promociones:` en `config.yaml`; TRANSACCIONES reemplaza `VIGIA.PR_CARGATRANSACCIONES` (por conjuntos,
  validadores `VAL*` por argumentos distintos, lotes con aislamiento de filas erróneas, alertas, rechazos a ERRORESPROCESO, uPROCESOS).
  8 pruebas en SQLite (`tests/test_promocion.py`); las funciones `VAL*` se simulan en dev (`etl/dev_funciones.py`).
- **Validar en el equipo B (no se pudo aquí):** ejecutar con `--dry-run` y comparar pendientes/rechazos con el procedimiento sobre la misma TMP;
  collation de `VSDNLIST.NUMIDE`; `SET DATEFORMAT mdy` en las alertas (columna `FECHA` de `RESULTADOALERTASPROGRAMADAS` y `FN_NXTVAL`);
  permisos de `INSERT` en `TRANSACCIONES`/`RESULTADOALERTASPROGRAMADAS`/`EXEC PR_GENMAIL`; tiempo con volumen real (las funciones `VAL*` y
  el `NOT EXISTS` con `COLLATE` sobre `TRANSACCIONES` son lo más costoso); decidir si `TELEFONOREMITE` debe insertarse.
- Siguientes programas del grupo (uno por procedimiento `PR_CARGA*` que entregue el usuario).

## Pendiente
- **TERCEROS:** corrida real con `hilos_escritura` (el 2026-10-15 una corrida tardo >151 min sin terminar; el `--dry-run` tardo 7,6 min,
  asi que el problema estaba en la fase final y no se reprodujo en TRANSACCIONES). Con las fases del log se puede ubicar.
- **Indice por fecha en el origen:** `PSA067` solo tiene `PSA06701` (PA, TD, DO, MC, NC, SO, TX, FC, CO) y `PSA06702` (CI). Se pidio crear
  `GNBWEB_DB.PSA06703 (PSA067FC)`; con el indice, las particiones por dia y la corrida de una fecha mejoran.
- **Opciones del driver ODBC** (bloques de registros, compresion) con `ORIGEN_OPCIONES`: probar y medir.
- **Script optimizado de origen:** `sql/origen/transacciones_optimizado.sql` (una lectura, resultado IGUAL verificado con `comparar` en
  una fecha); decidir si se adopta (`sql:` en `config.local.yaml`).
- Probar `todas` completo sin `--fecha` (debe leer toda la tabla de TRANSACCIONES) y la paridad contra el SSIS (`sql/paridad/*.sql`).
- **T7** alertas (correo/Teams) si el codigo de salida != 0.
- Decision de T1: ¿aborto por defecto o "ignorar con aviso"? Hoy aborta.
- Los `CREATE TABLE` de `sql/destino/` son borradores (dev); los reales estan en `Archivos_SQL` (OneDrive).
- Los mensajes de error enmascaran literales entre comillas: revisar que ningun driver filtre datos personales.

## Directrices acordadas
- Ver `CLAUDE.md` (reglas) y la seccion "Directrices acordadas" alli.
