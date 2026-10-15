"""Métricas exactas de ejecución en VIGIA.uPROCESOS (una fila por proceso masivo y por tabla).

La fila se inserta al comenzar (ESTADO='EN CURSO') y se completa al terminar. Si la tabla no existe o
falla, solo se avisa en el log: nunca interrumpe la carga. Los tiempos usan reloj monotónico (ms exactos).
"""
import socket
import time
from datetime import datetime

from . import dialecto
from .logger import PROCESO
from .snapshot import enmascarar

# etapa del snapshot -> columna de uPROCESOS
ETAPAS = {
    "contar origen": "MS_CONTAR_ORIGEN",
    "ejecutar SELECT de origen": "MS_EJECUTAR_ORIGEN",
    "leer estructura destino": "MS_ESTRUCTURA_DESTINO",
    "conteo destino": "MS_CONTEO_DESTINO",
    "cargar staging": "MS_CARGAR_STAGING",
    "validar staging": "MS_VALIDAR_STAGING",
    "transaccion destino": "MS_TRANSACCION_DESTINO",
}


def _ahora():
    return datetime.now().isoformat(sep="T", timespec="milliseconds")


class Ejecucion:
    def __init__(self, cfg, log, corrida, nombre, tipo, tabla_destino=None, id_padre=None,
                 dry_run=False, forzar=False):
        self.cfg, self.log, self.corrida = cfg, log, corrida
        self.proceso, self.tipo, self.tabla_destino = f"{PROCESO}.{nombre}", tipo, tabla_destino
        self.id_padre, self.dry_run, self.forzar = id_padre, dry_run, forzar
        self.id = None
        self.t0 = time.monotonic()

    def iniciar(self, destino):
        """Inserta la fila 'EN CURSO'. Devuelve el ID (o None si no se pudo registrar)."""
        self.t0 = time.monotonic()
        sql_ver = " OUTPUT INSERTED.ID" if dialecto.es_sqlserver(self.cfg) else ""
        sql = (f"INSERT INTO VIGIA.uPROCESOS (CORRIDA, PROCESO, TIPO, ID_PADRE, TABLA_DESTINO, ESTADO, INICIO, "
               f"DRY_RUN, FORZAR, USUARIO, EQUIPO){sql_ver} "
               f"VALUES (?, ?, ?, ?, ?, 'EN CURSO', ?, ?, ?, {dialecto.sql_usuario(self.cfg)}, ?)")
        try:
            cur = destino.cursor()
            cur.execute(sql, (self.corrida, self.proceso[:100], self.tipo, self.id_padre, self.tabla_destino,
                              _ahora(), int(self.dry_run), int(self.forzar), socket.gethostname()[:128]))
            self.id = int(cur.fetchone()[0]) if sql_ver else cur.lastrowid
            destino.commit()
        except Exception as e:  # noqa: BLE001
            self._fallo(destino, "inicio", e)
        return self.id

    def terminar(self, destino, estado, codigo, mensaje=None, est=None):
        """Completa la fila: fin, duración exacta, tiempos por etapa y conteos."""
        if self.id is None:
            return
        est = est or {}
        etapa = est.get("e")
        sets = {"ESTADO": estado, "CODIGO_SALIDA": codigo, "FIN": _ahora(),
                "DURACION_MS": int((time.monotonic() - self.t0) * 1000),
                "ETAPA_FINAL": None if etapa in (None, "inicio") else etapa,
                "FILAS_ORIGEN": est.get("origen"), "FILAS_CARGADAS": est.get("cargadas"),
                "FILAS_DESTINO_ANTES": est.get("destino_antes"),
                "MENSAJE": mensaje or None}
        for nombre, ms in (est.get("ms") or {}).items():
            if nombre in ETAPAS:
                sets[ETAPAS[nombre]] = ms
        cols = list(sets)
        try:
            cur = destino.cursor()
            cur.execute(f"UPDATE VIGIA.uPROCESOS SET {', '.join(c + ' = ?' for c in cols)} WHERE ID = ?",
                        [sets[c] for c in cols] + [self.id])
            destino.commit()
        except Exception as e:  # noqa: BLE001
            self._fallo(destino, "fin", e)

    def _fallo(self, destino, cuando, e):
        try:
            destino.rollback()
        except Exception:  # noqa: BLE001
            pass
        self.log.warning("No se pudo registrar en uPROCESOS (%s): %s: %s", cuando, type(e).__name__,
                         enmascarar(e, 300))
