"""Integración sobre SQLite (dev). No valida comportamiento real de DB2/ODBC."""
from etl.lock import Lock


def n(consultar, tabla):
    return consultar(f"SELECT COUNT(*) FROM VIGIA.{tabla}")[0][0]


def test_carga_y_casos_borde(correr, consultar):
    assert correr("TERCEROS") == 0
    assert n(consultar, "TMP_TERCEROS") == 96          # 100 menos 4 inactivos
    # '0' -> NULL (borde 6) y fecha 0 -> NULL (borde 5)
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TERCEROS WHERE FUNCIONARIO_RESPONSABLE IS NULL")[0][0] > 0
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TERCEROS WHERE FECHAVINCULACION IS NULL "
                     "OR FECHAACTUALIZACION IS NULL")[0][0] > 0
    # sucursal '' -> 1 convertido a texto
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TERCEROS WHERE SUCURSAL_CODIGO = '1'")[0][0] > 0
    # columnas del destino sin origen quedan NULL
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TERCEROS WHERE OBSERVACION IS NOT NULL")[0][0] == 0
    fecha = consultar("SELECT FECHAACTUALIZACION FROM VIGIA.TMP_TERCEROS WHERE FECHAACTUALIZACION IS NOT NULL")[0][0]
    assert fecha == "2023-01-01"


def test_idempotente(correr, consultar):
    assert correr("TERCEROS") == 0
    assert correr("TERCEROS") == 0
    assert n(consultar, "TMP_TERCEROS") == 96


def test_dry_run_no_toca_destino(correr, consultar):
    assert correr("TERCEROS", dry_run=True) == 0
    assert n(consultar, "TMP_TERCEROS") == 0


def test_rollback_ante_fila_invalida(correr, consultar, origen_rw):
    assert correr("TERCEROS") == 0
    origen_rw.execute("INSERT INTO psa065 (psa065td, psa065do, psa065es) VALUES ('CI', NULL, 'A')")
    origen_rw.commit()
    assert correr("TERCEROS") == 1                      # NOT NULL en NUMIDE
    assert n(consultar, "TMP_TERCEROS") == 96           # conserva la carga anterior
    estados = [r[0] for r in consultar("SELECT ERROR FROM VIGIA.ERRORESPROCESO")]
    assert "ERROR" in estados
    assert "etapa" in consultar("SELECT DATOSERROR FROM VIGIA.ERRORESPROCESO WHERE ERROR='ERROR'")[0][0]
    fila = consultar("SELECT PROCESO, TAREA, FECADI, USUADI FROM VIGIA.ERRORESPROCESO LIMIT 1")[0]
    assert fila[0] == "ETL_VIGIA_PY.TERCEROS" and fila[1] == "VIGIA.TMP_TERCEROS" and fila[2] and fila[3]


def test_origen_vacio_aborta_y_forzar(correr, consultar, origen_rw):
    assert correr("TERCEROS") == 0
    origen_rw.execute("DELETE FROM psa065")
    origen_rw.commit()
    assert correr("TERCEROS") == 3
    assert n(consultar, "TMP_TERCEROS") == 96
    assert correr("TERCEROS", forzar=True) == 0
    assert n(consultar, "TMP_TERCEROS") == 0


def test_caida_brusca(correr, consultar, origen_rw):
    assert correr("TERCEROS") == 0
    origen_rw.execute("DELETE FROM psa065 WHERE rowid % 4 != 0")
    origen_rw.commit()
    assert correr("TERCEROS") == 3
    assert n(consultar, "TMP_TERCEROS") == 96
    assert correr("TERCEROS", forzar=True) == 0


def test_ya_en_ejecucion(cfg, correr):
    with Lock(cfg.logs / "TERCEROS.lock"):
        assert correr("TERCEROS") == 2


def test_columna_origen_sin_destino_aborta(correr, consultar, cfg):
    import sqlite3
    con = sqlite3.connect(cfg.dev_dir / "destino.db")
    con.execute("ALTER TABLE TMP_TIPSTRAN DROP COLUMN ALERTAMANDATORIA")
    con.commit()
    con.close()
    assert correr("TIPSTRAN") == 3
    assert n(consultar, "TMP_TIPSTRAN") == 0


def test_mensaje_largo_se_reparte_en_varios_registros(cfg, correr, consultar):
    import logging
    from etl import db
    from etl.logger import registrar_proceso
    d = db.conectar_destino(cfg)
    registrar_proceso(cfg, d, logging.getLogger("t"), "VIGIA.TMP_X", "ERROR", "x" * 1200)
    d.close()
    filas = consultar("SELECT LENGTH(DATOSERROR) FROM VIGIA.ERRORESPROCESO WHERE TAREA='VIGIA.TMP_X' ORDER BY 1 DESC")
    assert [r[0] for r in filas] == [500, 500, 200]


def test_registra_fin_con_duracion_iso(correr, consultar):
    assert correr("TIPSTRAN") == 0
    fin = consultar("SELECT DATOSERROR FROM VIGIA.ERRORESPROCESO WHERE ERROR='FIN'")
    assert len(fin) == 1 and "estado=OK" in fin[0][0]
    import re
    assert re.search(r"duracion=PT\d+\.\d{2}M( |$)", fin[0][0])


def test_trozos_repiten_la_corrida(cfg, consultar):
    import logging
    from etl import db
    from etl.logger import registrar_proceso
    d = db.conectar_destino(cfg)
    registrar_proceso(cfg, d, logging.getLogger("t"), "VIGIA.TMP_Y", "ERROR", "corrida=C1 " + "x" * 1000)
    d.close()
    filas = [r[0] for r in consultar("SELECT DATOSERROR FROM VIGIA.ERRORESPROCESO WHERE TAREA='VIGIA.TMP_Y'")]
    assert len(filas) == 3 and all(f.startswith("corrida=C1 ") and len(f) <= 500 for f in filas)
