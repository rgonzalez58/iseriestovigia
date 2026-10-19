from datetime import date
from decimal import Decimal

from etl.dialecto import sql_vaciar
from etl.snapshot import a_fecha_iso, a_texto, enmascarar


def test_fecha_iso():
    assert a_fecha_iso(20230115) == "2023-01-15"
    assert a_fecha_iso(Decimal("20230115")) == "2023-01-15"
    assert a_fecha_iso("17530101") == "1753-01-01"
    assert a_fecha_iso("00000000") is None
    assert a_fecha_iso(0) is None
    assert a_fecha_iso(20231345) is None
    assert a_fecha_iso(None) is None
    assert a_fecha_iso(date(2020, 2, 3)) == "2020-02-03"


def test_a_texto():
    assert a_texto(1) == "1"
    assert a_texto(3.0) == "3"
    assert a_texto(Decimal("7")) == "7"
    assert a_texto("AB  ") == "AB"
    assert a_texto(None) is None


def test_enmascarar_no_expone_valores():
    assert "Perez" not in enmascarar("Conversion failed when converting 'Perez' to int")


def test_vaciar(cfg):
    assert sql_vaciar(cfg, "T", "truncate") == "DELETE FROM T"   # dev (SQLite)


def test_cadena_origen_con_driver_sin_dsn(cfg):
    from etl.config import ErrorConfig, cadena_odbc_origen
    cfg.env.update(ORIGEN_USUARIO="u", ORIGEN_PASSWORD="p", ORIGEN_DSN="Normativo")
    assert cadena_odbc_origen(cfg) == "DSN=Normativo;UID=u;PWD=p"
    cfg.env["ORIGEN_DRIVER"] = "{iSeries Access ODBC Driver}"
    try:
        cadena_odbc_origen(cfg)
        assert False
    except ErrorConfig:
        pass
    cfg.env["ORIGEN_SISTEMA"] = "10.0.0.1"
    assert cadena_odbc_origen(cfg) == "DRIVER={iSeries Access ODBC Driver};SYSTEM=10.0.0.1;UID=u;PWD=p"


def test_enmascarar_excepcion_estilo_pyodbc():
    e = ProgrammingError = type("ProgrammingError", (Exception,), {})("42S02", "[42S02] Objeto 'X' no es valido (208)")
    t = enmascarar(e)
    assert "42S02" in t and "208" in t and "'X'" not in t


def test_decimal_se_envia_como_texto_plano():
    from etl.config import Tabla
    from etl.snapshot import _transformador
    t = type("T", (), {"fechas": [], "a_texto": [], "texto_max": {}})()
    f = _transformador(t, ["A", "B"], [("A", "A"), ("B", "B")])
    assert f((Decimal("1E+3"), Decimal("12.50"))) == ("1000", "12.50")


def test_fecha_anterior_a_1753_es_null():
    assert a_fecha_iso("00010101") is None
    assert a_fecha_iso(17000101) is None
    assert a_fecha_iso(date(1500, 1, 1)) is None
    assert a_fecha_iso("17530101") == "1753-01-01"


class _Cur:
    """Cursor falso con description al estilo pyodbc: (nombre, tipo, display, tamaño, precisión, escala, acepta_null)."""
    def __init__(self, description):
        self.description = description

    def execute(self, sql):
        return self


def test_diagnostico_de_lote_detecta_causas_sin_mostrar_valores():
    import datetime as dt
    from etl.snapshot import _diagnosticar_lote
    cur = _Cur([("F", dt.datetime, None, None, None, None, True),
                ("T", str, None, 5, None, None, True),
                ("N", str, None, 30, None, None, False),
                ("D", Decimal, None, None, 6, 2, True)])
    filas = [("20240101", "ab", "x", Decimal("12.50")),
             ("00010101", "SECRETO-LARGO", "x", Decimal("123456.50")),
             ("2024-13-45", "ab", None, None)]
    msg = _diagnosticar_lote(cur, "s", ["F", "T", "N", "D"], filas)
    assert "F (2 filas)" in msg and "T (1 filas, máx 13 > 5)" in msg
    assert "N (1 filas)" in msg and "D (1 filas, caben 4 enteros)" in msg
    assert "SECRETO" not in msg and "123456" not in msg


def test_diagnostico_de_lote_sin_causa_detectada():
    from etl.snapshot import _diagnosticar_lote
    cur = _Cur([("A", str, None, 10, None, None, True)])
    assert "ninguna causa detectada" in _diagnosticar_lote(cur, "s", ["A"], [("abc",)])


def test_fechas_como_date_en_sqlserver_y_dedup_de_mensajes():
    t = type("T", (), {"fechas": ["A"], "a_texto": [], "texto_max": {}})()
    f = __import__("etl.snapshot", fromlist=["x"])._transformador(t, ["A"], [("A", "A")], True)
    assert f((20240131,)) == ("2024-01-31 00:00:00",) and f(("00000000",)) == (None,)
    e = Exception("a", "m1; m1; m2; m1")
    assert enmascarar(e) == "m1; m2"


def test_escritura_paralela_reparte_y_escribe_todo(tmp_path):
    import sqlite3
    from etl.diagnostico import escribir_paralelo
    ruta = tmp_path / "p.db"
    c = sqlite3.connect(ruta)
    c.execute("CREATE TABLE t (a INTEGER)")
    c.commit()
    c.close()
    filas = [(i,) for i in range(1000)]
    hechas, seg = escribir_paralelo(lambda: sqlite3.connect(ruta, timeout=30), "INSERT INTO t (a) VALUES (?)",
                                    filas, 4, 100, 30)
    c = sqlite3.connect(ruta)
    assert hechas == 1000 and c.execute("SELECT COUNT(DISTINCT a) FROM t").fetchone()[0] == 1000


def test_insert_select_con_y_sin_tablock():
    from etl import dialecto
    prod = type("C", (), {"entorno": "prod"})()
    assert "WITH (TABLOCK)" in dialecto.sql_insert_select(prod, "VIGIA.T", "#S", ["A"], ["A"])
    assert "TABLOCK" not in dialecto.sql_insert_select(prod, "VIGIA.T", "#S", ["A"], ["A"], tablock=False)
    dev = type("C", (), {"entorno": "dev"})()
    assert "TABLOCK" not in dialecto.sql_insert_select(dev, "VIGIA.T", "S", ["A"], ["A"])


def test_sql_de_indices_se_escapa_y_es_de_solo_lectura():
    from etl import dialecto
    assert dialecto.sql_alter_indice("VIGIA.T", "IX_A", "DISABLE") == "ALTER INDEX [IX_A] ON VIGIA.T DISABLE"
    assert dialecto.sql_alter_indice("VIGIA.T", "IX]X", "REBUILD") == "ALTER INDEX [IX]]X] ON VIGIA.T REBUILD"
    q = dialecto.sql_indices_no_agrupados(None)
    assert q.startswith("SELECT") and "index_id > 1" in q and "is_disabled = 0" in q


def test_consola_bloqueada_no_frena_el_log(cfg, monkeypatch):
    """Una consola congelada (selección con el ratón) no debe detener a quien escribe en el log."""
    import threading, time, logging
    from etl import logger
    bloqueo = threading.Event()
    original = logging.StreamHandler.emit
    monkeypatch.setattr(logging.StreamHandler, "emit", lambda self, r: bloqueo.wait(5) if type(self) is logging.StreamHandler else original(self, r))
    log = logger.crear_logger(cfg, "consola_bloqueada")
    t0 = time.monotonic()
    for i in range(50):
        log.info("linea %d", i)
    assert time.monotonic() - t0 < 1
    assert "linea 49" in (cfg.logs / "etl_vigia.log").read_text(encoding="utf-8")   # el archivo sí quedó completo
    bloqueo.set()


def test_consola_bloqueada_no_frena_el_log(cfg, monkeypatch):
    """Una consola congelada (selección con el ratón) no debe detener a quien escribe en el log."""
    import logging
    import threading
    import time
    from etl import logger
    bloqueo = threading.Event()
    original = logging.StreamHandler.emit
    monkeypatch.setattr(logging.StreamHandler, "emit",
                        lambda self, r: bloqueo.wait(5) if type(self) is logging.StreamHandler else original(self, r))
    log = logger.crear_logger(cfg, "consola_bloqueada")
    t0 = time.monotonic()
    for i in range(50):
        log.info("linea %d", i)
    assert time.monotonic() - t0 < 1
    for h in log.handlers:
        h.flush()
    assert "linea 49" in (cfg.logs / "etl_vigia.log").read_text(encoding="utf-8")   # el archivo queda completo
    bloqueo.set()


def test_tabla_de_un_solo_mes_se_parte_por_dias_y_cubre_todo():
    import sqlite3
    from datetime import date
    from etl import particiones
    f = particiones.construir_filtros("f", "num", date(2026, 9, 1), date(2026, 9, 30), lectores=4)
    assert len(f) > 30                                           # días + anterior + posterior + nulos
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t (f)")
    muestras = [20260901, 20260915, 20260930, 20200101, 20261001, 0, None]
    c.executemany("INSERT INTO t VALUES (?)", [(m,) for m in muestras])
    vistas = [r[0] for x in f for r in c.execute("SELECT f FROM t WHERE 1 = 1" + x)]
    assert sorted(vistas, key=lambda v: (v is None, v)) == sorted(muestras, key=lambda v: (v is None, v))
    assert len(particiones.construir_filtros("f", "num", date(2026, 9, 1), date(2026, 9, 30), lectores=1)) == 4
