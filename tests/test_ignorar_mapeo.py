"""T1: columnas ignoradas y renombradas."""
import sqlite3


def _alter(cfg, sql, archivo="destino.db"):
    con = sqlite3.connect(cfg.dev_dir / archivo)
    con.execute(sql)
    con.commit()
    con.close()


def test_columna_ignorada_no_llega_al_destino(cfg, correr, consultar):
    cfg.tabla("TIPSTRAN").ignorar = ["ALERTAMANDATORIA"]
    _alter(cfg, "ALTER TABLE STG_TIPSTRAN DROP COLUMN ALERTAMANDATORIA")   # staging no la necesita
    assert correr("TIPSTRAN") == 0
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TIPSTRAN WHERE ALERTAMANDATORIA IS NOT NULL")[0][0] == 0
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TIPSTRAN")[0][0] == 11


def test_columna_ignorada_permite_destino_sin_ella(cfg, correr):
    cfg.tabla("TIPSTRAN").ignorar = ["ALERTAMANDATORIA"]
    _alter(cfg, "ALTER TABLE STG_TIPSTRAN DROP COLUMN ALERTAMANDATORIA")
    _alter(cfg, "ALTER TABLE TMP_TIPSTRAN DROP COLUMN ALERTAMANDATORIA")
    assert correr("TIPSTRAN") == 0


def test_columna_renombrada(cfg, correr, consultar):
    cfg.tabla("TIPSTRAN").mapeo = {"NOMBRE": "DESCRIPCION"}
    _alter(cfg, "ALTER TABLE TMP_TIPSTRAN RENAME COLUMN NOMBRE TO DESCRIPCION")
    assert correr("TIPSTRAN") == 0
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TIPSTRAN WHERE DESCRIPCION LIKE 'TIPO %'")[0][0] == 11


def test_sin_mapeo_aborta(cfg, correr, consultar):
    _alter(cfg, "ALTER TABLE TMP_TIPSTRAN RENAME COLUMN NOMBRE TO DESCRIPCION")
    assert correr("TIPSTRAN") == 3
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TIPSTRAN")[0][0] == 0


def test_ignorar_columna_inexistente(cfg, correr):
    cfg.tabla("TIPSTRAN").ignorar = ["NO_EXISTE"]
    assert correr("TIPSTRAN") == 3


def test_mapeo_origen_inexistente(cfg, correr):
    cfg.tabla("TIPSTRAN").mapeo = {"NO_EXISTE": "NOMBRE"}
    assert correr("TIPSTRAN") == 3


def test_mapeo_destino_inexistente(cfg, correr):
    cfg.tabla("TIPSTRAN").mapeo = {"NOMBRE": "NO_EXISTE"}
    assert correr("TIPSTRAN") == 3


def test_fechas_y_clave_refieren_alias_origen(cfg, correr):
    cfg.tabla("TIPSTRAN").clave = ["NO_EXISTE"]
    assert correr("TIPSTRAN") == 3


def test_staging_automatico_no_requiere_tablas_stg(cfg, correr, consultar):
    """El origen es un script: el staging es temporal y no hace falta crear STG_*."""
    for t in ("STG_TIPSTRAN", "STG_TERCEROS"):
        _alter(cfg, f"DROP TABLE {t}")
    assert correr("TIPSTRAN") == 0
    assert correr("TERCEROS") == 0
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TIPSTRAN")[0][0] == 11


def test_origen_con_menos_columnas_que_destino(cfg, correr, consultar):
    """El destino puede tener más columnas que el script: quedan en NULL."""
    _alter(cfg, "ALTER TABLE TMP_TIPSTRAN ADD COLUMN EXTRA TEXT")
    assert correr("TIPSTRAN") == 0
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TIPSTRAN WHERE EXTRA IS NULL")[0][0] == 11
