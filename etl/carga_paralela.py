"""Carga a staging con varias conexiones: lectores del origen y escritores del destino.

- Escritores (hilos_escritura = N): N conexiones a SQL Server insertan lotes de una cola de tamaño limitado en una tabla
  temporal global (##) o compartida que todas ven.
- Lectores (lectores = R > 1): R conexiones al iSeries leen, cada una, particiones de fecha distintas (cola de trabajo
  dinámica: la que termina toma la siguiente) y convierten en su propio hilo.
- Con un solo lector, el hilo principal lee el cursor ya abierto.
Cualquier error detiene a todos, se vacían las colas y se vuelve a lanzar tal cual (el destino no se toca).
"""
import queue
import threading
import time

from . import db


def cargar(cfg, tabla, cur_o, convertir, stg, cols_stg, ins, hilos, log, est, escribir_lote, diagnosticar,
           filtros=None, lectores=1):
    """Devuelve (filas_enviadas, fases_en_segundos). Lanza la primera excepción de un lector o escritor."""
    q = queue.Queue(maxsize=(hilos + lectores) * 2)
    parar = threading.Event()
    lock = threading.Lock()
    estado = {"escritas": 0, "t_escritura": 0.0, "error": None, "enviadas": 0, "t_lect": 0.0, "t_conv": 0.0,
              "t_espera": 0.0, "particiones": 0}

    def fallo(e):
        with lock:
            if estado["error"] is None:
                estado["error"] = e
        parar.set()

    def escritor():
        conn = None
        try:
            conn = db.conectar_destino(cfg)
            cur = conn.cursor()
            db.activar_fast_executemany(cfg, cur)
            while True:
                filas = q.get()
                if filas is None:
                    return
                if parar.is_set():
                    continue                       # hay un error: se vacía la cola sin escribir
                t = time.monotonic()
                try:
                    escribir_lote(cfg, cur, tabla, stg, cols_stg, ins, filas)
                    conn.commit()
                except Exception as e:  # noqa: BLE001
                    with lock:
                        primero = estado["error"] is None
                    if primero:
                        est["diag"] = diagnosticar(cur, stg, cols_stg, filas)
                    fallo(e)
                    continue
                with lock:
                    estado["escritas"] += len(filas)
                    estado["t_escritura"] += time.monotonic() - t
        except BaseException as e:  # noqa: BLE001  (p. ej. no pudo conectar)
            fallo(e)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    escritores = [threading.Thread(target=escritor, name=f"escritor-{i}", daemon=True) for i in range(hilos)]
    for h in escritores:
        h.start()

    def poner(item):
        while True:
            try:
                q.put(item, timeout=1)
                return True
            except queue.Full:
                if not any(h.is_alive() for h in escritores):
                    return False

    def leer_cursor(cur):
        """Lee un cursor completo hacia la cola. Devuelve False si hay que parar."""
        while not parar.is_set():
            t1 = time.monotonic()
            lote = cur.fetchmany(tabla.lote)
            t2 = time.monotonic()
            if not lote:
                with lock:
                    estado["t_lect"] += t2 - t1
                return True
            filas = [convertir(f) for f in lote]
            t3 = time.monotonic()
            ok = poner(filas)
            t4 = time.monotonic()
            with lock:
                estado["t_lect"] += t2 - t1
                estado["t_conv"] += t3 - t2
                estado["t_espera"] += t4 - t3
                if ok:
                    estado["enviadas"] += len(lote)
                    est["cargadas"] = estado["enviadas"]
            if not ok:
                return False
            if not lectores_h:
                progreso()                         # un solo lector (hilo principal): el progreso se informa aquí
        return False

    pendientes = queue.Queue()
    for f in filtros or []:
        pendientes.put(f)

    def lector():
        conn = None
        try:
            conn = db.conectar_origen(cfg)
            cur = conn.cursor()
            while not parar.is_set():
                try:
                    filtro = pendientes.get_nowait()
                except queue.Empty:
                    return
                cur.execute(cfg.sql_origen(tabla, filtro))
                if not leer_cursor(cur):
                    return
                with lock:
                    estado["particiones"] += 1
        except BaseException as e:  # noqa: BLE001
            fallo(e)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    lectores_h = [threading.Thread(target=lector, name=f"lector-{i}", daemon=True) for i in range(lectores)] \
        if filtros is not None else []
    t_ini = ultimo = time.monotonic()

    def progreso():
        nonlocal ultimo
        if time.monotonic() - ultimo < 30:
            return
        ultimo = time.monotonic()
        with lock:
            env, esc, part = estado["enviadas"], estado["escritas"], estado["particiones"]
        dt = max(ultimo - t_ini, 1e-9)
        extra = f", particiones {part}/{len(filtros)}" if filtros is not None else ""
        log.info("%s: %d filas leídas (%.0f f/s), %d escritas (%.0f f/s) con %d lectores y %d escritores%s | cola %d/%d",
                 tabla.nombre, env, env / dt, esc, esc / dt, max(1, len(lectores_h)), hilos, extra,
                 q.qsize(), q.maxsize)

    try:
        if lectores_h:
            for h in lectores_h:
                h.start()
            while any(h.is_alive() for h in lectores_h):
                for h in lectores_h:
                    h.join(timeout=1)
                progreso()
                if not any(h.is_alive() for h in escritores):
                    parar.set()
        else:
            leer_cursor(cur_o)
    except BaseException:
        parar.set()
        raise
    finally:
        for h in lectores_h:
            h.join()
        for _ in escritores:
            poner(None)
        for h in escritores:
            h.join()
    if estado["error"] is not None:
        raise estado["error"]
    enviadas = estado["enviadas"]
    if estado["escritas"] != enviadas:
        raise RuntimeError(f"los escritores confirmaron {estado['escritas']} filas y se enviaron {enviadas}")
    total = time.monotonic() - t_ini
    n_l = max(1, len(lectores_h))
    fases = {"lectura_origen": round(estado["t_lect"] / n_l, 1), "conversion": round(estado["t_conv"] / n_l, 1),
             "espera_cola": round(estado["t_espera"] / n_l, 1),
             "escritura_staging": round(estado["t_escritura"] / hilos, 1)}
    log.info("%s: staging cargado, %d filas en %.1f s con %d lectores y %d escritores (por conexión: lectura %.1f s, "
             "conversión %.1f s, espera de la cola %.1f s, escritura %.1f s)", tabla.nombre, enviadas, total, n_l,
             hilos, *fases.values())
    return enviadas, fases
