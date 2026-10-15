"""Comandos conexiones y describir. Muestran solo nombres de columna y tipos, nunca datos."""
from . import db
from .config import ErrorConfig
from .snapshot import enmascarar


def conexiones(cfg):
    ok = True
    for etiqueta, f in (("origen", db.conectar_origen), ("destino", db.conectar_destino)):
        etapa = "conexion"
        try:
            c = f(cfg)
            etapa = "consulta"
            # DB2 no admite SELECT sin FROM: se usa SYSIBM.SYSDUMMY1 en el origen real.
            prueba = "SELECT 1 FROM SYSIBM.SYSDUMMY1" if etiqueta == "origen" and db.es_sqlserver(cfg) else "SELECT 1"
            c.cursor().execute(prueba)
            c.close()
            print(f"{etiqueta}: OK")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"{etiqueta}: FALLA en {etapa} ({type(e).__name__})")
            print(f"  detalle: {enmascarar(e, 400)}")
    return 0 if ok else 1


def describir(cfg, nombre):
    try:
        tabla = cfg.tabla(nombre)
    except ErrorConfig as e:
        print(e)
        return 3
    origen = db.conectar_origen(cfg)
    cur = origen.cursor()
    cur.execute(cfg.sql_origen(tabla))
    print(f"{tabla.nombre}: columnas del SELECT")
    for d in cur.description:
        tipo = getattr(d[1], "__name__", str(d[1]))
        marca = " (ignorada)" if d[0].upper() in tabla.ignorar else ""
        destino = tabla.mapeo.get(d[0].upper())
        print(f"  {d[0]:32s} {tipo}{marca}" + (f" -> {destino}" if destino else ""))
    # Tipos mezclados: se inspeccionan los tipos Python de las primeras filas (sin imprimir valores).
    muestra = cur.fetchmany(1000)
    for i, d in enumerate(cur.description):
        tipos = {type(f[i]).__name__ for f in muestra if f[i] is not None}
        if len(tipos) > 1:
            print(f"  AVISO tipos mezclados en {d[0]}: {sorted(tipos)} (considerar a_texto)")
    origen.close()
    return 0
