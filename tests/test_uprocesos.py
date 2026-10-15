"""VIGIA.uPROCESOS: métricas exactas por ejecución y enlace con ERRORESPROCESO."""
from datetime import datetime

from etl import runner

COLS = ("ID, CORRIDA, PROCESO, TIPO, ID_PADRE, TABLA_DESTINO, ESTADO, CODIGO_SALIDA, ETAPA_FINAL, INICIO, FIN, "
        "DURACION_MS, MS_CARGAR_STAGING, MS_TRANSACCION_DESTINO, FILAS_ORIGEN, FILAS_CARGADAS, FILAS_DESTINO_ANTES, "
        "USUARIO, EQUIPO")


def _filas(consultar, donde="1=1"):
    cur = consultar(f"SELECT {COLS} FROM VIGIA.uPROCESOS WHERE {donde} ORDER BY ID")
    nombres = [c.strip() for c in COLS.split(",")]
    return [dict(zip(nombres, f)) for f in cur]


def test_todas_registra_global_y_tablas_con_metricas(cfg, consultar):
    codigo, _ = runner.ejecutar_todas(cfg)
    assert codigo == 0
    filas = _filas(consultar)
    glob = [f for f in filas if f["TIPO"] == "TODAS"]
    tablas = [f for f in filas if f["TIPO"] == "TABLA"]
    assert len(glob) == 1 and len(tablas) == len(cfg.orden)
    g = glob[0]
    assert g["PROCESO"] == "ETL_VIGIA_PY.TODAS" and g["ESTADO"] == "OK" and g["CODIGO_SALIDA"] == 0
    assert g["FIN"] and g["DURACION_MS"] >= 0 and g["ID_PADRE"] is None
    # fin posterior al inicio y duración coherente (ms exactos)
    assert datetime.fromisoformat(g["FIN"]) >= datetime.fromisoformat(g["INICIO"])
    for t in tablas:
        assert t["ID_PADRE"] == g["ID"] and t["CORRIDA"] == g["CORRIDA"]
        assert t["ESTADO"] == "OK" and t["ETAPA_FINAL"] == "transaccion destino"
        assert t["MS_CARGAR_STAGING"] >= 0 and t["MS_TRANSACCION_DESTINO"] >= 0
        assert t["FILAS_ORIGEN"] == t["FILAS_CARGADAS"] > 0
        assert t["USUARIO"] == "dev" and t["EQUIPO"]
    # la duración global cubre las de las tablas
    assert g["DURACION_MS"] >= sum(t["DURACION_MS"] for t in tablas) - 5


def test_tabla_individual_sin_padre_y_dry_run(correr, consultar):
    assert correr("TIPSTRAN", dry_run=True) == 0
    f = _filas(consultar)[0]
    assert f["ID_PADRE"] is None and f["ESTADO"] == "DRY-RUN" and f["ETAPA_FINAL"] == "validar staging"
    assert f["MS_TRANSACCION_DESTINO"] is None            # el dry-run no llega a la transacción
    assert consultar("SELECT DRY_RUN FROM VIGIA.uPROCESOS")[0][0] == 1


def test_error_registra_etapa_y_enlaza_con_erroresproceso(correr, consultar, origen_rw):
    assert correr("TERCEROS") == 0
    origen_rw.execute("INSERT INTO psa065 (psa065td, psa065do, psa065es) VALUES ('CI', NULL, 'A')")
    origen_rw.commit()
    assert correr("TERCEROS") == 1
    f = _filas(consultar, "ESTADO <> 'OK'")[0]
    assert f["ESTADO"] == "ERROR" and f["CODIGO_SALIDA"] == 1 and f["ETAPA_FINAL"] == "transaccion destino"
    # enlace: ERRORESPROCESO.PROCESO = uPROCESOS.PROCESO y DATOSERROR empieza por corrida=<CORRIDA>
    enlaces = consultar(
        "SELECT e.ERROR FROM VIGIA.uPROCESOS u JOIN VIGIA.ERRORESPROCESO e ON e.PROCESO = u.PROCESO "
        "AND e.DATOSERROR LIKE 'corrida=' || u.CORRIDA || ' %' WHERE u.ID = ?", (f["ID"],))
    assert {r[0] for r in enlaces} >= {"INICIO", "ERROR", "FIN"}


def test_sin_tabla_uprocesos_la_carga_sigue(cfg, correr, consultar):
    import sqlite3
    con = sqlite3.connect(cfg.dev_dir / "destino.db")
    con.execute("DROP TABLE uPROCESOS")
    con.commit()
    con.close()
    assert correr("TIPSTRAN") == 0                         # solo avisa en el log
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TIPSTRAN")[0][0] == 11
