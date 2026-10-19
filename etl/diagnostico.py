"""Comandos conexiones y describir. Muestran solo nombres de columna y tipos, nunca datos."""
import time
from dataclasses import replace

from . import db, dialecto
from .config import ErrorConfig
from .snapshot import _columnas, escribir_lote, _transformador, _validar_columnas, enmascarar


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


def _escribir(cur, ins, filas, lote, max_seg, escribir=None):
    """Escribe por lotes (el primero chico, para medir pronto) y se detiene al superar max_seg."""
    t0, hechas, k, paso = time.monotonic(), 0, 0, min(200, lote)
    while k < len(filas) and (time.monotonic() - t0) < max_seg:
        t = time.monotonic()
        if escribir is None:
            cur.executemany(ins, filas[k:k + paso])
        else:
            escribir(filas[k:k + paso])
        if hechas == 0:
            print(f"    primer lote de {len(filas[k:k + paso])} filas: {time.monotonic() - t:.2f} s", flush=True)
        hechas += len(filas[k:k + paso])
        k += paso
        paso = lote
    cur.connection.commit()
    return hechas, time.monotonic() - t0


def escribir_paralelo(conectar, ins, filas, hilos, lote, max_seg, preparar=None):
    """Reparte las filas entre `hilos` conexiones propias que escriben a la vez. Devuelve (filas escritas, segundos).
    `preparar(cursor)` se llama al abrir cada conexión (p. ej. activar fast_executemany)."""
    import threading
    parte = [filas[i::hilos] for i in range(hilos)]
    hechas, errores, lock = [0], [], threading.Lock()
    t0 = time.monotonic()

    def trabajo(mis_filas):
        try:
            conn = conectar()
            cur = conn.cursor()
            if preparar:
                preparar(cur)
            for k in range(0, len(mis_filas), lote):
                if time.monotonic() - t0 > max_seg:
                    break
                trozo = mis_filas[k:k + lote]
                cur.executemany(ins, trozo)
                conn.commit()
                with lock:
                    hechas[0] += len(trozo)
            conn.close()
        except Exception as e:  # noqa: BLE001
            with lock:
                errores.append(f"{type(e).__name__}: {enmascarar(e, 150)}")

    hs = [threading.Thread(target=trabajo, args=(p,)) for p in parte]
    for h in hs:
        h.start()
    for h in hs:
        h.join()
    if errores:
        raise RuntimeError("; ".join(sorted(set(errores))))
    return hechas[0], time.monotonic() - t0


def _tamanos_parametros(cfg, destino, tabla, columnas):
    """Para setinputsizes: columnas de texto del destino como SQL_VARCHAR(n); el resto, None (por defecto)."""
    if not dialecto.es_sqlserver(cfg):
        return None
    try:
        import pyodbc
        partes = tabla.destino.split(".")
        cur = destino.cursor()
        cur.execute("SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?", (partes[-2], partes[-1]))
        info = {r[0].upper(): (r[1].lower(), r[2]) for r in cur.fetchall()}
        out = []
        for c in columnas:
            tipo, largo = info.get(c, ("", None))
            out.append((pyodbc.SQL_VARCHAR, 0 if largo in (None, -1) else int(largo), 0)
                       if tipo in ("varchar", "char", "nvarchar", "nchar") else None)
        return out
    except Exception:  # noqa: BLE001
        return None


def medir_lectura_particionada(cfg, tabla, origen, lectores, filas, max_seg):
    """Lee las particiones más recientes (las de la carga real) con 1 y con R conexiones al iSeries (hasta `filas` filas o
    `max_seg` segundos por conexión) para ver si la lectura paralela por fecha escala. Solo lectura."""
    import threading
    from . import particiones
    if not tabla.particion or not cfg.tiene_filtro_fecha(tabla):
        print("  lectura por particiones: la tabla no tiene 'particion' ni @@FILTRO_FECHA@@ en su SQL (omitida)")
        return
    t = time.monotonic()
    tipo, dmin, dmax = particiones.detectar(origen, tabla.particion)
    print(f"  fechas de {tabla.particion['columna']}: {dmin} a {dmax} (tipo {tipo}); MIN/MAX tardó "
          f"{time.monotonic() - t:.2f} s", flush=True)
    # Las mismas particiones que usa la carga real (diarias si el origen trae pocos meses).
    filtros = particiones.construir_filtros(tabla.particion["columna"], tipo, dmin, dmax, lectores)
    meses = filtros[1:-2]                                       # sin 'anteriores', 'posteriores' ni 'nulas'
    print(f"  {len(filtros)} particiones en total; se prueban las más recientes", flush=True)
    for r in sorted({1, lectores}):
        if r > len(meses):
            continue
        res, errores = [], []

        def trabajo(filtro):
            try:
                conn = db.conectar_origen(cfg)
                cur = conn.cursor()
                t0 = time.monotonic()
                cur.execute(cfg.sql_origen(tabla, filtro))
                filas_ = cur.fetchmany(1)
                t_primera = time.monotonic() - t0
                n = len(filas_)
                while n < filas and time.monotonic() - t0 < max_seg:
                    lote = cur.fetchmany(min(tabla.lote, filas - n))
                    if not lote:
                        break
                    n += len(lote)
                res.append((n, time.monotonic() - t0, t_primera))
                conn.close()
            except Exception as e:  # noqa: BLE001
                errores.append(f"{type(e).__name__}: {enmascarar(e, 150)}")

        t0 = time.monotonic()
        hs = [threading.Thread(target=trabajo, args=(meses[-1 - i],)) for i in range(r)]
        for h in hs:
            h.start()
        for h in hs:
            h.join()
        total = time.monotonic() - t0
        if errores:
            print(f"  lectura por particiones, {r} conexiones: FALLA: {'; '.join(sorted(set(errores)))}", flush=True)
            continue
        n = sum(x[0] for x in res)
        print(f"  lectura por particiones, {r} conexión(es) al iSeries: {n} filas en {total:.2f} s = "
              f"{n / max(total, 1e-9):.0f} filas/s (hasta la primera fila: {max(x[2] for x in res):.2f} s como máximo)",
              flush=True)


def medir(cfg, nombre, filas=20000, max_seg=60, hilos=0, lectores=0):
    """Mide dónde se va el tiempo con una muestra: lectura del origen, conversión y escritura a una tabla
    temporal (con y sin fast_executemany). No modifica ninguna tabla real y no imprime datos."""
    try:
        tabla = cfg.tabla(nombre)
    except ErrorConfig as e:
        print(e)
        return 3
    origen, destino = db.conectar_origen(cfg), db.conectar_destino(cfg)
    try:
        t = time.monotonic()
        cur_o = origen.cursor()
        cur_o.execute(cfg.sql_origen(tabla))
        t_exec = time.monotonic() - t
        cols_origen = [d[0].upper() for d in cur_o.description]
        muestra, t_lect = [], 0.0
        while len(muestra) < filas:
            t = time.monotonic()
            lote = cur_o.fetchmany(min(tabla.lote, filas - len(muestra)))
            t_lect += time.monotonic() - t
            if not lote:
                break
            muestra.extend(lote)
        n = len(muestra)
        print(f"{tabla.nombre}: muestra de {n} filas, {len(cols_origen)} columnas")
        print(f"  origen: SELECT hasta la primera fila {t_exec:.2f} s; lectura {t_lect:.2f} s = "
              f"{n / max(t_lect, 1e-9):.0f} filas/s")
        if lectores > 0:
            medir_lectura_particionada(cfg, tabla, origen, lectores, filas, max_seg)
        if not n:
            return 0
        cols_destino = _columnas(destino, tabla.destino)
        mapa = _validar_columnas(tabla, cols_origen, cols_origen, cols_destino)
        convertir = _transformador(tabla, cols_origen, mapa, dialecto.es_sqlserver(cfg))
        t = time.monotonic()
        filas_c = [convertir(f) for f in muestra]
        t_conv = time.monotonic() - t
        print(f"  conversión en Python: {t_conv:.2f} s = {n / max(t_conv, 1e-9):.0f} filas/s")
        cols_stg = [d for _, d in mapa]
        bytes_fila = sum(len(str(v)) for f in filas_c[:1000] for v in f if v is not None) / max(1, min(n, 1000))
        print(f"  tamaño medio por fila: {bytes_fila:.0f} caracteres de datos")
        tamanos = _tamanos_parametros(cfg, destino, tabla, cols_stg)
        modos = [("fast_executemany", True, None, False),
                 ("INSERT multi-fila con valores, sin parámetros (escritura: valores)", True, None, True)]
        if dialecto.es_sqlserver(cfg):
            if tamanos:
                modos.append(("fast_executemany + tipos de parámetro explícitos (varchar)", True, tamanos, False))
            modos.append(("sin fast_executemany", False, None, False))
        for i, (etiqueta, rapido, sizes, valores) in enumerate(modos):
            stg = dialecto.nombre_staging(cfg, f"{tabla.nombre}_MEDIR{i}")
            cur = destino.cursor()
            if dialecto.es_sqlserver(cfg):
                cur.fast_executemany = rapido
            cur.execute(dialecto.sql_crear_staging(cfg, stg, tabla.destino, cols_stg))
            ins = dialecto.sql_insert_staging(cfg, stg, cols_stg)
            print(f"  escritura a tabla temporal, {etiqueta}:", flush=True)
            try:
                if sizes:
                    cur.setinputsizes(sizes)
                escribir = None
                if valores:
                    t_val = replace(tabla, escritura="valores")
                    escribir = lambda lote, c=cur, tv=t_val, s_=stg: escribir_lote(cfg, c, tv, s_, cols_stg, ins, lote)  # noqa: E731
                hechas, seg = _escribir(cur, ins, filas_c, tabla.lote, max_seg, escribir)
                print(f"    {hechas} filas en {seg:.2f} s = {hechas / max(seg, 1e-9):.0f} filas/s "
                      f"(≈ {hechas * bytes_fila / max(seg, 1e-9) / 1e6:.2f} MB/s de datos)"
                      + ("  (cortada por tiempo)" if hechas < n else ""), flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"    FALLA: {type(e).__name__}: {enmascarar(e, 200)}", flush=True)
                destino.rollback()
            try:
                cur.execute(f"DROP TABLE {stg}")
                destino.commit()
            except Exception:  # noqa: BLE001
                destino.rollback()
        if hilos > 1 and dialecto.es_sqlserver(cfg):
            # Tabla temporal GLOBAL (##): visible para todas las conexiones, desaparece al cerrar la que la creó.
            stg = f"##STG_{tabla.nombre}_MEDIR"
            cur = destino.cursor()
            cur.execute(dialecto.sql_crear_staging(cfg, stg, tabla.destino, cols_stg))
            destino.commit()
            ins = dialecto.sql_insert_staging(cfg, stg, cols_stg)
            for h in sorted({2, hilos}):
                cur.execute(f"TRUNCATE TABLE {stg}")
                destino.commit()
                print(f"  escritura PARALELA, {h} conexiones (fast_executemany) a tabla temporal global:", flush=True)
                try:
                    hechas, seg = escribir_paralelo(lambda: db.conectar_destino(cfg), ins, filas_c, h, tabla.lote,
                                                    max_seg, preparar=lambda c: setattr(c, "fast_executemany", True))
                    print(f"    {hechas} filas en {seg:.2f} s = {hechas / max(seg, 1e-9):.0f} filas/s", flush=True)
                except Exception as e:  # noqa: BLE001
                    print(f"    FALLA: {e}", flush=True)
            cur.execute(f"DROP TABLE {stg}")
            destino.commit()
        if dialecto.es_sqlserver(cfg):
            cur = destino.cursor()
            cur.execute("SELECT (SELECT COUNT(*) FROM sys.indexes WHERE object_id = OBJECT_ID(?) AND index_id > 1), "
                        "(SELECT COUNT(*) FROM sys.triggers WHERE parent_id = OBJECT_ID(?))",
                        (tabla.destino, tabla.destino))
            idx, trg = cur.fetchone()
            print(f"  destino {tabla.destino}: {idx} índices no agrupados, {trg} triggers "
                  f"(cada índice y trigger encarece el INSERT final)")
        print("  Lectura: la fase más lenta (filas/s) es el cuello de botella; proyecte con "
              "filas_totales / filas_por_segundo.")
        return 0
    finally:
        origen.close()
        destino.close()


def comparar(cfg, nombre, sql_alterno, fechas=None):
    """Compara el SQL de origen de la tabla con un SQL alterno sobre el MISMO filtro de fechas (solo lectura en el
    origen): filas de cada uno, filas que solo están en uno u otro, y tiempo de cada consulta. No imprime datos."""
    from dataclasses import replace
    from pathlib import Path
    from . import particiones
    try:
        tabla = cfg.tabla(nombre)
    except ErrorConfig as e:
        print(e)
        return 3
    alterno = Path(sql_alterno)
    if not alterno.is_absolute():
        alterno = cfg.raiz / alterno
    if not alterno.exists():
        print(f"No existe el archivo {alterno}")
        return 3
    otra = replace(tabla, sql=alterno)
    origen = db.conectar_origen(cfg)
    try:
        rango = particiones.resolver_rango(tabla, fechas)
        filtro = ""
        if rango:
            if not (cfg.tiene_filtro_fecha(tabla) and cfg.tiene_filtro_fecha(otra)):
                print("Ambos SQL deben tener @@FILTRO_FECHA@@ para comparar por fechas")
                return 3
            filtro = particiones.filtros_para(origen, replace(tabla, lectores=1), rango)[0]
        print(f"{tabla.nombre}: comparación sobre {particiones.texto_rango(rango)}")
        a, b = cfg.sql_origen(tabla, filtro), cfg.sql_origen(otra, filtro)
        res = {}
        for etiqueta, sql in (("actual", a), ("alterno", b), ("solo en actual", f"SELECT * FROM ({a}) x EXCEPT SELECT * FROM ({b}) y"),
                              ("solo en alterno", f"SELECT * FROM ({b}) y EXCEPT SELECT * FROM ({a}) x")):
            t = time.monotonic()
            cur = origen.cursor()
            cur.execute(f"SELECT COUNT(*) FROM ({sql}) q")
            res[etiqueta] = (int(cur.fetchone()[0]), time.monotonic() - t)
            print(f"  {etiqueta:16s} {res[etiqueta][0]:>10d} filas   {res[etiqueta][1]:7.2f} s", flush=True)
        igual = res["solo en actual"][0] == 0 and res["solo en alterno"][0] == 0
        print("  RESULTADO: " + ("IGUALES (mismo conjunto de filas)" if igual else "DIFERENTES: no usar el alterno sin revisar"))
        if res["actual"][1] > 0 and res["alterno"][1] > 0:
            print(f"  tiempo del COUNT: alterno = {res['alterno'][1] / res['actual'][1]:.2f} × el actual")
        return 0 if igual else 1
    finally:
        origen.close()
