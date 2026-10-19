"""Conexiones. pyodbc solo se importa en prod; en dev se usa SQLite (dev_data/)."""
import sqlite3

from .config import cadena_odbc_destino, cadena_odbc_origen
from .dialecto import es_sqlserver


def _sqlite_con_esquemas(cfg, archivo, esquemas):
    conn = sqlite3.connect(":memory:", timeout=30)
    for esquema in esquemas:
        conn.execute(f"ATTACH DATABASE ? AS {esquema}", (str(cfg.dev_dir / archivo),))
    return conn


def conectar_origen(cfg):
    if es_sqlserver(cfg):
        import pyodbc
        # Solo lectura: nunca se escribe en iSeries.
        return pyodbc.connect(cadena_odbc_origen(cfg), readonly=True, autocommit=True)
    conn = _sqlite_con_esquemas(cfg, "origen.db", ["gnbweb_db", "bantotaldb"])
    conn.execute("PRAGMA query_only = ON")
    return conn


def conectar_destino(cfg):
    if es_sqlserver(cfg):
        import pyodbc
        conn = pyodbc.connect(cadena_odbc_destino(cfg), autocommit=False)
        # El login puede tener idioma Español (dmy): se fija ymd en esta sesión para que las fechas no dependan de él.
        conn.cursor().execute("SET DATEFORMAT ymd")
        return conn
    conn = _sqlite_con_esquemas(cfg, "destino.db", ["VIGIA"])
    from .dev_funciones import registrar        # simula las funciones VIGIA.VAL* de SQL Server
    registrar(conn)
    return conn


def activar_fast_executemany(cfg, cursor):
    if es_sqlserver(cfg):
        cursor.fast_executemany = True
