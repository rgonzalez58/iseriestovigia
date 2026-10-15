"""Log a archivo y registro en VIGIA.ERRORESPROCESO (supuesto: columnas ERROR y DATOSERROR)."""
import logging
import re
from datetime import datetime

from . import dialecto


def crear_logger(cfg, nombre):
    carpeta = cfg.logs
    carpeta.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger(f"etl.{nombre}.{carpeta}")
    if not log.handlers:
        log.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        for h in (logging.FileHandler(carpeta / "etl_vigia.log", encoding="utf-8", delay=True), logging.StreamHandler()):
            h.setFormatter(fmt)
            log.addHandler(h)
    return log


PROCESO = "ETL_VIGIA_PY"


def registrar_proceso(cfg, destino, log, tarea, estado, mensaje, proceso=None):
    """Registra en VIGIA.ERRORESPROCESO: PROCESO (= NOMBRE de VIGIA.PROCESOS, p. ej. ETL_VIGIA_PY.TERCEROS), TAREA (tabla/paso en ejecución),
    ERROR (estado), DATOSERROR (mensaje), FECADI (fecha) y USUADI (usuario de la conexión SQL Server).
    Nunca interrumpe la carga: si la tabla de log falla, solo se avisa en el archivo."""
    try:
        cur = destino.cursor()
        texto = mensaje or ""
        # DATOSERROR es varchar(500): un mensaje largo se reparte en varios registros (máx. 20).
        # Cada trozo repite el prefijo "corrida=..." para poder filtrar por corrida.
        m = re.match(r"corrida=\S+ ", texto)
        pre = m.group(0) if m else ""
        resto, ancho = texto[len(pre):], 500 - len(pre)
        partes = [pre + resto[i:i + ancho] for i in range(0, len(resto), ancho)][:20] or [pre]
        for parte in partes:
            cur.execute(f"INSERT INTO VIGIA.ERRORESPROCESO (PROCESO, TAREA, ERROR, DATOSERROR, FECADI, USUADI) "
                        f"VALUES (?, ?, ?, ?, {dialecto.sql_ahora(cfg)}, {dialecto.sql_usuario(cfg)})",
                        ((proceso or PROCESO)[:200], str(tarea)[:200], str(estado)[:500], parte))
        destino.commit()
    except Exception as e:  # noqa: BLE001
        try:
            destino.rollback()
        except Exception:  # noqa: BLE001
            pass
        from .snapshot import enmascarar
        log.warning("No se pudo registrar en ERRORESPROCESO: %s: %s", type(e).__name__, enmascarar(e, 400))


def registrar_ejecucion(destino, log, nombre, resultado):
    """Registra un evento de ejecución en VIGIA.PROCESOS (NOMBRE, FECHA ISO 8601 como texto, RESULTADO).
    Nunca interrumpe la carga."""
    try:
        cur = destino.cursor()
        cur.execute("INSERT INTO VIGIA.PROCESOS (NOMBRE, FECHA, RESULTADO) VALUES (?, ?, ?)",
                    (f"{PROCESO}.{nombre}"[:100], datetime.now().isoformat(timespec="seconds"), resultado))
        destino.commit()
    except Exception as e:  # noqa: BLE001
        try:
            destino.rollback()
        except Exception:  # noqa: BLE001
            pass
        from .snapshot import enmascarar
        log.warning("No se pudo registrar en PROCESOS: %s: %s", type(e).__name__, enmascarar(e, 400))
