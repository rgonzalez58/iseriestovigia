# Plan de desarrollo

## Hecho (27 pruebas en SQLite, `python -m pytest`)
- Base del proyecto reconstruida desde `CLAUDE.md` (la carpeta solo traía ese archivo y el zip del SSIS).
- **T1** `ignorar` / `mapeo` con validaciones y pruebas.
- **T2** TIPSTRAN, **T3** DATOSADICIONALES, **T4** PRODUCTOS (sustitución `@@PROD_A@@/@@PROD_B@@`; valores tomados de `Project.params`). SQL extraído de `TRX_Transacciones.dtsx`.
- **T6** comando `todas` con resumen y código de salida.

## Pendiente
- **T5 TRANSACCIONES** (modo incremental particionado). Falta: volumen, columna de fecha o ID creciente, si hay bajas/actualizaciones. SQL de origen ya está en `sql/origen/transacciones.sql` (no registrado en `config.yaml`).
- **T7** alertas (correo/Teams) si el código de salida ≠ 0.
- Decisión de T1: ¿aborto por defecto o "ignorar con aviso"? Hoy aborta.

## Por validar en el equipo B (no validable con SQLite)
- Paridad contra SSIS (`sql/paridad/*.sql`).
- `CREATE TABLE` reales del destino (los de `sql/destino/` son borradores; TERCEROS real tiene 79 columnas) y columnas `NOT NULL` sin origen.
- Espacios U+2003 en el SQL copiado del SSIS: se normalizan a espacio al leerlo; confirmar que el resultado coincide con el SSIS.
- Tipos reales del driver ODBC (fechas `AAAAMMDD`, `Decimal`), `''` vs `'   '` en CHAR de DB2, `a_texto` en SUCURSAL_CODIGO, codificación, permisos `TRUNCATE`, `TABLOCK`, `fast_executemany`.
- Conversión de fechas: se envían como texto `AAAAMMDD`→`YYYY-MM-DD`; `FECHAAPERTURA='00000000'` pasa a `17530101` en el origen (se conserva).
- Supuesto del log: `VIGIA.ERRORESPROCESO(ERROR, DATOSERROR)`; confirmar DDL real.
- Los mensajes de error enmascaran literales entre comillas; revisar que ningún driver filtre datos personales.
