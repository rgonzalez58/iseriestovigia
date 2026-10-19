"""Diferencias SQL Server vs SQLite (dev)."""


def es_sqlserver(cfg):
    return cfg.entorno == "prod"


def sql_vaciar(cfg, tabla, modo="truncate"):
    if es_sqlserver(cfg) and modo == "truncate":
        return f"TRUNCATE TABLE {tabla}"
    return f"DELETE FROM {tabla}"


def sql_insert_staging(cfg, tabla, columnas):
    cols = ", ".join(columnas)
    marcas = ", ".join("?" for _ in columnas)
    return f"INSERT INTO {tabla} ({cols}) VALUES ({marcas})"


def sql_insert_select(cfg, destino, staging, cols_destino, cols_staging, tablock=True):
    """tablock=False quita el hint: sin él no hay registro mínimo, que obliga a volcar las páginas al disco en el
    commit (puede tardar minutos con volúmenes grandes y disco lento); a cambio se escribe más log."""
    hint = " WITH (TABLOCK)" if es_sqlserver(cfg) and tablock else ""
    return (f"INSERT INTO {destino}{hint} ({', '.join(cols_destino)}) "
            f"SELECT {', '.join(cols_staging)} FROM {staging}")


def sql_columnas(tabla):
    """Devuelve 0 filas; los nombres salen de cursor.description."""
    return f"SELECT * FROM {tabla} WHERE 1 = 0"


def sql_ahora(cfg):
    return "GETDATE()" if es_sqlserver(cfg) else "CURRENT_TIMESTAMP"


def nombre_staging(cfg, nombre, compartido=False):
    """Staging temporal. Con varias conexiones escritoras (compartido) debe verlo toda conexión: ## en SQL Server;
    en dev, una tabla real de VIGIA que se borra al terminar."""
    if es_sqlserver(cfg):
        return f"##STG_{nombre}" if compartido else f"#STG_{nombre}"
    return f"VIGIA.STGPAR_{nombre}" if compartido else f"temp.STG_{nombre}"


def sql_crear_staging(cfg, stg, destino, columnas):
    """Tabla temporal (de esta conexión, o global/compartida) con la estructura de las columnas del destino."""
    cols = ", ".join(columnas)
    if es_sqlserver(cfg):
        return f"SELECT {cols} INTO {stg} FROM {destino} WHERE 1 = 0"
    if stg.lower().startswith("temp."):
        return f"CREATE TEMP TABLE {stg.split('.')[-1]} AS SELECT {cols} FROM {destino} WHERE 0"
    return f"CREATE TABLE {stg} AS SELECT {cols} FROM {destino} WHERE 0"


def sql_usuario(cfg):
    return "SYSTEM_USER" if es_sqlserver(cfg) else "'dev'"


# ---------------------------------------------------------------- escritura por VALORES (menos viajes de red)
def _literal(cfg, v):
    """Literal SQL de un valor ya convertido. Las comillas se duplican; en SQL Server el texto va como N'...'."""
    from datetime import date, datetime
    from decimal import Decimal
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, Decimal)):
        return format(v, "f") if isinstance(v, Decimal) else str(v)
    if isinstance(v, float):
        return "NULL" if v != v or v in (float("inf"), float("-inf")) else repr(v)
    if isinstance(v, (datetime, date)):
        v = v.strftime("%Y%m%d %H:%M:%S") if isinstance(v, datetime) else v.strftime("%Y%m%d")
    texto = str(v).replace("\x00", "").replace("'", "''")
    return f"N'{texto}'" if es_sqlserver(cfg) else f"'{texto}'"


def sqls_insert_valores(cfg, tabla, columnas, filas):
    """INSERT multi-fila (máx. 1000 filas por sentencia en SQL Server, 500 en SQLite). Devuelve la lista de sentencias."""
    tope = 1000 if es_sqlserver(cfg) else 500
    cabecera = f"INSERT INTO {tabla} ({', '.join(columnas)}) VALUES "
    return [cabecera + ", ".join("(" + ", ".join(_literal(cfg, v) for v in f) + ")" for f in filas[i:i + tope])
            for i in range(0, len(filas), tope)]


def expr_resto(cfg, columna, r):
    """|columna mod r| en el motor del origen (DB2 for i: MOD; SQLite de dev: %). ABS cubre los negativos."""
    return f"ABS(MOD({columna}, {r}))" if es_sqlserver(cfg) else f"ABS({columna} % {r})"


def sql_indices_no_agrupados(cfg):
    """Consulta (SQL Server) de los índices no agrupados y activos de una tabla; parámetro: nombre de la tabla."""
    return ("SELECT name FROM sys.indexes WHERE object_id = OBJECT_ID(?) AND index_id > 1 AND is_disabled = 0 "
            "AND is_hypothetical = 0 ORDER BY index_id")


def sql_alter_indice(destino, nombre, accion):
    return f"ALTER INDEX [{nombre.replace(']', ']]')}] ON {destino} {accion}"
