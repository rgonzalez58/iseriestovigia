# etl_vigia — Portabilidad entre maquinas

## Que cambio
- **Project.params**: 10 parametros de proyecto.
  - Destino: `Destino_Driver`, `Destino_Servidor`, `Destino_Base`, `Destino_Usuario`, `Destino_Password` (sensible)
  - Origen iSeries: `Origen_DSN`, `Origen_Usuario`, `Origen_Password` (sensible)
  - Productos: `Productos_GrupoA`, `Productos_GrupoB` (listas separadas por coma, sin espacios)
- **Connection managers** (TRX_Transacciones y PKG_LOG_EVENTO): `ConnectionString` y `Password` ahora son expresiones sobre los parametros. Ya no hay servidor/usuario fijos ni claves cifradas con DPAPI en los .dtsx.
- **Listas de productos**: `TMP_PRODUCTOSTERCERO` y `TMP_TRANSACCIONES` arman el `SqlCommand` por expresion (`REPLACE` sobre `@@PROD_A@@` / `@@PROD_B@@`). Las 6 listas `IN (...)` repetidas pasaron a 2 parametros.
- **ProtectionLevel = DontSaveSensitive** en los 4 paquetes y en el manifiesto del dtproj (coincide con el proyecto).
- Respaldo de los originales: `_backup_pre_parametros/`.

## Llevar el proyecto a otra maquina
1. Copiar al proyecto de destino (reemplazando): `etl_vigia/*.dtsx`, `etl_vigia/Project.params`, `etl_vigia/etl_vigia.dtproj`. (El zip `etl_vigia_cambios_parametros.zip` trae solo esos archivos.)
2. En la maquina destino verificar:
   - Driver SQL Server instalado. Si no esta SQL Server Native Client 11.0, cambiar `Destino_Driver` (ej. `ODBC Driver 17 for SQL Server`).
   - DSN ODBC de 64 o 32 bits segun el modo de ejecucion, con el nombre de `Origen_DSN` (por defecto `Normativo`).
3. Abrir `etl_vigia.sln` en Visual Studio + SSDT > `Project.params` > ajustar valores > cargar las dos contrasenas (no se guardan en disco).
4. Compilar y ejecutar. Por linea de comandos:
   `dtexec /Project etl_vigia.ispac /Package MASTER_ETL.dtsx /Par $Project::Destino_Password;"***" /Par $Project::Origen_Password;"***"`
   (agregar `/Par $Project::Destino_Servidor;otro.host` para cambiar otros valores).
   Si se despliega a SSISDB, definir los valores en un Environment.

## Pendiente de validar en SSDT (no se pudo probar sin Visual Studio)
- Abrir el proyecto y confirmar que no hay errores; "Test Connection" en los 3 connection managers con las contrasenas cargadas.
- Si SSDT reescribe el XML al guardar es normal (GUID de version, etc.).
- Ejecutar primero contra un destino de prueba.
