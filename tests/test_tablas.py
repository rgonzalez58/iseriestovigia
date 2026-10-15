"""TIPSTRAN, DATOSADICIONALES, PRODUCTOS y el orquestador."""
from etl import config, runner


def n(consultar, tabla):
    return consultar(f"SELECT COUNT(*) FROM VIGIA.{tabla}")[0][0]


def test_tipstran(correr, consultar):
    assert correr("TIPSTRAN") == 0
    assert n(consultar, "TMP_TIPSTRAN") == 11          # 12 menos 1 inactivo


def test_datosadicionales(correr, consultar):
    assert correr("DATOSADICIONALES") == 0
    assert n(consultar, "TMP_DATOSADICIONALESTERCERO") == 96
    unos = consultar("SELECT COUNT(*) FROM VIGIA.TMP_DATOSADICIONALESTERCERO WHERE VALORDATO = '1'")[0][0]
    assert 0 < unos < 96
    assert {r[0] for r in consultar("SELECT DISTINCT TIPODATOADICIONAL_CODIGO "
                                    "FROM VIGIA.TMP_DATOSADICIONALESTERCERO")} == {"LISTAS_RESTRICTIVAS"}


def test_productos_tres_ramas(correr, consultar):
    assert correr("PRODUCTOS") == 0
    productos = {r[0] for r in consultar("SELECT DISTINCT PRODUCTO_CODIGO FROM VIGIA.TMP_PRODUCTOSTERCERO")}
    assert productos == {"PRO-31-GS", "PRO-35-ME", "PRO-500-ME"}     # grupo A, grupo B, resto
    # fecha '00000000' -> 1753-01-01
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_PRODUCTOSTERCERO WHERE FECHAAPERTURA = '1753-01-01'")[0][0] > 0


def test_parametros_se_sustituyen(cfg):
    sql = cfg.sql_origen(cfg.tabla("PRODUCTOS"))
    assert "@@" not in sql and "35,22,225" in sql


def test_parametro_invalido(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv("PRODUCTOS_GRUPOA", "1; DROP TABLE x")
    with pytest.raises(config.ErrorConfig):
        config.cargar(entorno="dev", dev_dir=tmp_path)


def test_todas(cfg, consultar):
    codigo, resumen = runner.ejecutar_todas(cfg)
    assert codigo == 0
    assert [t for t, _ in resumen] == ["TERCEROS", "DATOSADICIONALES", "PRODUCTOS", "TIPSTRAN", "TRANSACCIONES"]
    assert n(consultar, "TMP_TERCEROS") == 96


def test_todas_se_detiene_en_error(cfg, consultar, origen_rw):
    origen_rw.execute("DELETE FROM psa065")
    origen_rw.commit()
    codigo, resumen = runner.ejecutar_todas(cfg)
    assert codigo == 3 and resumen == [("TERCEROS", 3)]


def test_procesos_registra_global_y_por_tabla(cfg, consultar):
    from etl import runner
    codigo, _ = runner.ejecutar_todas(cfg)
    assert codigo == 0
    g = [r[0] for r in consultar("SELECT RESULTADO FROM VIGIA.PROCESOS WHERE NOMBRE='ETL_VIGIA_PY.TODAS' ORDER BY FECHA, RESULTADO DESC")]
    assert len(g) == 2
    assert any(x.startswith("INICIO corrida=") for x in g)
    fin = [x for x in g if x.startswith("FIN")][0]
    assert "estado=OK" in fin and "tablas=5/5" in fin and "TERCEROS:PT" in fin
    corrida = fin.split("corrida=")[1].split()[0]
    assert all(corrida in r[0] for r in consultar("SELECT RESULTADO FROM VIGIA.PROCESOS WHERE NOMBRE='ETL_VIGIA_PY.TERCEROS'"))
    assert consultar("SELECT COUNT(*) FROM VIGIA.PROCESOS WHERE NOMBRE='ETL_VIGIA_PY.TIPSTRAN'")[0][0] == 2
    # Vínculo con ERRORESPROCESO: mismo PROCESO que NOMBRE y misma corrida.
    j = consultar("SELECT COUNT(*) FROM VIGIA.PROCESOS p JOIN VIGIA.ERRORESPROCESO e ON e.PROCESO = p.NOMBRE "
                  "AND e.DATOSERROR LIKE '%corrida=' || ? || '%' WHERE p.NOMBRE = 'ETL_VIGIA_PY.TERCEROS'" .replace("?", f"'{corrida}'"))
    assert j[0][0] > 0
    assert consultar("SELECT COUNT(*) FROM VIGIA.ERRORESPROCESO WHERE PROCESO='ETL_VIGIA_PY.TODAS' AND ERROR='FIN'")[0][0] == 1


# ---------------------------------------------------------------- TRANSACCIONES
def _sql_origen(cfg):
    return cfg.sql_origen(cfg.tabla("TRANSACCIONES"))


def test_transacciones_carga_tres_ramas_y_columnas(correr, consultar, cfg):
    assert correr("TRANSACCIONES") == 0
    assert "@@" not in _sql_origen(cfg)
    assert {r[0] for r in consultar("SELECT DISTINCT PRODUCTO_CODIGO FROM VIGIA.TMP_TRANSACCIONES")} ==         {"PRO-31-GS", "PRO-500-ME", "PRO-35-ME"}
    # Columnas que el script no trae quedan en NULL (ej. NOMBREREMITENTE) y las convertidas son texto sin relleno.
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TRANSACCIONES WHERE NOMBREREMITENTE IS NOT NULL")[0][0] == 0
    fila = consultar("SELECT PRODUCTO_NUMERO, INTERMEDIARIO_CODIGO, FECHA, VALOR FROM VIGIA.TMP_TRANSACCIONES LIMIT 1")[0]
    assert fila[1] == "CAN-7" and fila[2].startswith("2023-") and len(fila[0]) <= 20


def test_transacciones_no_cuenta_el_origen_dos_veces(correr, cfg, monkeypatch):
    from etl import snapshot
    sqls = []
    original = snapshot._contar
    monkeypatch.setattr(snapshot, "_contar", lambda c, q: (sqls.append(q), original(c, q))[1])
    assert correr("TRANSACCIONES") == 0
    assert not any("psa067" in q for q in sqls)      # ningún COUNT sobre el script de origen


def test_transacciones_texto_max_aborta_sin_mostrar_valores(correr, consultar, origen_rw):
    origen_rw.execute("UPDATE psa067 SET psa067op = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', psa067so = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'")
    origen_rw.commit()
    assert correr("TRANSACCIONES") == 3
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TRANSACCIONES")[0][0] == 0      # destino sin cambios


def test_transacciones_origen_vacio_aborta(correr, origen_rw):
    origen_rw.execute("DELETE FROM psa067")
    origen_rw.commit()
    assert correr("TRANSACCIONES") == 3
    assert correr("TRANSACCIONES", forzar=True) == 0


def test_script_optimizado_da_el_mismo_resultado_que_el_original(cfg, origen_rw):
    """transacciones_optimizado.sql (una lectura) == transacciones.sql (3 ramas con UNION), incl. duplicados y NULL."""
    from dataclasses import replace
    origen_rw.execute("INSERT INTO psa067 SELECT * FROM psa067 LIMIT 5")                    # filas duplicadas
    origen_rw.execute("INSERT INTO psa067 SELECT * FROM psa067 WHERE psa067mo = 31 LIMIT 1")
    origen_rw.execute("UPDATE psa067 SET psa067mo = NULL WHERE rowid IN (SELECT rowid FROM psa067 LIMIT 2)")
    origen_rw.commit()
    t = cfg.tabla("TRANSACCIONES")
    nuevo = replace(t, sql=t.sql.with_name("transacciones_optimizado.sql"))
    from etl import db
    lector = db.conectar_origen(cfg)
    original = lector.execute(cfg.sql_origen(t)).fetchall()
    variante = lector.execute(cfg.sql_origen(nuevo)).fetchall()
    lector.close()
    assert original and set(original) == set(variante) and len(original) == len(variante)


# ---------------------------------------------------------------- limpiar al final
def test_limpiar_al_final_desactivado_por_defecto(correr, consultar):
    assert correr("TIPSTRAN") == 0
    assert n(consultar, "TMP_TIPSTRAN") == 11


def test_limpiar_al_final_una_tabla_por_parametro(correr, consultar):
    assert correr("TIPSTRAN", limpiar=True) == 0
    assert n(consultar, "TMP_TIPSTRAN") == 0
    estados = [r[0] for r in consultar("SELECT ERROR FROM VIGIA.ERRORESPROCESO WHERE TAREA='VIGIA.TMP_TIPSTRAN'")]
    assert "LIMPIEZA" in estados and estados[-1] == "FIN"
    r = consultar("SELECT RESULTADO FROM VIGIA.PROCESOS WHERE NOMBRE='ETL_VIGIA_PY.TIPSTRAN' AND RESULTADO LIKE 'LIMPIEZA%'")
    assert "filas=11" in r[0][0]


def test_limpiar_al_final_por_config_y_no_limpiar_manda_la_linea_de_comandos(cfg, correr, consultar):
    cfg.tabla("TIPSTRAN").limpiar_al_final = True
    assert correr("TIPSTRAN", limpiar=False) == 0
    assert n(consultar, "TMP_TIPSTRAN") == 11
    assert correr("TIPSTRAN") == 0                      # sin opción: usa config.yaml
    assert n(consultar, "TMP_TIPSTRAN") == 0


def test_limpiar_no_aplica_en_dry_run_ni_ante_error(cfg, correr, consultar, origen_rw):
    assert correr("TIPSTRAN") == 0
    assert correr("TIPSTRAN", limpiar=True, dry_run=True) == 0
    assert n(consultar, "TMP_TIPSTRAN") == 11
    origen_rw.execute("DELETE FROM psa068")
    origen_rw.commit()
    assert correr("TIPSTRAN", limpiar=True) == 3        # origen vacío: aborta y no limpia
    assert n(consultar, "TMP_TIPSTRAN") == 11


def test_todas_limpia_al_terminar_solo_si_todo_salio_bien(cfg, consultar, origen_rw):
    from etl import runner
    codigo, _ = runner.ejecutar_todas(cfg, limpiar=True)
    assert codigo == 0
    assert all(n(consultar, t) == 0 for t in ("TMP_TERCEROS", "TMP_TIPSTRAN", "TMP_TRANSACCIONES"))
    fin = consultar("SELECT RESULTADO FROM VIGIA.PROCESOS WHERE NOMBRE='ETL_VIGIA_PY.TODAS' AND RESULTADO LIKE 'FIN%'")[0][0]
    assert "limpiadas=" in fin and "TRANSACCIONES" in fin
    origen_rw.execute("DELETE FROM psa068")
    origen_rw.commit()
    codigo, _ = runner.ejecutar_todas(cfg, limpiar=True)    # TIPSTRAN aborta: no se limpia nada
    assert codigo == 3 and n(consultar, "TMP_TERCEROS") == 96


def test_scripts_optimizados_de_datosadicionales_y_productos_dan_el_mismo_resultado(cfg, origen_rw):
    from dataclasses import replace
    from etl import db
    origen_rw.execute("INSERT INTO psa066 SELECT * FROM psa066 LIMIT 4")                    # filas duplicadas
    origen_rw.execute("UPDATE psa066 SET psa066mo = NULL WHERE rowid IN (SELECT rowid FROM psa066 LIMIT 2)")
    origen_rw.commit()
    lector = db.conectar_origen(cfg)
    for nombre, archivo in (("DATOSADICIONALES", "datosadicionales_optimizado.sql"),
                            ("PRODUCTOS", "productos_optimizado.sql")):
        t = cfg.tabla(nombre)
        nuevo = replace(t, sql=t.sql.with_name(archivo))
        a = lector.execute(cfg.sql_origen(t)).fetchall()
        b = lector.execute(cfg.sql_origen(nuevo)).fetchall()
        assert a and set(a) == set(b) and len(a) == len(b), nombre
    lector.close()
