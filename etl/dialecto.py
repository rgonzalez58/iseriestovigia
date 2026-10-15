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


def sql_insert_select(cfg, destino, staging, cols_destino, cols_staging):
    hint = " WITH (TABLOCK)" if es_sqlserver(cfg) else ""
    return (f"INSERT INTO {destino}{hint} ({', '.join(cols_destino)}) "
            f"SELECT {', '.join(cols_staging)} FROM {staging}")


def sql_columnas(tabla):
    """Devuelve 0 filas; los nombres salen de cursor.description."""
    return f"SELECT * FROM {tabla} WHERE 1 = 0"


def sql_ahora(cfg):
    return "GETDATE()" if es_sqlserver(cfg) else "CURRENT_TIMESTAMP"


def nombre_staging(cfg, nombre):
    return f"#STG_{nombre}" if es_sqlserver(cfg) else f"temp.STG_{nombre}"


def sql_crear_staging(cfg, stg, destino, columnas):
    """Tabla temporal (solo de esta conexión) con la estructura de las columnas del destino."""
    cols = ", ".join(columnas)
    if es_sqlserver(cfg):
        return f"SELECT {cols} INTO {stg} FROM {destino} WHERE 1 = 0"
    return f"CREATE TEMP TABLE {stg.split('.')[-1]} AS SELECT {cols} FROM {destino} WHERE 0"


def sql_usuario(cfg):
    return "SYSTEM_USER" if es_sqlserver(cfg) else "'dev'"
