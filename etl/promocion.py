"""Promoción de las tablas TMP_* a las definitivas: grupo de programas que reemplaza a los procedimientos PR_CARGA*.

TRANSACCIONES reemplaza a VIGIA.PR_CARGATRANSACCIONES, que recorría un cursor fila por fila llamando a funciones
escalares VIGIA.VAL*. Aquí el trabajo es por conjuntos (todo en el servidor, sin pasar datos por Python):

  1. Pendientes: filas distintas de TMP_TRANSACCIONES que aún no están en TRANSACCIONES (clave de 7 columnas).
  2. Validación: cada función VAL* se evalúa UNA vez por combinación distinta de argumentos (no por fila); las
     filas inválidas acumulan los motivos con los mismos textos del procedimiento.
  3. Inserción por lotes (una transacción por lote). Si un lote falla por datos (clave duplicada, truncamiento...)
     se parte por mitades hasta aislar la fila culpable, que se rechaza como lo hacía el TRY/CATCH del cursor.
  4. Alertas por listas restrictivas (solo filas insertadas), igual que el procedimiento.
  5. Rechazos a VIGIA.ERRORESPROCESO (PROCESO 'Carga Datos Base', TAREA 'Carga Transacciones') y borrado de TMP
     de lo ya promovido.

El procedimiento SQL original no se modifica. Códigos de salida como el resto del ETL: 0 OK, 1 error, 2 ya en
ejecución, 3 abortado por configuración (el destino no se modificó).
"""
import sqlite3
import time
from datetime import datetime

from . import db, dialecto
from .config import ErrorConfig
from .lock import Lock, YaEnEjecucion
from .logger import PROCESO, crear_logger, registrar_ejecucion, registrar_proceso
from .metricas import Ejecucion
from .snapshot import Aborto, _cerrar, _etapa, enmascarar

PROCESO_ERRORES = "Carga Datos Base"        # PROCESO/TAREA con que el procedimiento original registraba los rechazos
TAREA_ERRORES = "Carga Transacciones"

CLAVE = ["TERCERO_TIPIDE_CODIGO", "TERCERO_NUMIDE", "PRODUCTO_CODIGO", "PRODUCTO_NUMERO", "TIPTRAN_CODIGO", "FECHA",
         "CONSECUTIVO"]
# Columnas que lee el cursor (DISTINCT) y, sin TELEFONOREMITE, las que inserta el procedimiento original.
# CODIGO_MEDIO_OPERACION y CODIGO_PARTIDA_ARAN de TMP nunca se migraron; TELEFONOREMITE se lee pero no se inserta.
SELECCION = CLAVE + [
    "SISGEO_CODIGO", "SUCURSAL_CODIGO", "VALOR", "VALORMONEDAEXTRANJERA", "TIPMON_CODIGO", "INTERMEDIARIO_CODIGO",
    "TIPIDE_REMITENTE", "NUMIDE_REMITENTE", "NOMBREREMITENTE", "APELLIDOREMITENTE", "CODIGOPAISREMITE", "CIUDADREMITE",
    "DIRECCIONREMITE", "TELEFONOREMITE", "OBSERVACIONES", "REFERENCIA1", "REFERENCIA2", "MEDIO_CODIGO",
    "FORMAPAGO_CODIGO", "ENTIDADOPERADORA", "CODIGO_DISPOSITIVO", "DIRECCIONTRANSACCION", "CIUDADCLIENTE",
    "DIRECCIONCLIENTE", "TELEFONOCLIENTE", "ENTIDADOPERADORA2", "PAISENTIDADOPERADORA2", "CIUDADENTIDADOPERADORA2",
    "ESTADOOPERACION", "LATITUDE", "LONGITUDE", "LINK"]
INSERTAR = [c for c in SELECCION if c != "TELEFONOREMITE"]

# (función VIGIA.VAL*, columnas argumento, columna que debe ser no nula para validar o None, texto del motivo)
# Textos y orden idénticos a PR_CARGATRANSACCIONES (varios sin espacio final a propósito).
REGLAS = [
    ("VALTERCERO", ["TERCERO_TIPIDE_CODIGO", "TERCERO_NUMIDE"], None, " Tercero Invalido"),
    ("VALPRODUCTOSTERCERO", ["TERCERO_TIPIDE_CODIGO", "TERCERO_NUMIDE", "PRODUCTO_CODIGO", "PRODUCTO_NUMERO"], None,
     " Producto del tercero Invalido:"),
    ("VALTIPTRAN", ["TIPTRAN_CODIGO"], None, " Tipo Transaccion Invalida "),
    ("VALESTADOOPERACION", ["ESTADOOPERACION"], "ESTADOOPERACION", " Estado Operacion Invalido"),
    ("VALSISGEO", ["SISGEO_CODIGO"], None, " SisGeo Invalido "),
    ("VALSUCURSAL", ["SUCURSAL_CODIGO"], None, " Sucursal Invalida "),
    ("VALTIPMON", ["TIPMON_CODIGO"], "TIPMON_CODIGO", " Tipo de Moneda Invalido "),
    ("VALINTERMEDIARIO", ["INTERMEDIARIO_CODIGO"], "INTERMEDIARIO_CODIGO", " Intermediario Invalido "),
    ("VALMEDIOSOPERACION", ["MEDIO_CODIGO"], "MEDIO_CODIGO", " Medio Operacion Invalido "),
    ("VALFORMASPAGO", ["FORMAPAGO_CODIGO"], "FORMAPAGO_CODIGO", " Forma Pago Invalido "),
    ("VALDISPOSITIVO", ["CODIGO_DISPOSITIVO"], "CODIGO_DISPOSITIVO", " Dispositivo Invalido "),
]


# ---------------------------------------------------------------- SQL por dialecto
def _sqlserver(cfg):
    return dialecto.es_sqlserver(cfg)


def _temp(cfg, nombre):
    return f"#PROMO_{nombre}" if _sqlserver(cfg) else f"temp.PROMO_{nombre}"


def _cat(cfg, *partes):
    return (" + " if _sqlserver(cfg) else " || ").join(partes)


def _collate(cfg, expr):
    """Igual que el procedimiento: los textos de la clave se comparan con SQL_Latin1_General_CP1_CI_AS."""
    return f"{expr} COLLATE " + ("SQL_Latin1_General_CP1_CI_AS" if _sqlserver(cfg) else "NOCASE")


def _fn(cfg, nombre):
    return f"VIGIA.{nombre}" if _sqlserver(cfg) else nombre


def _igualdad_clave(cfg, a, c):
    textos = {"TERCERO_NUMIDE", "PRODUCTO_NUMERO"}
    return " AND ".join(f"{_collate(cfg, f'{a}.{k}')} = {_collate(cfg, f'{c}.{k}')}" if k in textos
                        else f"{a}.{k} = {c}.{k}" for k in CLAVE)


def sql_pendientes(cfg, promo):
    estado = ("CASE WHEN LTRIM(RTRIM(A.ESTADOOPERACION)) = '' THEN NULL ELSE A.ESTADOOPERACION END AS ESTADOOPERACION")
    cols = ", ".join(estado if c == "ESTADOOPERACION" else f"A.{c}" for c in SELECCION)
    interno = (f"SELECT DISTINCT {cols}, CAST('' AS VARCHAR(4000)) AS MOTIVO FROM {promo.origen} A "
               f"WHERE NOT EXISTS (SELECT 1 FROM {promo.destino} C WHERE {_igualdad_clave(cfg, 'A', 'C')})")
    pend = _temp(cfg, "PEND")
    if _sqlserver(cfg):
        return (f"SELECT IDENTITY(BIGINT, 1, 1) AS N, x.* INTO {pend} FROM ({interno}) x ORDER BY x.FECHA DESC")
    return f"CREATE TEMP TABLE {pend.split('.')[-1]} AS SELECT ROW_NUMBER() OVER (ORDER BY x.FECHA DESC) AS N, x.* FROM ({interno}) x"


def sql_indice_n(cfg):
    pend = _temp(cfg, "PEND")
    if _sqlserver(cfg):
        return f"CREATE CLUSTERED INDEX IX_PROMO_N ON {pend} (N)"
    return "CREATE INDEX temp.IX_PROMO_N ON PROMO_PEND (N)"


def sql_valores_distintos(cfg, i, args, opcional):
    v, pend = _temp(cfg, f"V{i}"), _temp(cfg, "PEND")
    donde = f" WHERE {opcional} IS NOT NULL" if opcional else ""
    interno = f"SELECT DISTINCT {', '.join(args)} FROM {pend}{donde}"
    if _sqlserver(cfg):
        return f"SELECT d.*, CAST(NULL AS INT) AS OK INTO {v} FROM ({interno}) d"
    return f"CREATE TEMP TABLE {v.split('.')[-1]} AS SELECT d.*, NULL AS OK FROM ({interno}) d"


def sql_evaluar(cfg, i, funcion, args):
    return f"UPDATE {_temp(cfg, f'V{i}')} SET OK = {_fn(cfg, funcion)}({', '.join(args)})"


def sql_marcar(cfg, i, args, opcional, prefijo):
    """Agrega el motivo a las filas cuyos argumentos resultaron inválidos (OK = 0, como `IF @vlref = 0`)."""
    v, pend = _temp(cfg, f"V{i}"), _temp(cfg, "PEND")
    valores = [f"'{prefijo}'"] + [x for k, a in enumerate(args) for x in ((f"P.{a}",) if k == 0 else ("'-'", f"P.{a}"))]
    motivo = f"MOTIVO = {_cat(cfg, 'P.MOTIVO', *valores)}"
    existe = (f"EXISTS (SELECT 1 FROM {v} V WHERE {' AND '.join(f'V.{a} = P.{a}' for a in args)} AND V.OK = 0)")
    if _sqlserver(cfg):
        return f"UPDATE P SET {motivo} FROM {pend} P WHERE {existe}"
    return f"UPDATE {pend} AS P SET {motivo} WHERE {existe}"


def sql_insertar_lote(cfg, promo):
    usuario = "USER" if _sqlserver(cfg) else "'dev'"
    cols = ", ".join(INSERTAR)
    return (f"INSERT INTO {promo.destino} ({cols}, FECADI, USUADI) SELECT {cols}, {dialecto.sql_ahora(cfg)}, {usuario} "
            f"FROM {_temp(cfg, 'PEND')} WHERE N >= ? AND N < ? AND MOTIVO = ''")


def sql_rechazos(cfg):
    izq = (lambda e: f"LEFT({e}, 500)") if _sqlserver(cfg) else (lambda e: f"SUBSTR({e}, 1, 500)")
    fecha = "CONVERT(VARCHAR, FECHA, 113)" if _sqlserver(cfg) else "FECHA"
    consec = "CONVERT(VARCHAR, CONSECUTIVO)" if _sqlserver(cfg) else "CAST(CONSECUTIVO AS TEXT)"
    datos = _cat(cfg, "TERCERO_TIPIDE_CODIGO", "' '", "TERCERO_NUMIDE", "' '", "PRODUCTO_CODIGO", "' '",
                 "PRODUCTO_NUMERO", "' '", "TIPTRAN_CODIGO", "' '", fecha, "' '", consec, "' '", "SISGEO_CODIGO",
                 "' '", "SUCURSAL_CODIGO")
    return (f"INSERT INTO VIGIA.ERRORESPROCESO (PROCESO, TAREA, ERROR, DATOSERROR, FECADI, USUADI) "
            f"SELECT '{PROCESO_ERRORES}', '{TAREA_ERRORES}', {izq('MOTIVO')}, {izq(datos)}, "
            f"{dialecto.sql_ahora(cfg)}, {dialecto.sql_usuario(cfg)} FROM {_temp(cfg, 'PEND')} WHERE MOTIVO <> ''")


def sql_borrar_promovidas(cfg, promo, tope=100000):
    donde = f"EXISTS (SELECT 1 FROM {promo.destino} C WHERE {_igualdad_clave(cfg, 'E', 'C')})"
    if _sqlserver(cfg):
        return f"DELETE TOP ({tope}) E FROM {promo.origen} E WHERE {donde}"
    return f"DELETE FROM {promo.origen} AS E WHERE {donde}"


def sql_candidatos_alerta(cfg):
    top, lim = ("TOP 1 ", "") if _sqlserver(cfg) else ("", " LIMIT 1")
    numide = "P.TERCERO_NUMIDE" + (" COLLATE DATABASE_DEFAULT" if _sqlserver(cfg) else "")
    return ("SELECT N, TERCERO_TIPIDE_CODIGO, TERCERO_NUMIDE, VALOR, TIPOLISTA FROM ("
            "SELECT P.N, P.TERCERO_TIPIDE_CODIGO, P.TERCERO_NUMIDE, P.VALOR, "
            f"(SELECT {top}A.TIPOLISTA FROM VIGIA.VSDNLIST A, VIGIA.TIPOSLISTA B WHERE A.NUMIDE = {numide} "
            f"AND A.CODIGOTIPOLISTA = B.CODIGO AND A.ESTADO = 'ACTIVO' AND B.PROCESA_CARGATRNSACC = 'S'{lim}) AS TIPOLISTA "
            f"FROM {_temp(cfg, 'PEND')} P WHERE P.MOTIVO = '') q WHERE TIPOLISTA IS NOT NULL ORDER BY N")


SQL_RESPONSABLE = ("SELECT c.EMAIL, b.RESPONSABLE, c.NOMBRE FROM VIGIA.TERCEROS a, VIGIA.SUCURSALES b, "
                   "VIGIA.FUNCIONARIOS c WHERE a.TIPIDE_CODIGO = ? AND a.NUMIDE = ? AND a.SUCURSAL_CODIGO = b.CODIGO "
                   "AND b.RESPONSABLE = c.CODIGO")


def sql_alerta(cfg):
    nxt = ("VIGIA.FN_NXTVAL(?, ?, ?)" if _sqlserver(cfg) else
           "(SELECT COALESCE(MAX(CONSECUTIVO), 0) + 1 FROM VIGIA.RESULTADOALERTASPROGRAMADAS "
           "WHERE TERCERO_TIPIDE_CODIGO = ? AND TERCERO_NUMIDE = ? AND FECHA = ?)")
    usuario = "USER" if _sqlserver(cfg) else "'dev'"
    return ("INSERT INTO VIGIA.RESULTADOALERTASPROGRAMADAS (ALERTAPROGRAMADA_CODIGO, TERCERO_TIPIDE_CODIGO, "
            "TERCERO_NUMIDE, FECHA, CONSECUTIVO, VALOR, REPORTADA, CASO_NUMERO, OBSERVACIONES, FECADI, USUADI) "
            f"VALUES (0, ?, ?, ?, {nxt}, ?, 'N', NULL, ?, {dialecto.sql_ahora(cfg)}, {usuario})")


def sql_correo(cfg):
    if _sqlserver(cfg):
        return "EXEC VIGIA.PR_GENMAIL ?, ?, ?, ?"
    return "INSERT INTO VIGIA.DEV_CORREOS (ORIGEN, DESTINO, ASUNTO, MENSAJE) VALUES (?, ?, ?, ?)"


# ---------------------------------------------------------------- ejecución
def _es_error_de_datos(e):
    """Error atribuible a la fila (clave duplicada, truncamiento, conversión...), no al servidor o a los permisos."""
    if isinstance(e, (sqlite3.IntegrityError, sqlite3.DataError)):
        return True
    estado = str(e.args[0]) if getattr(e, "args", None) else ""
    return estado[:2] in ("23", "22")


def _uno(cur, sql, params=()):
    cur.execute(sql, params)
    r = cur.fetchone()
    return None if r is None else r[0]


def _drop_temporales(cfg, destino):
    cur = destino.cursor()
    for nombre in ["PEND"] + [f"V{i}" for i in range(len(REGLAS))]:
        t = _temp(cfg, nombre)
        try:
            cur.execute(f"DROP TABLE {t}" if _sqlserver(cfg) else f"DROP TABLE IF EXISTS {t}")
        except Exception:  # noqa: BLE001
            try:
                destino.rollback()
            except Exception:  # noqa: BLE001
                pass


def _insertar_lote(cfg, promo, destino, log, desde, hasta, res):
    """Inserta N en [desde, hasta). Ante un error de datos parte el rango en mitades hasta aislar la fila."""
    cur = destino.cursor()
    try:
        cur.execute(sql_insertar_lote(cfg, promo), (desde, hasta))
        n = max(cur.rowcount, 0)
        destino.commit()
        res["insertadas"] += n
        return
    except Exception as e:  # noqa: BLE001
        try:
            destino.rollback()
        except Exception:  # noqa: BLE001
            pass
        if not _es_error_de_datos(e):
            raise
        if hasta - desde > 1:
            medio = (desde + hasta) // 2
            _insertar_lote(cfg, promo, destino, log, desde, medio, res)
            _insertar_lote(cfg, promo, destino, log, medio, hasta, res)
            return
        # fila aislada: se rechaza (no se registran valores; el detalle va enmascarado)
        cur.execute(f"UPDATE {_temp(cfg, 'PEND')} SET MOTIVO = ? WHERE N = ? AND MOTIVO = ''",
                    (enmascarar(e, 480), desde))
        destino.commit()
        res["fallos_insert"] += 1


def _validar(cfg, destino, log, res):
    cur = destino.cursor()
    for i, (funcion, args, opcional, prefijo) in enumerate(REGLAS):
        cur.execute(sql_valores_distintos(cfg, i, args, opcional))
        cur.execute(sql_evaluar(cfg, i, funcion, args))
        cur.execute(sql_marcar(cfg, i, args, opcional, prefijo))
        res["por_regla"][funcion] = max(cur.rowcount, 0)
        destino.commit()
    res["rechazadas_validacion"] = _uno(cur, f"SELECT COUNT(*) FROM {_temp(cfg, 'PEND')} WHERE MOTIVO <> ''")


def _alertas(cfg, promo, destino, log, res):
    """Alerta inicial + correo al responsable para cada transacción insertada cuyo tercero esté en una lista activa."""
    cur = destino.cursor()
    if _uno(cur, "SELECT VALOR FROM VIGIA.DEFINICIONALERTAS WHERE CODIGO = '42'") != "S":
        return
    buzon = _uno(cur, "SELECT VALOR FROM VIGIA.CONTROL WHERE CODIGO = '2'")
    ruta = _uno(cur, "SELECT VALOR FROM VIGIA.CONTROL WHERE CODIGO = '6'")
    cur.execute(sql_candidatos_alerta(cfg))
    candidatos = cur.fetchall()
    if not candidatos:
        return
    prod = _sqlserver(cfg)
    if prod:     # el procedimiento formatea la fecha como MM-dd-yyyy y la deja convertir: se fija mdy mientras dura
        fecha = _uno(cur, "SELECT FORMAT(GETDATE(), 'MM-dd-yyyy')")
        cur.execute("SET DATEFORMAT mdy")
    else:
        fecha = datetime.now().strftime("%m-%d-%Y")
    cache = {}
    try:
        for _n, tipide, numide, valor, tipolista in candidatos:
            try:
                resp = cache.get((tipide, numide))
                if resp is None:
                    cur.execute(SQL_RESPONSABLE, (tipide, numide))
                    resp = cache[(tipide, numide)] = cur.fetchone() or (None, None, None)
                email, _responsable, nombre = resp
                cur.execute(sql_alerta(cfg), (tipide, numide, fecha, tipide, numide, fecha, valor,
                                              "ALERTA INICIAL GENERADA AUTOMATICAMENTE POR EL SISTEMA EN EL CARGUE "
                                              "DE TRANSACCIONES, COINCIDENCIA EN LISTA: " + str(tipolista)))
                res["alertas"] += 1
                if email and buzon:
                    mensaje = ("\r\nApreciado usuario %s:\r\n\r\nSe ha generado en el sistema una alerta los cuales "
                               "debe verificar.\r\nIngrese al sistema %s para procesar los casos.\r\n\r\n"
                               "Cordialmente,\r\n\r\nAdministrador VIGIA." % (nombre, ruta))
                    cur.execute(sql_correo(cfg), (buzon, email, "Correo Automatico VIGIA", mensaje))
                destino.commit()
            except Exception as e:  # noqa: BLE001
                try:
                    destino.rollback()
                except Exception:  # noqa: BLE001
                    pass
                res["alertas_error"] += 1
                log.warning("alerta no generada: %s: %s", type(e).__name__, enmascarar(e, 200))
    finally:
        if prod:
            cur.execute("SET DATEFORMAT ymd")


def _borrar_promovidas(cfg, promo, destino):
    cur = destino.cursor()
    total = 0
    while True:
        cur.execute(sql_borrar_promovidas(cfg, promo))
        n = max(cur.rowcount, 0)
        destino.commit()
        total += n
        if not _sqlserver(cfg) or n == 0:        # SQLite borra todo en una sentencia; SQL Server por tandas
            return total


def promover_transacciones(cfg, promo, destino, log, dry_run, est):
    res = {"pendientes": 0, "rechazadas_validacion": 0, "insertadas": 0, "fallos_insert": 0, "alertas": 0,
           "alertas_error": 0, "borradas": 0, "por_regla": {}}
    cur = destino.cursor()
    _drop_temporales(cfg, destino)
    try:
        _etapa(est, "calcular pendientes")
        cur.execute(sql_pendientes(cfg, promo))
        cur.execute(sql_indice_n(cfg))
        destino.commit()
        res["pendientes"] = est["origen"] = _uno(cur, f"SELECT COUNT(*) FROM {_temp(cfg, 'PEND')}")
        log.info("%s: pendientes por promover=%d", promo.nombre, res["pendientes"])
        _etapa(est, "validar")
        _validar(cfg, destino, log, res)
        for funcion, n in res["por_regla"].items():
            if n:
                log.info("%s: %s rechaza %d fila(s)", promo.nombre, funcion, n)
        if dry_run:
            return res
        _etapa(est, "insertar")
        maximo = _uno(cur, f"SELECT COALESCE(MAX(N), 0) FROM {_temp(cfg, 'PEND')}")
        for desde in range(1, maximo + 1, promo.lote):
            _insertar_lote(cfg, promo, destino, log, desde, min(desde + promo.lote, maximo + 1), res)
            log.info("%s: insertadas=%d de %d", promo.nombre, res["insertadas"],
                     res["pendientes"] - res["rechazadas_validacion"])
        est["cargadas"] = res["insertadas"]
        if promo.alertas:
            _etapa(est, "alertas")
            _alertas(cfg, promo, destino, log, res)
        _etapa(est, "registrar rechazos")
        cur.execute(sql_rechazos(cfg))
        destino.commit()
        if promo.borrar_promovidas:
            _etapa(est, "borrar promovidas de TMP")
            res["borradas"] = _borrar_promovidas(cfg, promo, destino)
        return res
    finally:
        _cerrar(est)
        _drop_temporales(cfg, destino)


PROMOCIONES = {"TRANSACCIONES": promover_transacciones}


def ejecutar_promocion(cfg, nombre, dry_run=False, corrida=None):
    from .runner import duracion_iso, nueva_corrida
    log = crear_logger(cfg, "main")
    corrida = corrida or nueva_corrida()
    try:
        promo = cfg.promocion(nombre)
        if promo.nombre not in PROMOCIONES:
            raise ErrorConfig(f"No hay programa de promoción para {promo.nombre}. Disponibles: {', '.join(PROMOCIONES)}")
        paso = f"PROMO_{promo.nombre}"
        pn = f"{PROCESO}.{paso}"
        # Dos locks: este proceso y la carga de la tabla TMP (que la vacía y la llena).
        with Lock(cfg.logs / f"{paso}.lock"), Lock(cfg.logs / f"{promo.nombre}.lock"):
            destino = None
            t0 = time.monotonic()
            fin = {"estado": "ERROR", "codigo": 1, "mensaje": None}
            est = {"e": "inicio", "_t": time.monotonic(), "ms": {}}
            ej = Ejecucion(cfg, log, corrida, paso, "TABLA", promo.destino, None, dry_run, False)
            try:
                destino = db.conectar_destino(cfg)
                ej.iniciar(destino)
                registrar_proceso(cfg, destino, log, promo.destino, "INICIO", f"corrida={corrida} {paso}: inicio", pn)
                registrar_ejecucion(destino, log, paso, f"INICIO corrida={corrida}")
                try:
                    r = PROMOCIONES[promo.nombre](cfg, promo, destino, log, dry_run, est)
                except Aborto as e:
                    log.error("ABORTADO: %s", e)
                    registrar_proceso(cfg, destino, log, promo.destino, "ABORTADO", f"corrida={corrida} {paso}: {e}", pn)
                    fin.update(estado="ABORTADO", codigo=3, mensaje=f"{e}"[:2000])
                    return 3
                except Exception as e:  # noqa: BLE001
                    try:
                        destino.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    msg = f"{paso}: etapa '{est.get('e')}': {type(e).__name__}: {enmascarar(e)}"
                    log.error("ERROR: %s", msg)
                    registrar_proceso(cfg, destino, log, promo.destino, "ERROR", f"corrida={corrida} {msg}", pn)
                    fin.update(estado="ERROR", codigo=1, mensaje=msg[:2000])
                    return 1
                rechazadas = r["rechazadas_validacion"] + r["fallos_insert"]
                resumen = (f"pendientes={r['pendientes']} insertadas={r['insertadas']} rechazadas={rechazadas} "
                           f"(validacion={r['rechazadas_validacion']} insercion={r['fallos_insert']}) "
                           f"alertas={r['alertas']} alertas_error={r['alertas_error']} borradas_tmp={r['borradas']}")
                estado = "DRY-RUN" if dry_run else "OK"
                log.info("%s: %s", paso, resumen)
                registrar_proceso(cfg, destino, log, promo.destino, estado, f"corrida={corrida} {paso}: {resumen}", pn)
                registrar_ejecucion(destino, log, paso, f"PROCESO FINALIZADO. Ingreso {r['insertadas']} registro(s) y "
                                                         f"rechazo {rechazadas}")
                fin.update(estado=estado, codigo=0, mensaje=resumen)
                return 0
            finally:
                if destino is not None:
                    _cerrar(est)
                    etapas = ";".join(f"{k.replace(' ', '_')}:{v / 1000:.1f}s" for k, v in est.get("ms", {}).items() if k != "inicio")
                    msg = (f"corrida={corrida} {paso}: estado={fin['estado']} "
                           f"duracion={duracion_iso(time.monotonic() - t0)}" + (f" etapas={etapas}" if etapas else ""))
                    log.info("FIN %s", msg)
                    registrar_proceso(cfg, destino, log, promo.destino, "FIN", msg, pn)
                    registrar_ejecucion(destino, log, paso, "FIN " + msg.replace(f"{paso}: ", "", 1))
                    ej.terminar(destino, fin["estado"], fin["codigo"],
                                ((fin["mensaje"] or "") + (f" etapas={etapas}" if etapas else ""))[:4000] or None, est)
                    destino.close()
    except YaEnEjecucion:
        log.error("%s ya está en ejecución (o se está cargando su tabla TMP)", nombre)
        return 2
    except ErrorConfig as e:
        log.error("Configuración: %s", e)
        return 3
    except Exception as e:  # noqa: BLE001
        log.error("ERROR: %s: %s", type(e).__name__, enmascarar(e))
        return 1
