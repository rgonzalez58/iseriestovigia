"""Panel de seguimiento: lectura de datos y servidor HTTP (solo SQLite)."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from etl import panel, runner


def test_leer_datos_devuelve_procesos_y_errores(cfg):
    runner.ejecutar_todas(cfg)
    d = panel.leer_datos(cfg, 50)
    assert d["tablas"] == cfg.orden
    assert any(p["nombre"] == "ETL_VIGIA_PY.TODAS" for p in d["procesos"])
    assert any(e["estado"] == "FIN" for e in d["errores"])


def test_servidor_sirve_html_y_json(cfg, monkeypatch):
    runner.ejecutar_todas(cfg)
    cache = panel._Cache(cfg, 50)
    llamadas = []
    original = panel.leer_datos
    monkeypatch.setattr(panel, "leer_datos", lambda *a, **k: (llamadas.append(1), original(*a, **k))[1])
    cache.obtener()
    cache.obtener()
    assert len(llamadas) == 1                       # la caché evita consultas repetidas
    assert "procesos" in json.loads(cache.obtener())
    assert b"<title>Panel ETL VIGIA</title>" in panel.HTML.read_bytes()


def test_panel_incluye_ejecuciones_y_tolera_tabla_ausente(cfg):
    import sqlite3
    runner.ejecutar_todas(cfg)
    d = panel.leer_datos(cfg, 50)
    assert d["ejecuciones"] and {"id", "ms", "ms_staging", "f_cargadas"} <= set(d["ejecuciones"][0])
    con = sqlite3.connect(cfg.dev_dir / "destino.db")
    con.execute("DROP TABLE uPROCESOS")
    con.commit()
    con.close()
    assert panel.leer_datos(cfg, 50)["ejecuciones"] == []
