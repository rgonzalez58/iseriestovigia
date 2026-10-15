# Plan de mejora del flujo y la ejecución — etl_vigia

Objetivo: que el ETL sea **repetible (sin duplicar), más rápido, observable y recuperable**.
Orden pensado por impacto/riesgo. Cada fase termina con una verificación objetiva.
No requiere probar conexiones: la validación es por conteos y comparación de resultados.

## Fase 1 — Corrección (prioridad máxima)
| # | Cambio | Cómo | Verificación |
|---|---|---|---|
| 1.1 | Re-ejecutar no debe duplicar | `Execute SQL Task` con `TRUNCATE TABLE VIGIA.TMP_x` antes de cada Data Flow (o uno al inicio de `TRX_Transacciones` para las 5 tablas). Rehabilitar/rehacer `Truncate_TMP_PT` y apuntarlo a la tabla correcta. | Ejecutar 2 veces: mismo conteo en cada `TMP_*`. |
| 1.2 | Manejo de fallo en MASTER | Restricciones de precedencia *On Failure* → `Log_Error` (usa `User::TareaError`/`Mensaje`) → `Log_Fin` con estado ERROR. Hoy un fallo puede dejar el log sin cerrar. | Forzar un error (tabla inexistente) y ver `ERRORESPROCESO`. |
| 1.3 | Decidir `TMP_FUNCIONARIOS` | Habilitarlo en la cadena o eliminarlo. Hoy está muerto. | Revisión con negocio. |

## Fase 2 — Rendimiento (mayor ganancia)
| # | Cambio | Detalle | Riesgo |
|---|---|---|---|
| 2.1 | **Una lectura en vez de tres** en `psa066` y `psa067` | Las 3 ramas `UNION` solo difieren en cómo arman `NUMERO`/`PRODUCTO_NUMERO`. Reemplazar por un solo `SELECT` con `CASE` sobre `psa06xmo` (grupo A → `op-so`; grupo B → `nc-op`; resto → `nc-so`). `psa067` es la tabla más grande: pasa de 3 escaneos a 1. | Medio: validar que el resultado es idéntico. |
| 2.2 | `UNION` → `UNION ALL` o `DISTINCT` | Las ramas son disjuntas por producto, así que el `UNION` solo sirve para quitar duplicados exactos dentro de una rama. Medir si existen (`COUNT(*)` vs `COUNT(DISTINCT …)`); si no hay, `UNION ALL`. | **Requiere validar con negocio.** |
| 2.3 | Carga incremental de `TMP_TRANSACCIONES` | Tabla `ETL_CONTROL(Proceso, Tabla, UltimaFecha)` en SQL Server; filtrar por `psa067fc > UltimaFecha − N días` (ventana de seguridad). Las demás tablas son chicas: seguir con carga completa. | Medio: definir con negocio si hay correcciones retroactivas. |
| 2.4 | Destino más rápido | Cambiar `ODBC Destination` (lotes de 1000) por `OLE DB Destination` con *Fast Load*, `TABLOCK`, filas por lote 10 000–100 000. Quitar/deshabilitar índices no clustered de `TMP_*` durante la carga. | Bajo. |
| 2.5 | Paralelismo | `TERCEROS`, `DATOSADICIONALES`, `TIPSTRAN` y `PRODUCTOS` no dependen entre sí (solo escriben a `TMP_*`): quitar la cadena secuencial y correrlos en paralelo; `TRANSACCIONES` al final o en paralelo. Subir `MaxConcurrentExecutables`. Confirmar antes que ningún proceso posterior exija un orden. | Bajo. |
| 2.6 | Sin transformaciones en el pipeline | Mover el `Data Conversion` de `TMP_TRANSACCIONES` al `SELECT` (`CAST` en DB2) o a los tipos de la tabla destino. Seleccionar solo las columnas necesarias. | Bajo. |
| 2.7 | Buffers | Ajustar `DefaultBufferMaxRows` / `DefaultBufferSize` (p. ej. 50–100 MB) según el ancho de fila. | Bajo; medir. |
| 2.8 | Dónde corre | Ejecutar el paquete en el servidor cercano a SQL Server, no desde un equipo de desarrollo. | — |

## Fase 3 — Observabilidad
- Tabla `ETL_LOG_EJECUCION`: proceso, tarea, inicio, fin, filas leídas, filas escritas, estado.
- Guardar el `Row Count` de **todas** las tareas (hoy solo `TMP_TRANSACCIONES`) y una conciliación: `COUNT(*)` origen vs destino.
- Salida de errores de las filas (hoy `FailComponent` aborta todo): redirigir a tabla de rechazos con la fila y el motivo.

## Fase 4 — Operación
- Desplegar a **SSISDB** (modelo de proyecto) con Environment para los parámetros y las contraseñas.
- Ejecutar desde un **Job de SQL Server Agent** con horario, reintento y alerta por correo (Database Mail).
- Reiniciar desde la tarea fallida (checkpoints) solo si la carga completa tarda demasiado; si no, no vale la complejidad.
- Poner el proyecto bajo git.

## Fase 5 — Hacia la carga final
Aclarar cómo pasan los datos de `TMP_*` a las tablas finales de VIGIA (no está en este proyecto). Lo ideal: procedimiento almacenado (MERGE) invocado como último paso del MASTER, para que todo sea un solo flujo.

## Cómo comprobar que no se rompió nada (antes/después)
1. Guardar el estado actual de cada `TMP_*` (conteo y suma de verificación, p. ej. `CHECKSUM_AGG(BINARY_CHECKSUM(*))`).
2. Aplicar un cambio, volver a cargar y comparar con `EXCEPT` en ambos sentidos.
3. Registrar el tiempo antes/después por tarea en `ETL_LOG_EJECUCION`.

## Orden recomendado de ejecución
1. 1.1 y 1.2 · 2. Fase 3 (para medir) · 3. 2.4, 2.5 y 2.6 · 4. 2.1 · 5. 2.2 y 2.3 (con validación de negocio) · 6. Fases 4 y 5.
