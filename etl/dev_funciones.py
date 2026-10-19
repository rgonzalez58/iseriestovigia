"""Solo dev: simula en SQLite las funciones escalares VIGIA.VAL* de SQL Server.

Por defecto todo valor es válido. VIGIA.DEV_VALIDOS(VALIDADOR, CLAVE, VALIDO) fija excepciones: CLAVE son los
argumentos unidos por '|' (o '*' para cambiar el valor por defecto de ese validador). En producción se usan las funciones reales.
"""
import sqlite3

NOMBRES = {"VALTERCERO": 2, "VALPRODUCTOSTERCERO": 4, "VALTIPTRAN": 1, "VALESTADOOPERACION": 1, "VALSISGEO": 1,
           "VALSUCURSAL": 1, "VALTIPMON": 1, "VALINTERMEDIARIO": 1, "VALMEDIOSOPERACION": 1, "VALFORMASPAGO": 1,
           "VALDISPOSITIVO": 1}


def registrar(conn):
    try:
        filas = conn.execute("SELECT VALIDADOR, CLAVE, VALIDO FROM VIGIA.DEV_VALIDOS").fetchall()
    except sqlite3.Error:
        filas = []
    tabla = {(v.upper(), c): int(ok) for v, c, ok in filas}

    def crear(nombre):
        def f(*args):
            clave = "|".join("" if a is None else str(a) for a in args)
            return tabla.get((nombre, clave), tabla.get((nombre, "*"), 1))
        return f
    for nombre, n in NOMBRES.items():
        conn.create_function(nombre, n, crear(nombre))
