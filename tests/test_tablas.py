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
    assert [t for t, _ in resumen] == ["TIPSTRAN", "TERCEROS", "DATOSADICIONALES", "PRODUCTOS", "TRANSACCIONES"]
    assert n(consultar, "TMP_TERCEROS") == 96


def test_todas_se_detiene_en_error(cfg, consultar, origen_rw):
    origen_rw.execute("DELETE FROM psa065")
    origen_rw.commit()
    codigo, resumen = runner.ejecutar_todas(cfg)
    assert codigo == 3 and resumen == [("TIPSTRAN", 0), ("TERCEROS", 3)]


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
    origen_rw.execute("DELETE FROM psa066")
    origen_rw.commit()
    codigo, _ = runner.ejecutar_todas(cfg, limpiar=True)    # PRODUCTOS aborta: no se limpia nada
    assert codigo == 3 and n(consultar, "TMP_TERCEROS") == 96 and n(consultar, "TMP_TIPSTRAN") == 11


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


def test_medir_no_modifica_tablas_y_muestra_fases(cfg, consultar, capsys):
    from etl import diagnostico
    assert diagnostico.medir(cfg, "TIPSTRAN", 100) == 0
    out = capsys.readouterr().out
    assert "lectura" in out and "conversión" in out and "escritura" in out
    assert n(consultar, "TMP_TIPSTRAN") == 0                    # el destino real no se tocó


def test_carga_registra_fases_en_el_fin(cfg, correr, consultar):
    assert correr("TIPSTRAN") == 0
    fin = consultar("SELECT DATOSERROR FROM VIGIA.ERRORESPROCESO WHERE ERROR='FIN' AND TAREA='VIGIA.TMP_TIPSTRAN'")[0][0]
    assert "fases=lectura_origen:" in fin and "etapas=" in fin


def test_listar_respeta_el_orden_y_orden_invalido_falla(tmp_path, capsys):
    import pytest
    import main as cli
    from etl import config
    assert cli.main(["listar"]) == 0
    out = capsys.readouterr().out
    assert out.index("TIPSTRAN") < out.index("TERCEROS") < out.index("TRANSACCIONES")
    texto = (config.RAIZ / "config.yaml").read_text(encoding="utf-8")
    invertido = texto.replace("orden: [TIPSTRAN, TERCEROS, DATOSADICIONALES, PRODUCTOS, TRANSACCIONES]",
                              "orden: [TRANSACCIONES, TIPSTRAN, PRODUCTOS, DATOSADICIONALES, TERCEROS]")
    assert invertido != texto
    (tmp_path / "config.yaml").write_text(invertido, encoding="utf-8")
    assert config.cargar(raiz=tmp_path, entorno="dev", dev_dir=tmp_path / "d").orden[0] == "TRANSACCIONES"
    (tmp_path / "config.yaml").write_text(texto.replace("orden: [TIPSTRAN,", "orden: [TIPSTRANZ,"), encoding="utf-8")
    with pytest.raises(config.ErrorConfig):
        config.cargar(raiz=tmp_path, entorno="dev", dev_dir=tmp_path / "d")


def _raiz_con(tmp_path, base=None, local=None):
    from etl import config
    texto = (config.RAIZ / "config.yaml").read_text(encoding="utf-8")
    (tmp_path / "config.yaml").write_text(base or texto, encoding="utf-8")
    if local is not None:
        (tmp_path / "config.local.yaml").write_text(local, encoding="utf-8")
    return config.cargar(raiz=tmp_path, entorno="dev", dev_dir=tmp_path / "d")


def test_config_local_se_mezcla_sobre_config_yaml(tmp_path):
    c = _raiz_con(tmp_path, local="orden: [TIPSTRAN, TERCEROS]\ndefaults:\n  limpiar_al_final: true\n"
                                  "tablas:\n  TERCEROS:\n    lote: 777\n")
    assert c.orden == ["TIPSTRAN", "TERCEROS"]
    assert c.tabla("TERCEROS").lote == 777 and c.tabla("TERCEROS").fechas       # el resto de TERCEROS se conserva
    assert c.tabla("TIPSTRAN").limpiar_al_final is True
    assert c.tabla("PRODUCTOS").lote == 5000


def test_config_local_ausente_o_vacio_no_cambia_nada(tmp_path):
    assert _raiz_con(tmp_path).orden[0] == "TIPSTRAN"
    assert _raiz_con(tmp_path, local="").orden[0] == "TIPSTRAN"


def test_claves_mal_escritas_se_rechazan(tmp_path):
    import pytest
    from etl import config
    for local in ("tablas:\n  TERCEROS:\n    limpiar_al_fin: true\n", "ordenes: [TERCEROS]\n",
                  "defaults:\n  lotes: 10\n", "orden: [\n", "- uno\n- dos\n"):
        with pytest.raises(config.ErrorConfig):
            _raiz_con(tmp_path, local=local)


def test_escritura_por_valores_equivale_a_parametros_y_escapa_comillas(cfg, correr, consultar, origen_rw):
    origen_rw.execute("UPDATE psa065 SET psa065nm = ' O''Brien; DROP TABLE x -- ', psa065ap = 'Ñandú \"x\"' WHERE rowid <= 3")
    origen_rw.commit()
    assert correr("TERCEROS") == 0
    esperado = consultar("SELECT NOMBRES, APELLIDOS, ACTIVO, FECHAVINCULACION, SUCURSAL_CODIGO FROM VIGIA.TMP_TERCEROS ORDER BY NUMIDE")
    cfg.tabla("TERCEROS").escritura = "valores"
    assert correr("TERCEROS") == 0
    obtenido = consultar("SELECT NOMBRES, APELLIDOS, ACTIVO, FECHAVINCULACION, SUCURSAL_CODIGO FROM VIGIA.TMP_TERCEROS ORDER BY NUMIDE")
    assert obtenido == esperado and any("O'Brien; DROP TABLE x --" in r[0] for r in obtenido)
    assert n(consultar, "TMP_TERCEROS") == 96


def test_escritura_por_valores_en_todas_las_tablas(cfg, consultar):
    from etl import runner
    for t in cfg.tablas.values():
        t.escritura = "valores"
    codigo, _ = runner.ejecutar_todas(cfg)
    assert codigo == 0 and n(consultar, "TMP_TRANSACCIONES") > 0 and n(consultar, "TMP_TIPSTRAN") == 11


def test_literales_sql_server():
    from etl import config, dialecto
    c = type("C", (), {"entorno": "prod"})()
    assert dialecto._literal(c, None) == "NULL" and dialecto._literal(c, 5) == "5"
    assert dialecto._literal(c, "a'b\x00") == "N'a''b'"
    from decimal import Decimal
    assert dialecto._literal(c, Decimal("1E+3")) == "1000"
    assert len(dialecto.sqls_insert_valores(c, "T", ["A"], [("x",)] * 2500)) == 3


# ---------------------------------------------------------------- hilos_escritura (carga con varias conexiones)
def _tablas_staging(consultar):
    return [r[0] for r in consultar("SELECT name FROM VIGIA.sqlite_master WHERE type='table' AND name LIKE 'STGPAR%'")]


def test_hilos_cargan_lo_mismo_que_un_hilo_y_sueltan_el_staging(cfg, correr, consultar):
    assert correr("TERCEROS") == 0
    esperado = consultar("SELECT * FROM VIGIA.TMP_TERCEROS ORDER BY NUMIDE, TIPIDE_CODIGO")
    t = cfg.tabla("TERCEROS")
    t.hilos_escritura, t.lote = 4, 7                      # muchos lotes pequeños repartidos entre 4 conexiones
    assert correr("TERCEROS") == 0
    assert consultar("SELECT * FROM VIGIA.TMP_TERCEROS ORDER BY NUMIDE, TIPIDE_CODIGO") == esperado
    assert _tablas_staging(consultar) == []               # el staging compartido se borró


def test_hilos_en_transacciones_y_registra_fases(cfg, correr, consultar):
    assert correr("TRANSACCIONES") == 0
    esperado = n(consultar, "TMP_TRANSACCIONES")
    t = cfg.tabla("TRANSACCIONES")
    t.hilos_escritura, t.lote = 3, 11
    assert correr("TRANSACCIONES") == 0 and n(consultar, "TMP_TRANSACCIONES") == esperado
    fin = consultar("SELECT DATOSERROR FROM VIGIA.ERRORESPROCESO WHERE ERROR='FIN' AND TAREA='VIGIA.TMP_TRANSACCIONES' "
                    "ORDER BY FECADI DESC")[0][0]
    assert "espera_cola:" in fin or "fases=" in fin


def test_hilos_dry_run_no_toca_el_destino_ni_deja_staging(cfg, correr, consultar):
    assert correr("TIPSTRAN") == 0
    cfg.tabla("TIPSTRAN").hilos_escritura = 2
    assert correr("TIPSTRAN", dry_run=True) == 0
    assert n(consultar, "TMP_TIPSTRAN") == 11 and _tablas_staging(consultar) == []


def test_hilos_error_en_un_lote_aborta_sin_colgarse_y_conserva_el_destino(cfg, correr, consultar, monkeypatch):
    from etl import snapshot
    assert correr("TERCEROS") == 0
    t = cfg.tabla("TERCEROS")
    t.hilos_escritura, t.lote = 4, 5
    original, llamadas = snapshot.escribir_lote, []

    def falla_en_el_tercero(*a, **k):
        llamadas.append(1)
        if len(llamadas) == 3:
            raise RuntimeError("fallo simulado de escritura")
        return original(*a, **k)

    monkeypatch.setattr(snapshot, "escribir_lote", falla_en_el_tercero)
    assert correr("TERCEROS") == 1
    assert n(consultar, "TMP_TERCEROS") == 96             # conserva la carga anterior
    assert _tablas_staging(consultar) == []
    msg = consultar("SELECT DATOSERROR FROM VIGIA.ERRORESPROCESO WHERE ERROR='ERROR' ORDER BY FECADI DESC")[0][0]
    assert "fallo simulado" in msg and "cargar staging" in msg


def test_hilos_invalidos_se_rechazan(tmp_path):
    import pytest
    from etl import config
    for valor in (0, 17, -1):
        with pytest.raises(config.ErrorConfig):
            _raiz_con(tmp_path, local=f"tablas:\n  TERCEROS:\n    hilos_escritura: {valor}\n")


# ---------------------------------------------------------------- lectores y ventana (particiones de fecha)
def _fechas_variadas(origen_rw):
    """Reparte las transacciones en varios meses/años (la semilla las deja todas en enero de 2023)."""
    origen_rw.execute("UPDATE psa067 SET psa067fc = CASE rowid % 5 WHEN 0 THEN 20220315 WHEN 1 THEN 20230101 "
                      "WHEN 2 THEN 20230720 WHEN 3 THEN 20240910 ELSE 20241231 END")
    origen_rw.commit()


def _transacciones(consultar):
    return sorted(consultar("SELECT TERCERO_NUMIDE, PRODUCTO_NUMERO, FECHA, CONSECUTIVO, VALOR "
                            "FROM VIGIA.TMP_TRANSACCIONES"))


def test_particiones_cubren_todas_las_filas_exactamente_una_vez():
    import sqlite3
    from datetime import date
    from etl import particiones
    muestras = [20220315, 20230101, 20230131, 20230201, 20240910, 20241231, 0, 99999999, 17000101, 20230231]
    for tipo, conv in (("num", lambda v: v), ("txt", lambda v: f"{v:08d}"), ("iso", lambda v: f"{v:08d}")):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE t (f)")
        for v in muestras:
            c.execute("INSERT INTO t VALUES (?)", (conv(v),))
        c.execute("INSERT INTO t VALUES (NULL)")
        filtros = particiones.construir_filtros("f", "num" if tipo == "num" else "txt", date(2022, 3, 15),
                                                date(2024, 12, 31))
        asignadas = []
        for f in filtros:
            asignadas += [r[0] for r in c.execute("SELECT rowid FROM t WHERE 1 = 1" + f).fetchall()]
        assert sorted(asignadas) == list(range(1, len(muestras) + 2)), (tipo, filtros[:2])


def test_filtros_de_rango_cubren_cada_fecha_una_sola_vez():
    import sqlite3
    from datetime import date
    from etl import particiones
    muestras = [20220101, 20230228, 20230301, 20230302, 20230331, 20230401, 0, 99999999]
    for tipo, conv in (("num", lambda v: v), ("txt", lambda v: f"{v:08d}")):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE t (f)")
        c.executemany("INSERT INTO t VALUES (?)", [(conv(v),) for v in muestras])
        for desde, hasta, esperadas in ((date(2023, 3, 1), date(2023, 3, 31), [20230301, 20230302, 20230331]),
                                         (date(2023, 3, 2), date(2023, 3, 2), [20230302])):
            filtros = particiones.filtros_rango("f", tipo, desde, hasta)
            assert len(filtros) == (hasta - desde).days + 1                 # una partición por día
            vistas = [r[0] for f in filtros for r in c.execute("SELECT f FROM t WHERE 1 = 1" + f).fetchall()]
            assert sorted(int(v) for v in vistas) == esperadas


def test_lectores_y_escritores_cargan_lo_mismo_que_la_carga_normal(cfg, correr, consultar, origen_rw):
    _fechas_variadas(origen_rw)
    assert correr("TRANSACCIONES") == 0
    esperado = _transacciones(consultar)
    assert len({r[2][:7] for r in esperado}) >= 4
    t = cfg.tabla("TRANSACCIONES")
    t.lectores, t.hilos_escritura, t.lote = 3, 2, 9
    assert correr("TRANSACCIONES") == 0
    assert _transacciones(consultar) == esperado
    assert _tablas_staging(consultar) == []


def _fecha_de(consultar):
    return sorted({r[2][:10] for r in _transacciones(consultar)})


def test_fecha_por_defecto_ayer_carga_solo_un_dia(cfg, correr, consultar, origen_rw, monkeypatch):
    from datetime import date
    from etl import particiones
    _fechas_variadas(origen_rw)
    monkeypatch.setattr(particiones, "hoy", lambda: date(2024, 9, 11))
    cfg.tabla("TRANSACCIONES").fecha_defecto = "ayer"
    assert correr("TRANSACCIONES") == 0
    assert _fecha_de(consultar) == ["2024-09-10"]
    fin = consultar("SELECT DATOSERROR FROM VIGIA.ERRORESPROCESO WHERE ERROR='FIN' AND TAREA='VIGIA.TMP_TRANSACCIONES'")[0][0]
    assert "fechas=2024-09-10" in fin


def test_rango_con_un_solo_lector_es_un_unico_filtro():
    from datetime import date
    from etl import particiones
    assert len(particiones.filtros_rango("f", "num", date(2023, 3, 1), date(2023, 3, 31), partir=False)) == 1


def test_reproceso_por_rango_y_mes_con_lectores(cfg, correr, consultar, origen_rw):
    from datetime import date
    _fechas_variadas(origen_rw)
    assert correr("TRANSACCIONES", fechas=(date(2023, 1, 1), date(2023, 7, 31))) == 0
    assert _fecha_de(consultar) == ["2023-01-01", "2023-07-20"]
    uno = _transacciones(consultar)
    t = cfg.tabla("TRANSACCIONES")
    t.lectores, t.hilos_escritura, t.lote = 3, 2, 7
    assert correr("TRANSACCIONES", fechas=(date(2023, 1, 1), date(2023, 7, 31))) == 0
    assert _transacciones(consultar) == uno                                   # paralelo == un solo hilo
    assert correr("TRANSACCIONES", fechas=(date(2024, 9, 1), date(2024, 9, 30))) == 0
    assert _fecha_de(consultar) == ["2024-09-10"]                             # un mes pequeño tras uno grande: sin caída brusca


def test_todas_las_fechas_ignora_la_fecha_por_defecto(cfg, correr, consultar, origen_rw, monkeypatch):
    from datetime import date
    from etl import particiones
    _fechas_variadas(origen_rw)
    monkeypatch.setattr(particiones, "hoy", lambda: date(2024, 9, 11))
    cfg.tabla("TRANSACCIONES").fecha_defecto = "ayer"
    assert correr("TRANSACCIONES", fechas="todas") == 0
    assert len(_fecha_de(consultar)) == 5


def test_dia_sin_transacciones_segun_permitir_origen_vacio(cfg, correr, consultar, origen_rw):
    from datetime import date
    _fechas_variadas(origen_rw)
    assert correr("TRANSACCIONES") == 0
    vacio = (date(2030, 1, 1), date(2030, 1, 1))
    cfg.tabla("TRANSACCIONES").permitir_origen_vacio = False
    assert correr("TRANSACCIONES", fechas=vacio) == 3                         # sin permitirlo, aborta (destino intacto)
    assert len(_transacciones(consultar)) > 0
    cfg.tabla("TRANSACCIONES").permitir_origen_vacio = True
    assert correr("TRANSACCIONES", fechas=vacio) == 0
    assert _transacciones(consultar) == []                                    # TMP refleja que ese día no hubo nada


def test_cli_fechas_se_interpretan_y_validan(cfg, monkeypatch, capsys):
    import pytest
    import main as cli
    from datetime import date
    capturado = {}
    monkeypatch.setattr(cli.runner, "ejecutar_tabla", lambda c, t, dr, fz, limpiar=None, fechas=None: capturado.update(f=fechas) or 0)
    assert cli.main(["ejecutar", "--tabla", "TRANSACCIONES", "--fecha", "2026-10-15"]) == 0
    assert capturado["f"] == (date(2026, 10, 15), date(2026, 10, 15))
    cli.main(["ejecutar", "--tabla", "TRANSACCIONES", "--mes", "2026-02"])
    assert capturado["f"] == (date(2026, 2, 1), date(2026, 2, 28))
    cli.main(["ejecutar", "--tabla", "TRANSACCIONES", "--desde", "2026-09-01", "--hasta", "2026-09-30"])
    assert capturado["f"] == (date(2026, 9, 1), date(2026, 9, 30))
    cli.main(["ejecutar", "--tabla", "TRANSACCIONES", "--todas-las-fechas"])
    assert capturado["f"] == "todas"
    cli.main(["ejecutar", "--tabla", "TRANSACCIONES"])
    assert capturado["f"] is None
    for malo in (["--fecha", "2026-10-15", "--mes", "2026-10"], ["--desde", "2026-09-01"], ["--fecha", "15/10/2026"],
                 ["--desde", "2026-09-30", "--hasta", "2026-09-01"], ["--mes", "2026-13"]):
        with pytest.raises(SystemExit):
            cli.main(["ejecutar", "--tabla", "TRANSACCIONES"] + malo)


def test_config_fecha_defecto_invalida_o_sin_particion(tmp_path):
    import pytest
    from etl import config
    for local in ("tablas:\n  TRANSACCIONES:\n    fecha_defecto: manana\n",
                  "tablas:\n  TIPSTRAN:\n    fecha_defecto: ayer\n"):
        with pytest.raises(config.ErrorConfig):
            _raiz_con(tmp_path, local=local)


def test_error_en_un_lector_aborta_y_conserva_el_destino(cfg, correr, consultar, origen_rw, monkeypatch):
    import threading
    from etl import db
    _fechas_variadas(origen_rw)
    assert correr("TRANSACCIONES") == 0
    antes = _transacciones(consultar)
    t = cfg.tabla("TRANSACCIONES")
    t.lectores, t.hilos_escritura, t.lote = 3, 2, 5
    original = db.conectar_origen

    def falla_en_lectores(c):
        if threading.current_thread().name.startswith("lector"):
            raise ConnectionError("origen caído (simulado)")
        return original(c)

    monkeypatch.setattr(db, "conectar_origen", falla_en_lectores)
    assert correr("TRANSACCIONES") == 1
    assert _transacciones(consultar) == antes and _tablas_staging(consultar) == []


def test_lectores_exigen_particion_y_marcador_en_el_sql(tmp_path, cfg, correr):
    import pytest
    from etl import config
    with pytest.raises(config.ErrorConfig):
        _raiz_con(tmp_path, local="tablas:\n  TIPSTRAN:\n    lectores: 2\n")
    with pytest.raises(config.ErrorConfig):
        _raiz_con(tmp_path, local="tablas:\n  TRANSACCIONES:\n    lectores: 0\n")
    t = cfg.tabla("TIPSTRAN")
    t.lectores, t.particion = 2, {"tabla": "gnbweb_db.psa068", "columna": "psa068co"}
    assert correr("TIPSTRAN") == 3                           # su SQL no tiene @@FILTRO_FECHA@@


def test_medir_lectura_particionada_en_dev(cfg, capsys, origen_rw):
    from etl import diagnostico
    _fechas_variadas(origen_rw)
    assert diagnostico.medir(cfg, "TRANSACCIONES", 200, 10, 0, 3) == 0
    out = capsys.readouterr().out
    assert "particiones en total" in out and "3 conexión(es) al iSeries" in out and "1 conexión(es)" in out
    assert diagnostico.medir(cfg, "TIPSTRAN", 50, 10, 0, 2) == 0           # sin particion: se omite sin fallar
    assert "omitida" in capsys.readouterr().out


def test_comparar_sql_actual_y_optimizado_por_fecha(cfg, capsys, origen_rw):
    from datetime import date
    from etl import diagnostico
    _fechas_variadas(origen_rw)
    origen_rw.execute("INSERT INTO psa067 SELECT * FROM psa067 LIMIT 5")                    # duplicados exactos
    origen_rw.commit()
    rc = diagnostico.comparar(cfg, "TRANSACCIONES", "sql/origen/transacciones_optimizado.sql",
                              (date(2023, 1, 1), date(2023, 12, 31)))
    out = capsys.readouterr().out
    assert rc == 0 and "IGUALES" in out and "solo en actual" in out and "tiempo del COUNT" in out
    assert diagnostico.comparar(cfg, "TRANSACCIONES", "no_existe.sql") == 3


def test_sql_alterno_se_activa_desde_config_local(tmp_path):
    c = _raiz_con(tmp_path, local="tablas:\n  TRANSACCIONES:\n    sql: sql/origen/transacciones_optimizado.sql\n")
    assert c.tabla("TRANSACCIONES").sql.name == "transacciones_optimizado.sql"


# ---------------------------------------------------------------- una fecha repartida entre varios lectores
def test_subparticiones_cubren_cada_fila_una_sola_vez_incluso_nulos_negativos_y_fracciones():
    import sqlite3
    from datetime import date
    from etl import particiones
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t (f INTEGER, co REAL)")
    valores = [1, 2, 3, 4, 5, 6, 7, 0, -5, -6, 2.5, 100000000000, None, None]
    for v in valores:
        c.execute("INSERT INTO t VALUES (20230115, ?)", (v,))
    c.execute("INSERT INTO t VALUES (20230116, 3)")                 # otra fecha: no debe entrar
    mod = lambda col, r: f"ABS({col} % {r})"                          # noqa: E731
    for lectores in (2, 3, 4):
        filtros = particiones.filtros_rango("f", "num", date(2023, 1, 15), date(2023, 1, 15), sub="co",
                                            lectores=lectores, mod=mod)
        assert len(filtros) == lectores + 1                             # lectores trozos + el resto
        vistas = [r[0] for f in filtros for r in c.execute("SELECT rowid FROM t WHERE 1 = 1" + f).fetchall()]
        assert sorted(vistas) == list(range(1, len(valores) + 1)), (lectores, filtros[-1])


def test_subparticiones_solo_con_pocas_fechas():
    from datetime import date
    from etl import particiones
    mod = lambda col, r: f"ABS({col} % {r})"                          # noqa: E731
    mes = particiones.filtros_rango("f", "num", date(2023, 1, 1), date(2023, 1, 31), sub="co", lectores=4, mod=mod)
    assert len(mes) == 31 and "co" not in "".join(mes)                # 31 días ≥ 2 × lectores: solo por fecha
    uno = particiones.filtros_rango("f", "num", date(2023, 1, 1), date(2023, 1, 1), sub="co", lectores=4, mod=mod)
    assert len(uno) == 5
    un_lector = particiones.filtros_rango("f", "num", date(2023, 1, 1), date(2023, 1, 1), partir=False, sub="co",
                                          lectores=1, mod=mod)
    assert len(un_lector) == 1


def test_una_fecha_con_varios_lectores_carga_lo_mismo(cfg, correr, consultar, origen_rw):
    from datetime import date
    _fechas_variadas(origen_rw)
    dia = (date(2023, 7, 20), date(2023, 7, 20))
    assert correr("TRANSACCIONES", fechas=dia) == 0
    esperado = _transacciones(consultar)
    assert esperado
    t = cfg.tabla("TRANSACCIONES")
    t.lectores, t.hilos_escritura, t.lote = 4, 2, 5
    assert correr("TRANSACCIONES", fechas=dia) == 0
    assert _transacciones(consultar) == esperado
    assert _tablas_staging(consultar) == []


def test_reconstruir_indices_no_afecta_a_dev_ni_al_resultado(cfg, correr, consultar):
    assert correr("TRANSACCIONES") == 0
    esperado = _transacciones(consultar)
    cfg.tabla("TRANSACCIONES").reconstruir_indices = True       # solo aplica a SQL Server; en dev se ignora
    assert correr("TRANSACCIONES") == 0 and _transacciones(consultar) == esperado


def test_sin_parametros_de_fecha_se_lee_toda_la_tabla_por_defecto(tmp_path):
    from etl import config, particiones
    c = config.cargar(raiz=config.RAIZ, dev_dir=tmp_path / "dev_data", entorno="dev")
    assert c.tabla("TRANSACCIONES").fecha_defecto == ""
    assert particiones.resolver_rango(c.tabla("TRANSACCIONES")) is None
