"""Carga snapshot: origen -> staging -> (una transacción) destino."""
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from . import db, dialecto, particiones


class Aborto(Exception):
    """Validación fallida: el destino NO se modificó (código de salida 3)."""


@dataclass
class Resultado:
    estado: str          # OK | DRY-RUN
    origen: int
    cargadas: int
    mensaje: str = ""


def enmascarar(msg, limite=300):
    """Quita literales entre comillas de un mensaje de error (pueden ser datos personales)."""
    if isinstance(msg, BaseException) and len(msg.args) > 1 and all(isinstance(a, str) for a in msg.args):
        msg = max(msg.args, key=len)      # pyodbc: (SQLSTATE, mensaje completo)
    partes = []
    for p in str(msg).split("; "):          # el driver repite el mismo mensaje por cada fila del lote
        if p not in partes:
            partes.append(p)
    return re.sub(r"'[^']*'", "'***'", "; ".join(partes))[:limite]


# ---------------------------------------------------------------- conversiones
ANIO_MIN = 1753      # mínimo de DATETIME en SQL Server: antes de esa fecha se carga NULL

def a_fecha_iso(v):
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d") if v.year >= ANIO_MIN else None
    s = str(int(v)) if isinstance(v, (int, float, Decimal)) else str(v).strip()
    if len(s) == 8 and s.isdigit() and s != "00000000":
        try:
            d = datetime.strptime(s, "%Y%m%d")
            return d.strftime("%Y-%m-%d") if d.year >= ANIO_MIN else None
        except ValueError:
            return None
    return None


def a_texto(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, Decimal) and v == v.to_integral_value():
        v = int(v)
    return str(v).rstrip()


# ---------------------------------------------------------------- validaciones
def _validar_columnas(tabla, cols_origen, cols_staging, cols_destino):
    """Devuelve la lista [(alias_origen, columna_destino)] a migrar o lanza Aborto."""
    origen, staging, destino = set(cols_origen), set(cols_staging), set(cols_destino)
    for etiqueta, nombres in (("ignorar", tabla.ignorar), ("mapeo", tabla.mapeo),
                              ("fechas", tabla.fechas), ("a_texto", tabla.a_texto), ("clave", tabla.clave),
                              ("texto_max", tabla.texto_max)):
        faltan = [n for n in nombres if n not in origen]
        if faltan:
            raise Aborto(f"{tabla.nombre}: '{etiqueta}' menciona columnas que no están en el SELECT: {faltan}")
    faltan = [d for d in tabla.mapeo.values() if d not in destino]
    if faltan:
        raise Aborto(f"{tabla.nombre}: 'mapeo' apunta a columnas inexistentes en {tabla.destino}: {faltan}")
    usadas = [c for c in cols_origen if c not in tabla.ignorar]
    sin_staging = [c for c in usadas if c not in staging]
    sin_destino = [c for c in usadas if tabla.mapeo.get(c, c) not in destino]
    if sin_staging:
        raise Aborto(f"{tabla.nombre}: columnas del SELECT sin columna en staging {tabla.staging}: {sin_staging}")
    if sin_destino:
        raise Aborto(f"{tabla.nombre}: columnas del SELECT sin destino en {tabla.destino} "
                     f"(agregar a 'ignorar' o 'mapeo'): {sin_destino}")
    destinos = [tabla.mapeo.get(c, c) for c in usadas]
    if len(set(destinos)) != len(destinos):
        raise Aborto(f"{tabla.nombre}: dos columnas del origen apuntan a la misma columna destino")
    return list(zip(usadas, destinos))


def _columnas(conn, tabla):
    cur = conn.cursor()
    try:
        cur.execute(dialecto.sql_columnas(tabla))
    except Exception as e:  # noqa: BLE001
        raise Aborto(f"no se pudo leer la estructura de la tabla {tabla} (¿existe y hay permiso?): "
                     f"{type(e).__name__}: {enmascarar(e, 250)}") from e
    return [d[0].upper() for d in cur.description]


def _fecha_fuera_de_rango(v):
    try:
        if isinstance(v, (datetime, date)):
            return v.year < ANIO_MIN
        s = str(v).strip()
        d = datetime.strptime(s[:8] if len(s) == 8 and s.isdigit() else s[:10],
                              "%Y%m%d" if len(s) == 8 and s.isdigit() else "%Y-%m-%d")
        return d.year < ANIO_MIN
    except ValueError:
        return True


def _diagnosticar_lote(cur, stg, columnas, filas):
    """Ante un error de carga, indica (solo nombres y conteos, nunca valores) qué columnas del lote tienen fechas fuera
    de rango, textos más largos que el destino, NULL en columnas obligatorias o números que no caben."""
    try:
        cur.execute(f"SELECT {', '.join(columnas)} FROM {stg} WHERE 1 = 0")
        meta = cur.description
        hallazgos = {"fecha fuera de rango": [], "texto más largo que el destino": [],
                     "NULL en columna obligatoria": [], "número fuera de precisión": []}
        for i, (col, d) in enumerate(zip(columnas, meta)):
            tipo, tam, prec, esc, acepta_null = d[1], d[3], d[4], d[5], d[6]
            vals = [f[i] for f in filas]
            if acepta_null is False:
                n = sum(v is None for v in vals)
                if n:
                    hallazgos["NULL en columna obligatoria"].append(f"{col} ({n} filas)")
            if tipo in (datetime, date):
                n = sum(1 for v in vals if v is not None and _fecha_fuera_de_rango(v))
                if n:
                    hallazgos["fecha fuera de rango"].append(f"{col} ({n} filas)")
            elif tipo is str and tam and tam < 1_000_000:
                largos = [len(v) for v in vals if isinstance(v, str) and len(v) > tam]
                if largos:
                    hallazgos["texto más largo que el destino"].append(
                        f"{col} ({len(largos)} filas, máx {max(largos)} > {tam})")
            elif tipo is Decimal and prec:
                enteros = prec - (esc or 0)
                malos = [v for v in vals if v is not None and
                         len(str(v).lstrip("-+").split(".")[0].lstrip("0") or "0") > enteros]
                if malos:
                    hallazgos["número fuera de precisión"].append(f"{col} ({len(malos)} filas, caben {enteros} enteros)")
        partes = [f"{k}: {', '.join(v)}" for k, v in hallazgos.items() if v]
        return ("DIAGNOSTICO (lote con error): " + "; ".join(partes)) if partes else             "DIAGNOSTICO (lote con error): ninguna causa detectada en fechas, largos, NULL ni precisión (revisar tipos)"
    except Exception as e2:  # noqa: BLE001
        return f"DIAGNOSTICO no disponible ({type(e2).__name__})"


def escribir_lote(cfg, cur, tabla, stg, cols_stg, ins, filas):
    """Escribe un lote al staging. 'parametros' (por defecto): executemany; 'valores': INSERT multi-fila sin
    parámetros, que reduce los viajes de red cuando el servidor está lejos (se elige con escritura: valores)."""
    if tabla.escritura == "valores":
        sqls = dialecto.sqls_insert_valores(cfg, stg, cols_stg, filas)
        if dialecto.es_sqlserver(cfg):
            cur.execute("; ".join(sqls))          # un solo viaje de red por lote
        else:
            for q in sqls:
                cur.execute(q)
    else:
        cur.executemany(ins, filas)


def _contar(conn, sql):
    cur = conn.cursor()
    cur.execute(sql)
    return int(cur.fetchone()[0])


def _a_fecha_sqlserver(v):
    """'AAAA-MM-DD 00:00:00' (formato canónico ODBC). Sirve en las dos rutas del driver: si convierte en el cliente
    (columna descrita como datetime, p. ej. staging ## o permanente) lo acepta; si lo envía como texto, el servidor
    lo lee con DATEFORMAT ymd (se fija en cada conexión). 'AAAAMMDD' fallaba en la primera ruta (error 22018)."""
    iso = a_fecha_iso(v)
    return iso + " 00:00:00" if iso else None


def _transformador(tabla, cols_origen, mapa, fechas_como_date=False):
    idx = [cols_origen.index(o) for o, _ in mapa]
    conv = []
    for o, _ in mapa:
        conv.append(_a_fecha_sqlserver if o in tabla.fechas and fechas_como_date
                    else a_fecha_iso if o in tabla.fechas else a_texto if o in tabla.a_texto else None)

    maximos = [tabla.texto_max.get(o) for o, _ in mapa]
    nombres = [o for o, _ in mapa]

    def f(fila):
        out = []
        for i, c, mx, nom in zip(idx, conv, maximos, nombres):
            v = fila[i]
            if c:
                v = c(v)
            elif isinstance(v, Decimal):
                # fast_executemany de pyodbc falla con "Converting decimal loses precision" si la escala
                # del Decimal difiere de la inferida; como texto el servidor convierte a la columna.
                v = format(v, "f")
            if mx and v is not None and len(str(v)) > mx:
                # Igual que la conversión a string(n) del SSIS: no se trunca en silencio (sin mostrar el valor).
                raise Aborto(f"columna {nom}: hay valores de {len(str(v))} caracteres, mayor al máximo {mx} "
                             f"(texto_max); corregir el origen o ampliar el máximo")
            out.append(v)
        return tuple(out)
    return f


# ---------------------------------------------------------------- proceso
def _etapa(est, nombre):
    """Cierra el tiempo (ms exactos) de la etapa en curso y abre la siguiente."""
    ahora = time.monotonic()
    _cerrar(est, ahora)
    est["e"], est["_t"] = nombre, ahora


def _cerrar(est, ahora=None):
    if est.get("_t") is not None:
        ms = est.setdefault("ms", {})
        ms[est["e"]] = ms.get(est["e"], 0) + int(((ahora or time.monotonic()) - est["_t"]) * 1000)
        est["_t"] = None


def ejecutar_snapshot(cfg, tabla, origen, destino, log, dry_run=False, forzar=False, est=None, fechas=None):
    """Indica en el error la etapa en la que falló (sin datos personales).
    `est` (opcional) recibe las métricas: etapa, ms por etapa y conteos de filas, incluso si falla."""
    est = {} if est is None else est
    est.update(e="inicio", _t=time.monotonic(), ms={})
    try:
        return _snapshot(cfg, tabla, origen, destino, log, dry_run, forzar, est, fechas)
    except Aborto:
        raise
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"etapa '{est['e']}': {type(e).__name__}: {enmascarar(e, 3000)}" + (f" | {est['diag']}" if est.get("diag") else "")) from e
    finally:
        _cerrar(est)
        if est.get("stg_compartido"):
            try:
                destino.rollback()
                destino.cursor().execute(f"DROP TABLE {est['stg_compartido']}")
                destino.commit()
            except Exception:  # noqa: BLE001
                pass


def _cargar_serie(cfg, tabla, cur_o, cur_d, convertir, stg, cols_stg, ins, log, est):
    """Un solo hilo: lee, convierte y escribe por lotes (ruta por defecto)."""
    enviadas = 0
    t_lect = t_conv = t_ins = 0.0          # segundos acumulados por fase (lectura del origen / conversión / escritura)
    t_carga = ultimo = time.monotonic()
    while True:
        t1 = time.monotonic()
        lote = cur_o.fetchmany(tabla.lote)
        t2 = time.monotonic()
        t_lect += t2 - t1
        if not lote:
            break
        filas = [convertir(f) for f in lote]
        t3 = time.monotonic()
        t_conv += t3 - t2
        try:
            escribir_lote(cfg, cur_d, tabla, stg, cols_stg, ins, filas)
        except Exception as e:  # noqa: BLE001
            est["diag"] = _diagnosticar_lote(cur_d, stg, cols_stg, filas)
            raise
        t_ins += time.monotonic() - t3
        enviadas += len(lote)
        est["cargadas"] = enviadas
        if t_ins and time.monotonic() - ultimo >= 30:      # progreso visible en el log aunque la carga sea larga
            ultimo = time.monotonic()
            log.info("%s: %d filas en staging | origen %.0f f/s, conversión %.0f f/s, escritura %.0f f/s",
                     tabla.nombre, enviadas, enviadas / max(t_lect, 1e-9), enviadas / max(t_conv, 1e-9),
                     enviadas / t_ins)
    est["fases_s"] = {"lectura_origen": round(t_lect, 1), "conversion": round(t_conv, 1),
                      "escritura_staging": round(t_ins, 1)}
    log.info("%s: staging cargado, %d filas en %.1f s (lectura origen %.1f s, conversión %.1f s, escritura %.1f s)",
             tabla.nombre, enviadas, time.monotonic() - t_carga, t_lect, t_conv, t_ins)
    return enviadas


def _snapshot(cfg, tabla, origen, destino, log, dry_run, forzar, est, fechas=None):
    sql = cfg.sql_origen(tabla)
    _etapa(est, "contar origen")
    # Con contar_origen: false no se ejecuta el SELECT dos veces (útil en tablas enormes); el total es lo leído.
    # Filtro por fecha: una fecha en la corrida diaria, varias en un reproceso (fecha_defecto o --fecha/--desde/--mes).
    if fechas and fechas != "todas" and not tabla.particion:
        log.warning("%s: la tabla no admite filtro de fecha; se carga completa", tabla.nombre)
    rango = particiones.resolver_rango(tabla, fechas)
    est["fechas"] = particiones.texto_rango(rango) if tabla.particion else ""
    partido = tabla.lectores > 1 or rango is not None          # lectura por particiones de fecha y/o por rango
    filtros = None
    if partido:
        if not cfg.tiene_filtro_fecha(tabla):
            raise Aborto(f"{tabla.nombre}: lectores/fechas requieren @@FILTRO_FECHA@@ en el SQL de origen")
        _etapa(est, "calcular particiones")
        filtros = particiones.filtros_para(origen, tabla, rango,
                                           mod=lambda c, r: dialecto.expr_resto(cfg, c, r))
        log.info("%s: fechas %s en %d particiones, %d conexiones de lectura", tabla.nombre, particiones.texto_rango(rango),
                 len(filtros), tabla.lectores)
        _etapa(est, "contar origen")
    elif tabla.particion:
        log.info("%s: todas las fechas", tabla.nombre)
    total_origen = (_contar(origen, f"SELECT COUNT(*) FROM ({sql}) q")
                    if tabla.contar_origen and not partido else None)
    est["origen"] = total_origen
    if total_origen is not None:
        log.info("%s: origen=%d filas", tabla.nombre, total_origen)

    _etapa(est, "ejecutar SELECT de origen")
    cur_o = origen.cursor()
    if tabla.lectores > 1:
        cur_o.execute(cfg.sql_origen(tabla, " and 1 = 0"))       # solo para conocer las columnas (sin filas)
    elif partido:
        cur_o.execute(cfg.sql_origen(tabla, filtros[0]))
    else:
        cur_o.execute(sql)
    cols_origen = [d[0].upper() for d in cur_o.description]
    _etapa(est, "leer estructura destino")
    cols_destino = _columnas(destino, tabla.destino)
    auto = tabla.staging.lower() in ("", "auto")
    # En modo auto el staging tiene los nombres de columna del destino; si no, los alias del origen.
    mapa = _validar_columnas(tabla, cols_origen, cols_origen if auto else _columnas(destino, tabla.staging),
                             cols_destino)
    stg = tabla.staging
    paralelo = tabla.hilos_escritura > 1 or tabla.lectores > 1
    if auto:
        stg = dialecto.nombre_staging(cfg, tabla.nombre, compartido=paralelo)
        cols_stg = [d for _, d in mapa]
    else:
        cols_stg = [o for o, _ in mapa]
    # En SQL Server las fechas viajan como 'AAAA-MM-DD 00:00:00' (con DATEFORMAT ymd en la sesión): sin eso depende del idioma
    # de la sesión (con dmy da "fuera de intervalo" cuando el día > 12).
    convertir = _transformador(tabla, cols_origen, mapa, dialecto.es_sqlserver(cfg))

    # Guardas (antes de tocar el destino).
    _etapa(est, "conteo destino")
    actuales = _contar(destino, f"SELECT COUNT(*) FROM {tabla.destino}")
    est["destino_antes"] = actuales

    def guardas(total):
        if forzar:
            return
        if total == 0 and not (rango and tabla.permitir_origen_vacio):
            raise Aborto(f"{tabla.nombre}: el origen devolvió 0 filas (use --forzar para vaciar el destino)")
        if total == 0:
            log.warning("%s: sin filas para %s; el destino queda vacío (permitir_origen_vacio)", tabla.nombre,
                        particiones.texto_rango(rango))
        # Con rango de fechas el volumen varía mucho (un día tras un mes de reproceso): no aplica la caída brusca.
        if rango is None and actuales > 0 and total < actuales * (1 - tabla.max_caida_pct / 100):
            raise Aborto(f"{tabla.nombre}: caída brusca (origen={total}, destino={actuales}, "
                         f"máx {tabla.max_caida_pct}%); use --forzar si es correcto")

    if total_origen is not None:
        guardas(total_origen)

    # Staging: se vacía y se carga por lotes.
    _etapa(est, "cargar staging")
    cur_d = destino.cursor()
    db.activar_fast_executemany(cfg, cur_d)
    if auto:
        cur_d.execute(dialecto.sql_crear_staging(cfg, stg, tabla.destino, cols_stg))
        if paralelo:
            est["stg_compartido"] = stg          # se borra al terminar (ejecutar_snapshot)
    else:
        cur_d.execute(dialecto.sql_vaciar(cfg, stg, tabla.vaciado))
    if paralelo:
        destino.commit()                         # las otras conexiones deben ver la tabla ya creada
    ins = dialecto.sql_insert_staging(cfg, stg, cols_stg)
    if paralelo:
        from . import carga_paralela
        enviadas, est["fases_s"] = carga_paralela.cargar(cfg, tabla, cur_o, convertir, stg, cols_stg, ins,
                                                         max(1, tabla.hilos_escritura), log, est, escribir_lote,
                                                         _diagnosticar_lote,
                                                         filtros if tabla.lectores > 1 else None, tabla.lectores)
    else:
        enviadas = _cargar_serie(cfg, tabla, cur_o, cur_d, convertir, stg, cols_stg, ins, log, est)
    destino.commit()
    est["cargadas"] = enviadas

    _etapa(est, "validar staging")
    en_staging = _contar(destino, f"SELECT COUNT(*) FROM {stg}")
    if en_staging != enviadas:
        raise Aborto(f"{tabla.nombre}: staging={en_staging} distinto de enviadas={enviadas}")
    if total_origen is None:
        total_origen = enviadas
        est["origen"] = enviadas
        log.info("%s: leídas=%d filas", tabla.nombre, enviadas)
        guardas(total_origen)
    if abs(total_origen - enviadas) > tabla.tolerancia_filas:
        raise Aborto(f"{tabla.nombre}: origen={total_origen} vs cargadas={enviadas} "
                     f"(tolerancia {tabla.tolerancia_filas})")

    if tabla.clave:
        k = ", ".join(tabla.mapeo.get(o, o) if auto else o for o in tabla.clave)
        dup = _contar(destino, f"SELECT COUNT(*) FROM (SELECT {k} FROM {stg} "
                               f"GROUP BY {k} HAVING COUNT(*) > 1) d")
        if dup:
            log.warning("%s: %d claves duplicadas en staging (%s)", tabla.nombre, dup, k)

    if dry_run:
        log.info("%s: dry-run, destino sin cambios", tabla.nombre)
        return Resultado("DRY-RUN", total_origen, enviadas)

    _etapa(est, "transaccion destino")
    # Única transacción sobre el destino. Si otra sesión mantiene bloqueada la tabla, se espera como máximo
    # espera_bloqueo_seg y se falla (el destino conserva la carga anterior) en lugar de quedar colgado.
    try:
        cur = destino.cursor()
        if dialecto.es_sqlserver(cfg):
            cur.execute(f"SET LOCK_TIMEOUT {max(1, tabla.espera_bloqueo_seg) * 1000}")
        fases = est.setdefault("fases_s", {})
        indices = []
        if tabla.reconstruir_indices and dialecto.es_sqlserver(cfg):
            # Se desactivan los índices no agrupados durante la carga y se reconstruyen al final, todo dentro de la
            # misma transacción (si algo falla, el rollback los deja como estaban).
            t1 = time.monotonic()
            cur.execute(dialecto.sql_indices_no_agrupados(cfg), (tabla.destino,))
            indices = [r[0] for r in cur.fetchall()]
            for nombre in indices:
                cur.execute(dialecto.sql_alter_indice(tabla.destino, nombre, "DISABLE"))
            fases["indices_off"] = round(time.monotonic() - t1, 1)
            log.info("%s: %d índices desactivados en %.1f s", tabla.nombre, len(indices), fases["indices_off"])
        t1 = time.monotonic()
        cur.execute(dialecto.sql_vaciar(cfg, tabla.destino, tabla.vaciado))
        fases["vaciar"] = round(time.monotonic() - t1, 1)
        log.info("%s: destino vaciado en %.1f s", tabla.nombre, fases["vaciar"])
        t1 = time.monotonic()
        cur.execute(dialecto.sql_insert_select(cfg, tabla.destino, stg,
                                               [d for _, d in mapa], cols_stg, tabla.tablock))
        insertadas = cur.rowcount
        fases["insertar"] = round(time.monotonic() - t1, 1)
        log.info("%s: insertado desde staging en %.1f s (filas insertadas según el servidor: %s)", tabla.nombre,
                 fases["insertar"], insertadas)
        if indices:
            t1 = time.monotonic()
            for nombre in indices:
                cur.execute(dialecto.sql_alter_indice(tabla.destino, nombre, "REBUILD"))
            fases["indices_rebuild"] = round(time.monotonic() - t1, 1)
            log.info("%s: %d índices reconstruidos en %.1f s", tabla.nombre, len(indices), fases["indices_rebuild"])
        t1 = time.monotonic()
        final = _contar(destino, f"SELECT COUNT(*) FROM {tabla.destino}")
        fases["contar"] = round(time.monotonic() - t1, 1)
        log.info("%s: conteo del destino en %.1f s", tabla.nombre, fases["contar"])
        if final != enviadas:
            raise Aborto(f"{tabla.nombre}: destino={final} distinto de staging={enviadas}")
        t1 = time.monotonic()
        destino.commit()
        fases["commit"] = round(time.monotonic() - t1, 1)
        log.info("%s: commit en %.1f s", tabla.nombre, fases["commit"])
    except BaseException as e:
        destino.rollback()
        if "1222" in str(e):
            raise RuntimeError(f"tiempo de espera de bloqueo agotado ({tabla.espera_bloqueo_seg} s): otra sesión "
                               f"mantiene bloqueada {tabla.destino}; el destino conserva la carga anterior. Ver "
                               f"blocking_session_id (manual, 'Carga lenta')") from e
        raise
    finally:
        if dialecto.es_sqlserver(cfg):
            try:
                destino.cursor().execute("SET LOCK_TIMEOUT -1")
            except Exception:  # noqa: BLE001
                pass
    est["cargadas"] = final
    log.info("%s: cargadas %d filas", tabla.nombre, final)
    return Resultado("OK", total_origen, final)
