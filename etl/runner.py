"""Orquesta lock + conexiones + snapshot + log. Devuelve el código de salida.

0 OK | 1 error | 2 ya en ejecución | 3 abortado por validación (destino sin cambios)
"""
import time
from datetime import datetime

from . import db, dialecto
from .config import ErrorConfig
from .lock import Lock, YaEnEjecucion
from .metricas import Ejecucion
from .logger import PROCESO, crear_logger, registrar_ejecucion, registrar_proceso
from .snapshot import Aborto, enmascarar, ejecutar_snapshot


def nueva_corrida():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _registrar_global(cfg, log, resultado, ej=None, estado=None, codigo=None):
    """Conexión corta al destino solo para el registro global (no se mantiene abierta durante la carga).
    `ej` (Ejecucion global): al inicio inserta la fila de uPROCESOS; al fin (estado/código) la completa."""
    try:
        destino = db.conectar_destino(cfg)
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo registrar en PROCESOS (conexión): %s", type(e).__name__)
        return
    try:
        registrar_ejecucion(destino, log, "TODAS", resultado)
        registrar_proceso(cfg, destino, log, "TODAS", resultado.split()[0], resultado, f"{PROCESO}.TODAS")
        if ej is not None:
            if estado is None:
                ej.iniciar(destino)
            else:
                ej.terminar(destino, estado, codigo, resultado[:4000])
    finally:
        destino.close()


def _quiere_limpiar(tabla, limpiar):
    """La opción de la línea de comandos (si se indicó) manda sobre la de config.yaml."""
    return tabla.limpiar_al_final if limpiar is None else limpiar


def limpiar_tablas(cfg, tablas, log, corrida, destino=None):
    """Vacía las tablas destino al terminar el proceso (parámetro limpiar_al_final). Registra cada una.
    Devuelve 0 si todo fue bien, 1 si alguna falló."""
    propia = destino is None
    codigo = 0
    try:
        if propia:
            destino = db.conectar_destino(cfg)
        for t in tablas:
            pn = f"{PROCESO}.{t.nombre}"
            try:
                cur = destino.cursor()
                cur.execute(f"SELECT COUNT(*) FROM {t.destino}")
                n = int(cur.fetchone()[0])
                cur.execute(dialecto.sql_vaciar(cfg, t.destino, t.vaciado))
                destino.commit()
                msg = f"corrida={corrida} {t.nombre}: tabla vaciada al final del proceso ({n} filas)"
                log.info("LIMPIEZA %s", msg)
                registrar_proceso(cfg, destino, log, t.destino, "LIMPIEZA", msg, pn)
                registrar_ejecucion(destino, log, t.nombre, "LIMPIEZA " + msg.split(" ", 1)[0] + f" filas={n}")
            except Exception as e:  # noqa: BLE001
                try:
                    destino.rollback()
                except Exception:  # noqa: BLE001
                    pass
                codigo = 1
                msg = f"corrida={corrida} {t.nombre}: no se pudo vaciar: {type(e).__name__}: {enmascarar(e, 300)}"
                log.error("LIMPIEZA %s", msg)
                registrar_proceso(cfg, destino, log, t.destino, "ERROR", msg, pn)
    finally:
        if propia and destino is not None:
            destino.close()
    return codigo


def duracion_iso(segundos):
    """Duración en minutos con formato ISO 8601 (p. ej. PT12.50M)."""
    return f"PT{segundos / 60:.2f}M"


def ejecutar_tabla(cfg, nombre, dry_run=False, forzar=False, corrida=None, limpiar=None, desde_todas=False,
                   id_padre=None, fechas=None):
    log = crear_logger(cfg, "main")
    corrida = corrida or nueva_corrida()
    try:
        tabla = cfg.tabla(nombre)
        pn = f"{PROCESO}.{tabla.nombre}"      # igual a NOMBRE en VIGIA.PROCESOS: une ambas tablas
        with Lock(cfg.logs / f"{tabla.nombre}.lock"):
            origen = destino = None
            t0 = time.monotonic()
            fin = {"estado": "ERROR", "codigo": 1, "mensaje": None}
            est = {}
            ej = Ejecucion(cfg, log, corrida, tabla.nombre, "TABLA", tabla.destino, id_padre, dry_run, forzar)
            try:
                origen = db.conectar_origen(cfg)
                destino = db.conectar_destino(cfg)
                ej.iniciar(destino)
                registrar_proceso(cfg, destino, log, tabla.destino, "INICIO", f"corrida={corrida} {tabla.nombre}: inicio",
                                  pn)
                registrar_ejecucion(destino, log, tabla.nombre, f"INICIO corrida={corrida}")
                try:
                    r = ejecutar_snapshot(cfg, tabla, origen, destino, log, dry_run, forzar, est, fechas)
                except Aborto as e:
                    log.error("ABORTADO: %s", e)
                    registrar_proceso(cfg, destino, log, tabla.destino, "ABORTADO", f"corrida={corrida} {tabla.nombre}: {e}", pn)
                    fin.update(estado="ABORTADO", codigo=3, mensaje=f"{e}"[:2000])
                    return 3
                except Exception as e:  # noqa: BLE001
                    msg = (f"{tabla.nombre}: {e}" if isinstance(e, RuntimeError) and e.__cause__ is not None
                           else f"{tabla.nombre}: {type(e).__name__}: {enmascarar(e)}")
                    log.error("ERROR: %s", msg)
                    registrar_proceso(cfg, destino, log, tabla.destino, "ERROR", f"corrida={corrida} {msg}", pn)
                    fin.update(estado="ERROR", codigo=1, mensaje=msg[:2000])
                    return 1
                registrar_proceso(cfg, destino, log, tabla.destino, "OK" if r.estado == "OK" else "DRY-RUN",
                                  f"corrida={corrida} {tabla.nombre}: origen={r.origen} cargadas={r.cargadas}", pn)
                fin.update(estado=r.estado, codigo=0)
                # Limpieza final (solo si todo salió bien y no es dry-run). En 'todas' se hace al terminar el proceso.
                if not desde_todas and not dry_run and _quiere_limpiar(tabla, limpiar):
                    rc = limpiar_tablas(cfg, [tabla], log, corrida, destino)
                    if rc:
                        fin["estado"] = "ERROR"
                    return rc
                return 0
            finally:
                if destino is not None:
                    msg = (f"corrida={corrida} {tabla.nombre}: estado={fin['estado']} "
                           f"duracion={duracion_iso(time.monotonic() - t0)}")
                    # Tiempo por etapa y por fase de la carga (dónde se va el tiempo), solo nombres y segundos.
                    etapas = ";".join(f"{k.replace(' ', '_')}:{v / 1000:.0f}s" for k, v in est.get("ms", {}).items())
                    fases = ";".join(f"{k}:{v:.0f}s" for k, v in est.get("fases_s", {}).items())
                    if est.get("fechas"):
                        msg += f" fechas={est['fechas'].replace(' ', '')}"
                    if etapas:
                        msg += f" etapas={etapas}"
                    if fases:
                        msg += f" fases={fases}"
                    log.info("FIN %s", msg)
                    registrar_proceso(cfg, destino, log, tabla.destino, "FIN", msg, pn)
                    registrar_ejecucion(destino, log, tabla.nombre, "FIN " + msg.replace(f"{tabla.nombre}: ", "", 1))
                    ej.terminar(destino, fin["estado"], fin["codigo"], fin["mensaje"], est)
                for c in (origen, destino):
                    if c is not None:
                        c.close()
    except YaEnEjecucion:
        log.error("%s ya está en ejecución", nombre)
        return 2
    except ErrorConfig as e:
        log.error("Configuración: %s", e)
        return 3
    except Exception as e:  # noqa: BLE001
        log.error("ERROR: %s: %s", type(e).__name__, enmascarar(e))
        return 1


def ejecutar_todas(cfg, dry_run=False, forzar=False, detener_en_error=True, limpiar=None, fechas=None):
    """Corre las tablas en cfg.orden. Devuelve (codigo_salida, [(tabla, codigo)])."""
    log = crear_logger(cfg, "main")
    corrida = nueva_corrida()
    t0 = time.monotonic()
    ej = Ejecucion(cfg, log, corrida, "TODAS", "TODAS", None, None, dry_run, forzar)
    _registrar_global(cfg, log, f"INICIO corrida={corrida} tablas={','.join(cfg.orden)}", ej)
    resumen, tiempos = [], []
    for nombre in cfg.orden:
        t1 = time.monotonic()
        codigo = ejecutar_tabla(cfg, nombre, dry_run, forzar, corrida, desde_todas=True, id_padre=ej.id,
                                fechas=fechas)
        tiempos.append(f"{nombre}:{duracion_iso(time.monotonic() - t1)}")
        resumen.append((nombre, codigo))
        if codigo != 0 and detener_en_error:
            break
    pendientes = [n for n in cfg.orden if n not in dict(resumen)]
    for n, c in resumen:
        log.info("RESUMEN %-20s código %d", n, c)
    for n in pendientes:
        log.info("RESUMEN %-20s no ejecutada", n)
    peor = max((c for _, c in resumen), default=0)
    ok = sum(1 for _, c in resumen if c == 0)
    limpiadas = []
    if peor == 0 and not dry_run:       # solo si TODO salió bien: ante un error los datos se conservan para revisarlos
        aLimpiar = [cfg.tabla(n) for n in cfg.orden if _quiere_limpiar(cfg.tabla(n), limpiar)]
        if aLimpiar:
            peor = max(peor, limpiar_tablas(cfg, aLimpiar, log, corrida))
            limpiadas = [t.nombre for t in aLimpiar]
    _registrar_global(cfg, log, f"FIN corrida={corrida} estado={'OK' if peor == 0 else 'ERROR'} codigo={peor} "
                                f"tablas={ok}/{len(cfg.orden)} duracion={duracion_iso(time.monotonic() - t0)} "
                                f"detalle={';'.join(tiempos)}" + (f" limpiadas={','.join(limpiadas)}" if limpiadas else ""),
                     ej, "OK" if peor == 0 else "ERROR", peor)
    return peor, resumen
