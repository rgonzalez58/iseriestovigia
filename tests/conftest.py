import sqlite3
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "dev"))

import seed_dev  # noqa: E402
from etl import config, runner  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    seed_dev.crear(tmp_path / "dev_data", filas=100)
    c = config.cargar(raiz=RAIZ, dev_dir=tmp_path / "dev_data", entorno="dev")
    for t in c.tablas.values():
        t.fecha_defecto = ""          # las pruebas de fecha por defecto la fijan ellas
    return c


@pytest.fixture
def correr(cfg):
    def f(tabla, **kw):
        return runner.ejecutar_tabla(cfg, tabla, **kw)
    return f


@pytest.fixture
def consultar(cfg):
    def f(sql, params=()):
        con = sqlite3.connect(":memory:")
        con.execute("ATTACH DATABASE ? AS VIGIA", (str(cfg.dev_dir / "destino.db"),))
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()
    return f


@pytest.fixture
def origen_rw(cfg):
    """Conexión de escritura al origen simulado (solo pruebas)."""
    con = sqlite3.connect(cfg.dev_dir / "origen.db")
    yield con
    con.commit()
    con.close()
