-- VIGIA.uPROCESOS: métricas exactas de cada ejecución (una fila por proceso masivo y por tabla).
-- Convive con VIGIA.PROCESOS (no se modifica). Se enlaza con VIGIA.ERRORESPROCESO por PROCESO + CORRIDA.
-- Los tiempos se miden con reloj monotónico en el equipo del ETL (ms exactos); INICIO/FIN son la hora de ese equipo.
CREATE TABLE VIGIA.uPROCESOS (
    ID                      bigint IDENTITY(1,1) NOT NULL,
    CORRIDA                 varchar(20)  NOT NULL,   -- AAAAMMDD_HHMMSS: agrupa un proceso masivo y sus tablas
    PROCESO                 varchar(100) NOT NULL,   -- = PROCESOS.NOMBRE = ERRORESPROCESO.PROCESO (ETL_VIGIA_PY.TERCEROS / .TODAS)
    TIPO                    varchar(10)  NOT NULL,   -- TODAS | TABLA
    ID_PADRE                bigint       NULL,       -- ID de la fila TODAS cuando la tabla corrió dentro de 'todas'
    TABLA_DESTINO           varchar(128) NULL,
    ESTADO                  varchar(20)  NOT NULL,   -- EN CURSO | OK | DRY-RUN | ERROR | ABORTADO
    CODIGO_SALIDA           int          NULL,       -- 0 OK, 1 error, 3 abortado por validación
    ETAPA_FINAL             varchar(50)  NULL,       -- última etapa alcanzada (donde falló si ESTADO <> OK)
    INICIO                  datetime2(3) NOT NULL,
    FIN                     datetime2(3) NULL,
    DURACION_MS             bigint       NULL,
    MS_CONTAR_ORIGEN        bigint       NULL,
    MS_EJECUTAR_ORIGEN      bigint       NULL,
    MS_ESTRUCTURA_DESTINO   bigint       NULL,
    MS_CONTEO_DESTINO       bigint       NULL,
    MS_CARGAR_STAGING       bigint       NULL,       -- lectura del iSeries + conversión + inserción en staging
    MS_VALIDAR_STAGING      bigint       NULL,
    MS_TRANSACCION_DESTINO  bigint       NULL,       -- vaciar destino + INSERT...SELECT + verificación
    FILAS_ORIGEN            bigint       NULL,
    FILAS_CARGADAS          bigint       NULL,
    FILAS_DESTINO_ANTES     bigint       NULL,
    FILAS_POR_SEG           AS (CASE WHEN DURACION_MS > 0 THEN CAST(FILAS_CARGADAS * 1000.0 / DURACION_MS AS decimal(18,2)) END),
    DRY_RUN                 bit          NOT NULL CONSTRAINT DF_uPROCESOS_DRY DEFAULT (0),
    FORZAR                  bit          NOT NULL CONSTRAINT DF_uPROCESOS_FORZAR DEFAULT (0),
    USUARIO                 varchar(50)  NOT NULL CONSTRAINT DF_uPROCESOS_USU DEFAULT (SYSTEM_USER),
    EQUIPO                  varchar(128) NULL,
    MENSAJE                 varchar(max) NULL,       -- resumen sin datos personales
    CONSTRAINT PK_uPROCESOS PRIMARY KEY CLUSTERED (ID)
);
GO
CREATE NONCLUSTERED INDEX IX_uPROCESOS_CORRIDA ON VIGIA.uPROCESOS (CORRIDA, PROCESO);
CREATE NONCLUSTERED INDEX IX_uPROCESOS_INICIO  ON VIGIA.uPROCESOS (INICIO DESC) INCLUDE (PROCESO, ESTADO, DURACION_MS);
GO
-- Errores enlazados: ERRORESPROCESO.DATOSERROR siempre empieza por "corrida=<CORRIDA> ".
CREATE VIEW VIGIA.V_uPROCESOS_ERRORES AS
SELECT u.ID, u.CORRIDA, u.PROCESO, u.TIPO, u.ESTADO, u.INICIO, u.FIN, u.DURACION_MS,
       e.FECADI AS ERROR_FECHA, e.ERROR AS ERROR_ESTADO, e.TAREA, e.DATOSERROR, e.USUADI
FROM VIGIA.uPROCESOS u
JOIN VIGIA.ERRORESPROCESO e
  ON e.PROCESO = u.PROCESO
 AND e.DATOSERROR LIKE 'corrida=' + u.CORRIDA + ' %';
GO
