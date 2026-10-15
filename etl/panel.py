"""Panel web liviano de seguimiento (solo lectura). Proceso aparte: no comparte nada con la carga.

Lee VIGIA.PROCESOS y VIGIA.ERRORESPROCESO (últimos N registros, sin bloquear) y los sirve en una página
HTML sin dependencias externas. Los datos se guardan en caché unos segundos para que varios
navegadores abiertos no multipliquen las consultas.
"""
import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import db, dialecto

HTML = Path(__file__).with_name("panel.html")
CACHE_SEG = 5


def _texto(v):
    return v.isoformat(timespec="seconds") if isinstance(v, datetime) else ("" if v is None else str(v))


def _filas(cfg, conn, sql_prod, sql_dev, columnas):
    cur = conn.cursor()
    cur.execute(sql_prod if dialecto.es_sqlserver(cfg) else sql_dev)
    return [dict(zip(columnas, (_texto(v) for v in f))) for f in cur.fetchall()]


COLS_U = ["id", "corrida", "proceso", "tipo", "id_padre", "estado", "etapa", "inicio", "fin", "ms", "ms_origen", "ms_ejec",
          "ms_destino_estr", "ms_conteo", "ms_staging", "ms_validar", "ms_trans", "f_origen", "f_cargadas", "f_antes",
          "usuario", "equipo", "dry"]


def _ejecuciones(cfg, conn, maximo):
    """Métricas exactas de VIGIA.uPROCESOS. Si la tabla aún no existe, el panel sigue funcionando sin ellas."""
    cols = ("ID, CORRIDA, PROCESO, TIPO, ID_PADRE, ESTADO, ETAPA_FINAL, INICIO, FIN, DURACION_MS, MS_CONTAR_ORIGEN, "
            "MS_EJECUTAR_ORIGEN, MS_ESTRUCTURA_DESTINO, MS_CONTEO_DESTINO, MS_CARGAR_STAGING, MS_VALIDAR_STAGING, "
            "MS_TRANSACCION_DESTINO, FILAS_ORIGEN, FILAS_CARGADAS, FILAS_DESTINO_ANTES, USUARIO, EQUIPO, DRY_RUN")
    try:
        return _filas(cfg, conn,
                      f"SELECT TOP {maximo} {cols} FROM VIGIA.uPROCESOS ORDER BY ID DESC",
                      f"SELECT {cols} FROM VIGIA.uPROCESOS ORDER BY ID DESC LIMIT {maximo}", COLS_U)
    except Exception:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return []


def leer_datos(cfg, maximo=300):
    """Últimos registros de ambas tablas (solo SELECT; READ UNCOMMITTED para no esperar bloqueos)."""
    maximo = max(10, min(int(maximo), 2000))
    conn = db.conectar_destino(cfg)
    try:
        if dialecto.es_sqlserver(cfg):
            conn.cursor().execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        procesos = _filas(
            cfg, conn,
            f"SELECT TOP {maximo} NOMBRE, FECHA, RESULTADO FROM VIGIA.PROCESOS ORDER BY FECHA DESC",
            f"SELECT NOMBRE, FECHA, RESULTADO FROM VIGIA.PROCESOS ORDER BY FECHA DESC LIMIT {maximo}",
            ["nombre", "fecha", "resultado"])
        errores = _filas(
            cfg, conn,
            f"SELECT TOP {maximo} PROCESO, TAREA, ERROR, DATOSERROR, FECADI, USUADI "
            f"FROM VIGIA.ERRORESPROCESO ORDER BY FECADI DESC",
            f"SELECT PROCESO, TAREA, ERROR, DATOSERROR, FECADI, USUADI FROM VIGIA.ERRORESPROCESO "
            f"ORDER BY FECADI DESC LIMIT {maximo}",
            ["proceso", "tarea", "estado", "datos", "fecha", "usuario"])
        ejecuciones = _ejecuciones(cfg, conn, maximo)
    finally:
        conn.close()
    return {"generado": datetime.now().isoformat(timespec="seconds"), "tablas": cfg.orden,
            "procesos": procesos, "errores": errores, "ejecuciones": ejecuciones}


class _Cache:
    def __init__(self, cfg, maximo):
        self.cfg, self.maximo, self.lock = cfg, maximo, threading.Lock()
        self.t = 0.0
        self.cuerpo = b"{}"

    def obtener(self):
        with self.lock:
            if time.monotonic() - self.t > CACHE_SEG:
                try:
                    self.cuerpo = json.dumps(leer_datos(self.cfg, self.maximo)).encode("utf-8")
                except Exception as e:  # noqa: BLE001
                    self.cuerpo = json.dumps({"error": f"{type(e).__name__}"}).encode("utf-8")
                self.t = time.monotonic()
            return self.cuerpo


def servir(cfg, host="127.0.0.1", puerto=8765, maximo=300):
    cache = _Cache(cfg, maximo)

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] == "/datos":
                cuerpo, tipo = cache.obtener(), "application/json; charset=utf-8"
            elif self.path.split("?")[0] in ("/", "/index.html"):
                cuerpo, tipo = HTML.read_bytes(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, puerto), H)
    print(f"Panel en http://{host}:{puerto}/  (Ctrl+C para detener)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
