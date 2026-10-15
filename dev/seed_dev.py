"""Genera dev_data/ (iSeries y SQL Server simulados con SQLite). Solo datos FALSOS, con casos borde."""
import argparse
import random
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

TABLAS_DDL = ["terceros", "datosadicionalestercero", "productostercero", "tipstran", "transacciones"]

ORIGEN_DDL = """
CREATE TABLE psa065 (psa065td TEXT, psa065do TEXT, psa065nm TEXT, psa065ap TEXT, psa065ci TEXT, psa065ti TEXT,
  psa065pf TEXT, psa065oc TEXT, psa065ac REAL, psa065ps REAL, psa065pt REAL, psa065in REAL, psa065eg REAL,
  psa065ct REAL, psa065ri TEXT, psa065vi TEXT, psa065di TEXT, psa065tf TEXT, psa065em TEXT, psa065fi INTEGER,
  psa065fa INTEGER, psa065ej TEXT, psa065ae TEXT, psa065pe TEXT, psa065tp TEXT, psa065sg TEXT, psa065su,
  psa065fn INTEGER, psa065ea TEXT, psa065sx TEXT, psa065es TEXT, psa065an TEXT, psa065nj TEXT, psa065na TEXT,
  psa065pn TEXT, psa065ec TEXT, psa065nl TEXT, psa065dl TEXT, psa065pa TEXT);
CREATE TABLE psa002 (psa002pa TEXT, psa002to TEXT, psa002do TEXT);
CREATE TABLE psa066 (psa066td TEXT, psa066do TEXT, psa066mn TEXT, psa066mo INTEGER, psa066op TEXT, psa066so TEXT,
  psa066ct TEXT, psa066su, psa066fa TEXT, psa066cu REAL, psa066sd REAL, psa066st TEXT, psa066ti TEXT);
CREATE TABLE psa067 (psa067td TEXT, psa067do TEXT, psa067mn TEXT, psa067mo INTEGER, psa067op TEXT, psa067so TEXT,
  psa067nc TEXT, psa067mc TEXT, psa067tx TEXT, psa067fc INTEGER, psa067co REAL, psa067ci, psa067su, psa067va REAL,
  psa067me REAL, psa067ca TEXT, psa067pc TEXT, psa067rl TEXT, psa067or TEXT, psa067os TEXT);
CREATE TABLE psa068 (psa068co TEXT, psa068ds TEXT, psa068na TEXT, psa068ef TEXT, psa068in TEXT, psa068im TEXT,
  psa068ap TEXT, psa068am TEXT, psa068st TEXT);
"""

ERRORES_DDL = """CREATE TABLE VIGIA.ERRORESPROCESO (
  PROCESO TEXT NOT NULL, TAREA TEXT NOT NULL, ERROR TEXT NOT NULL, DATOSERROR TEXT NOT NULL,
  FECADI TEXT NOT NULL, USUADI TEXT NOT NULL)"""

PROCESOS_DDL = """CREATE TABLE VIGIA.PROCESOS (NOMBRE TEXT NOT NULL, FECHA TEXT NOT NULL, RESULTADO TEXT)"""

UPROCESOS_DDL = """CREATE TABLE VIGIA.uPROCESOS (
  ID INTEGER PRIMARY KEY AUTOINCREMENT, CORRIDA TEXT NOT NULL, PROCESO TEXT NOT NULL, TIPO TEXT NOT NULL,
  ID_PADRE INTEGER, TABLA_DESTINO TEXT, ESTADO TEXT NOT NULL, CODIGO_SALIDA INTEGER, ETAPA_FINAL TEXT,
  INICIO TEXT NOT NULL, FIN TEXT, DURACION_MS INTEGER, MS_CONTAR_ORIGEN INTEGER, MS_EJECUTAR_ORIGEN INTEGER,
  MS_ESTRUCTURA_DESTINO INTEGER, MS_CONTEO_DESTINO INTEGER, MS_CARGAR_STAGING INTEGER, MS_VALIDAR_STAGING INTEGER,
  MS_TRANSACCION_DESTINO INTEGER, FILAS_ORIGEN INTEGER, FILAS_CARGADAS INTEGER, FILAS_DESTINO_ANTES INTEGER,
  DRY_RUN INTEGER NOT NULL DEFAULT 0, FORZAR INTEGER NOT NULL DEFAULT 0, USUARIO TEXT NOT NULL, EQUIPO TEXT,
  MENSAJE TEXT)"""


def _ddl_destino(con):
    for t in TABLAS_DDL:
        for prefijo in ("tmp", "stg"):
            if not (RAIZ / "sql" / "destino" / f"{prefijo}_{t}.sql").exists():
                continue          # el staging es automático (temporal): solo hace falta el destino
            con.execute((RAIZ / "sql" / "destino" / f"{prefijo}_{t}.sql").read_text(encoding="utf-8"))
    con.execute(ERRORES_DDL)
    con.execute(PROCESOS_DDL)
    con.execute(UPROCESOS_DDL)


def crear(dev_dir, filas=200, semilla=7):
    dev_dir = Path(dev_dir)
    dev_dir.mkdir(parents=True, exist_ok=True)
    for f in ("origen.db", "destino.db"):
        (dev_dir / f).unlink(missing_ok=True)
    rnd = random.Random(semilla)

    o = sqlite3.connect(dev_dir / "origen.db")
    o.executescript(ORIGEN_DDL)
    tipos = ["CI", "RUC", "PAS"]
    for i in range(filas):
        td, doc = rnd.choice(tipos), f"{1000000 + i}"
        # Casos borde: ceros -> NULL, '' -> NULL, sucursal vacía (se convierte a 1), fecha 0, inactivos.
        borde = i % 10
        o.execute("INSERT INTO psa065 VALUES (" + ",".join("?" * 39) + ")", (
            td, doc, f"  NOMBRE{i} ", f"APELLIDO{i}  ", "1", "F",
            "0" if borde == 1 else "12", "0" if borde == 2 else "7", 1000.5, 200.0, 800.5, 300.0, 100.0,
            5000.0, "A", "" if borde == 3 else "T1", "Calle falsa 123", "" if borde == 4 else "099000000",
            "" if borde == 4 else f"u{i}@ejemplo.test", 20200101 + (i % 28), 0 if borde == 5 else 20230101,
            "0" if borde == 6 else "55", "0" if borde == 7 else "9999", "" if borde == 8 else "S", "",
            "SEG1", "" if borde == 9 else (i % 5) + 1, 19800101 + (i % 28), "N", "" if borde == 0 else "M",
            "I" if i % 25 == 24 else "A", "S", "0", "0", "0", "", "Empresa X", "Av. Falsa 1", "AS"))
        if i % 3 == 0:
            o.execute("INSERT INTO psa002 VALUES (?,?,?)", ("AS", td, doc))
        for mo, mn in ((31, "6900"), (35, "0"), (500, "0")):  # grupo A, grupo B, resto
            if (i + mo) % 4 == 0:
                o.execute("INSERT INTO psa066 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    td, f" {doc} ", mn, mo, f"{i:04d}", f"{mo:03d}", "CT", (i % 5) + 1,
                    "00000000" if borde == 0 else "20210315", 1000.0, 250.0, "V", "T"))
    # Transacciones: una por tercero en cada rama (grupo A=31, resto=500, grupo B=35). Casos borde: fecha '0'
    # (la columna FECHA del destino es NOT NULL, no se inserta), valores con decimales y espacios de relleno.
    for i in range(filas):
        for mo, mn in ((31, "6900"), (500, "0"), (35, "0")):
            if (i + mo) % 3 == 0:
                o.execute("INSERT INTO psa067 VALUES (" + ",".join("?" * 20) + ")", (
                    tipos[i % 3], f"{1000000 + i}", mn, mo, f"{i:04d}  ", f"{mo:03d}", "NC1", "MC", "TX1",
                    20230101 + (i % 28), 1000 + i, "GEO1", (i % 5) + 1, 1234.56 + i, 0.0, "7", "PY", "R1", "O1", "S1"))
    for i in range(12):
        o.execute("INSERT INTO psa068 VALUES (?,?,?,?,?,?,?,?,?)",
                  (f"{i:03d}", f"TIPO {i}", "D" if i % 2 else "C", "S", "S", "N", "S", "N", "I" if i == 11 else "A"))
    o.commit()
    o.close()

    d = sqlite3.connect(":memory:")
    d.execute("ATTACH DATABASE ? AS VIGIA", (str(dev_dir / "destino.db"),))
    _ddl_destino(d)
    d.commit()
    d.close()
    return dev_dir


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--filas", type=int, default=5000)
    ap.add_argument("--dir", default=str(RAIZ / "dev_data"))
    a = ap.parse_args()
    print("creado:", crear(a.dir, a.filas))
