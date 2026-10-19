"""Promoción TMP_TRANSACCIONES -> TRANSACCIONES (reemplaza VIGIA.PR_CARGATRANSACCIONES). Solo SQLite: las funciones
VIGIA.VAL* se simulan con VIGIA.DEV_VALIDOS; el comportamiento real contra SQL Server se valida en el equipo B."""
import sqlite3

import pytest

from etl import promocion
from etl.lock import Lock

BASE = dict(TERCERO_TIPIDE_CODIGO="CI", TERCERO_NUMIDE="100", PRODUCTO_CODIGO="31", PRODUCTO_NUMERO="P1",
            TIPTRAN_CODIGO="001", FECHA="2026-10-01 00:00:00", CONSECUTIVO=1, SISGEO_CODIGO="G1", SUCURSAL_CODIGO="1",
            VALOR=10.5)


def _fila(**kw):
    return {**BASE, **kw}


def _insertar(cfg, tabla, filas):
    con = sqlite3.connect(cfg.dev_dir / "destino.db")
    for f in filas:
        con.execute(f"INSERT INTO {tabla} ({', '.join(f)}) VALUES ({', '.join('?' * len(f))})", list(f.values()))
    con.commit()
    con.close()


def _validos(cfg, *reglas):
    _insertar(cfg, "DEV_VALIDOS", [dict(VALIDADOR=v, CLAVE=c, VALIDO=ok) for v, c, ok in reglas])


def _promover(cfg, **kw):
    return promocion.ejecutar_promocion(cfg, "TRANSACCIONES", **kw)


def test_promueve_valida_rechaza_y_aisla_filas_con_error(cfg, consultar):
    previa = _fila(CONSECUTIVO=1, TELEFONOREMITE="x")
    _insertar(cfg, "TRANSACCIONES", [previa])                                 # ya está: no es pendiente
    _insertar(cfg, "TMP_TRANSACCIONES", [
        previa,
        _fila(CONSECUTIVO=2, SUCURSAL_CODIGO="9"),                            # sucursal inválida
        _fila(CONSECUTIVO=3, TERCERO_NUMIDE="X2"),                            # tercero inválido
        _fila(CONSECUTIVO=4, TELEFONOREMITE="555", ESTADOOPERACION="   "),    # válida; estado en blanco -> NULL
        _fila(CONSECUTIVO=5, TIPMON_CODIGO="USD"),                            # válida con campo opcional
        _fila(CONSECUTIVO=6, VALOR=1),                                        # misma clave que la siguiente:
        _fila(CONSECUTIVO=6, VALOR=2),                                        # la segunda choca con el índice único
    ])
    _validos(cfg, ("VALSUCURSAL", "9", 0), ("VALTERCERO", "CI|X2", 0))
    assert _promover(cfg) == 0

    assert consultar("SELECT COUNT(*) FROM VIGIA.TRANSACCIONES")[0][0] == 1 + 3      # previa + 4, 5 y una de las dos "6"
    nuevas = consultar("SELECT CONSECUTIVO, FECADI, USUADI, TELEFONOREMITE, ESTADOOPERACION FROM VIGIA.TRANSACCIONES "
                       "WHERE CONSECUTIVO > 1 ORDER BY CONSECUTIVO")
    assert [f[0] for f in nuevas] == [4, 5, 6]
    assert all(f[1] and f[2] == "dev" for f in nuevas)
    assert nuevas[0][3] is None and nuevas[0][4] is None           # TELEFONOREMITE no se inserta (igual que el SP); '  ' -> NULL

    errores = consultar("SELECT ERROR FROM VIGIA.ERRORESPROCESO WHERE PROCESO = 'Carga Datos Base' "
                        "AND TAREA = 'Carga Transacciones' ORDER BY ERROR")
    textos = [e[0] for e in errores]
    assert len(textos) == 3
    assert " Sucursal Invalida 9" in textos and " Tercero InvalidoCI-X2" in textos
    # lo promovido sale de TMP; los rechazados quedan para revisarlos
    quedan = consultar("SELECT CONSECUTIVO FROM VIGIA.TMP_TRANSACCIONES ORDER BY CONSECUTIVO")
    assert [q[0] for q in quedan] == [2, 3]

    u = consultar("SELECT PROCESO, ESTADO, CODIGO_SALIDA, FILAS_ORIGEN, FILAS_CARGADAS FROM VIGIA.uPROCESOS")[0]
    assert u == ("ETL_VIGIA_PY.PROMO_TRANSACCIONES", "OK", 0, 6, 3)
    assert consultar("SELECT COUNT(*) FROM VIGIA.ERRORESPROCESO WHERE PROCESO = 'ETL_VIGIA_PY.PROMO_TRANSACCIONES' "
                     "AND ERROR = 'FIN'")[0][0] >= 1

    # idempotente: nada pendiente
    assert _promover(cfg) == 0
    assert consultar("SELECT COUNT(*) FROM VIGIA.TRANSACCIONES")[0][0] == 4


def test_dry_run_no_escribe_en_tablas_reales(cfg, consultar):
    _insertar(cfg, "TMP_TRANSACCIONES", [_fila(CONSECUTIVO=1), _fila(CONSECUTIVO=2, SUCURSAL_CODIGO="9")])
    _validos(cfg, ("VALSUCURSAL", "9", 0))
    assert _promover(cfg, dry_run=True) == 0
    assert consultar("SELECT COUNT(*) FROM VIGIA.TRANSACCIONES")[0][0] == 0
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TRANSACCIONES")[0][0] == 2
    assert consultar("SELECT COUNT(*) FROM VIGIA.ERRORESPROCESO WHERE PROCESO = 'Carga Datos Base'")[0][0] == 0
    assert consultar("SELECT ESTADO FROM VIGIA.uPROCESOS")[0][0] == "DRY-RUN"


def test_campos_opcionales_solo_se_validan_si_vienen(cfg, consultar):
    _insertar(cfg, "TMP_TRANSACCIONES", [_fila(CONSECUTIVO=1), _fila(CONSECUTIVO=2, TIPMON_CODIGO="XXX")])
    _validos(cfg, ("VALTIPMON", "*", 0), ("VALESTADOOPERACION", "*", 0))    # cualquier moneda/estado informado es inválido
    assert _promover(cfg) == 0
    assert [f[0] for f in consultar("SELECT CONSECUTIVO FROM VIGIA.TRANSACCIONES")] == [1]
    assert consultar("SELECT ERROR FROM VIGIA.ERRORESPROCESO WHERE PROCESO = 'Carga Datos Base'")[0][0] == \
        " Tipo de Moneda Invalido XXX"


def test_alertas_por_lista_restrictiva(cfg, consultar):
    _insertar(cfg, "DEFINICIONALERTAS", [dict(CODIGO="42", VALOR="S")])
    _insertar(cfg, "CONTROL", [dict(CODIGO="2", VALOR="vigia@ejemplo.test"), dict(CODIGO="6", VALOR="http://vigia.test")])
    _insertar(cfg, "VSDNLIST", [dict(NUMIDE="100", TIPOLISTA="PEP", CODIGOTIPOLISTA="L1", ESTADO="ACTIVO"),
                                dict(NUMIDE="200", TIPOLISTA="OFAC", CODIGOTIPOLISTA="L1", ESTADO="INACTIVO")])
    _insertar(cfg, "TIPOSLISTA", [dict(CODIGO="L1", PROCESA_CARGATRNSACC="S")])
    _insertar(cfg, "TERCEROS", [dict(TIPIDE_CODIGO="CI", NUMIDE="100", SUCURSAL_CODIGO="S1")])
    _insertar(cfg, "SUCURSALES", [dict(CODIGO="S1", RESPONSABLE="F1")])
    _insertar(cfg, "FUNCIONARIOS", [dict(CODIGO="F1", EMAIL="func@ejemplo.test", NOMBRE="Func Falso")])
    _insertar(cfg, "TMP_TRANSACCIONES", [_fila(CONSECUTIVO=1), _fila(CONSECUTIVO=2),
                                         _fila(CONSECUTIVO=3, TERCERO_NUMIDE="200")])
    assert _promover(cfg) == 0
    alertas = consultar("SELECT TERCERO_NUMIDE, CONSECUTIVO, REPORTADA, OBSERVACIONES FROM "
                        "VIGIA.RESULTADOALERTASPROGRAMADAS ORDER BY CONSECUTIVO")
    assert [(a[0], a[1], a[2]) for a in alertas] == [("100", 1, "N"), ("100", 2, "N")]   # lista inactiva no alerta
    assert alertas[0][3].endswith("COINCIDENCIA EN LISTA: PEP")
    correos = consultar("SELECT ORIGEN, DESTINO, ASUNTO, MENSAJE FROM VIGIA.DEV_CORREOS")
    assert len(correos) == 2 and correos[0][:3] == ("vigia@ejemplo.test", "func@ejemplo.test", "Correo Automatico VIGIA")
    assert "Apreciado usuario Func Falso:" in correos[0][3] and "http://vigia.test" in correos[0][3]


def test_sin_alertas_si_el_indicador_no_es_S(cfg, consultar):
    _insertar(cfg, "DEFINICIONALERTAS", [dict(CODIGO="42", VALOR="N")])
    _insertar(cfg, "VSDNLIST", [dict(NUMIDE="100", TIPOLISTA="PEP", CODIGOTIPOLISTA="L1", ESTADO="ACTIVO")])
    _insertar(cfg, "TIPOSLISTA", [dict(CODIGO="L1", PROCESA_CARGATRNSACC="S")])
    _insertar(cfg, "TMP_TRANSACCIONES", [_fila()])
    assert _promover(cfg) == 0
    assert consultar("SELECT COUNT(*) FROM VIGIA.TRANSACCIONES")[0][0] == 1
    assert consultar("SELECT COUNT(*) FROM VIGIA.RESULTADOALERTASPROGRAMADAS")[0][0] == 0


def test_lotes_pequenos_y_conservar_tmp(cfg, consultar):
    cfg.promocion("TRANSACCIONES").lote = 2
    cfg.promocion("TRANSACCIONES").borrar_promovidas = False
    _insertar(cfg, "TMP_TRANSACCIONES", [_fila(CONSECUTIVO=i) for i in range(1, 8)])
    assert _promover(cfg) == 0
    assert consultar("SELECT COUNT(*) FROM VIGIA.TRANSACCIONES")[0][0] == 7
    assert consultar("SELECT COUNT(*) FROM VIGIA.TMP_TRANSACCIONES")[0][0] == 7


def test_codigos_de_salida(cfg):
    assert promocion.ejecutar_promocion(cfg, "NOEXISTE") == 3
    with Lock(cfg.logs / "TRANSACCIONES.lock"):                    # la carga de TMP está en curso
        assert _promover(cfg) == 2


def test_error_de_servidor_no_se_confunde_con_dato(cfg, consultar):
    con = sqlite3.connect(cfg.dev_dir / "destino.db")
    con.execute("DROP TABLE TRANSACCIONES")
    con.commit()
    con.close()
    _insertar(cfg, "TMP_TRANSACCIONES", [_fila()])
    assert _promover(cfg) == 1
    assert consultar("SELECT ESTADO, ETAPA_FINAL FROM VIGIA.uPROCESOS")[0] == ("ERROR", "calcular pendientes")
