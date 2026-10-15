"""Carga de config.yaml y .env."""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
_PARAM_OK = re.compile(r"^\d+(,\d+)*$")


class ErrorConfig(Exception):
    pass


@dataclass
class Tabla:
    nombre: str
    sql: Path
    staging: str
    destino: str
    clave: list = field(default_factory=list)
    fechas: list = field(default_factory=list)
    a_texto: list = field(default_factory=list)
    ignorar: list = field(default_factory=list)
    mapeo: dict = field(default_factory=dict)
    lote: int = 5000
    tolerancia_filas: int = 0
    max_caida_pct: float = 50
    vaciado: str = "truncate"
    texto_max: dict = field(default_factory=dict)
    contar_origen: bool = True
    limpiar_al_final: bool = False


@dataclass
class Config:
    entorno: str
    raiz: Path
    dev_dir: Path
    tablas: dict
    orden: list
    parametros: dict
    logs: Path = None
    encoding: str = "cp1252"
    env: dict = field(default_factory=dict)

    def tabla(self, nombre):
        clave = nombre.upper()
        if clave not in self.tablas:
            raise ErrorConfig(f"Tabla desconocida: {nombre}. Disponibles: {', '.join(self.tablas)}")
        return self.tablas[clave]

    def sql_origen(self, tabla):
        """Lee el SQL de origen y sustituye @@PARAM@@ sin editar el archivo."""
        texto = tabla.sql.read_text(encoding="utf-8")
        # El SQL copiado del SSIS trae espacios Unicode (U+2003) que no son separadores válidos en todos los motores.
        texto = re.sub(r"[  -​　]", " ", texto)
        for nombre in set(re.findall(r"@@(\w+)@@", texto)):
            if nombre not in self.parametros:
                raise ErrorConfig(f"Parámetro @@{nombre}@@ sin valor (sección 'parametros')")
            texto = texto.replace(f"@@{nombre}@@", self.parametros[nombre])
        return texto.strip()


def leer_env(ruta):
    valores = {}
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                valores[k.strip()] = v.strip().strip('"').strip("'")
    return valores


def cargar(raiz=None, dev_dir=None, entorno=None):
    raiz = Path(raiz or RAIZ)
    env = leer_env(raiz / ".env")
    env.update({k: v for k, v in os.environ.items()})  # el entorno del proceso manda
    crudo = yaml.safe_load((raiz / "config.yaml").read_text(encoding="utf-8"))
    defaults = crudo.get("defaults", {})
    tablas = {}
    for nombre, t in (crudo.get("tablas") or {}).items():
        d = {**defaults, **t}
        tablas[nombre.upper()] = Tabla(
            nombre=nombre.upper(),
            sql=raiz / d["sql"],
            staging=str(d.get("staging", "auto")),
            destino=d["destino"],
            clave=[c.upper() for c in d.get("clave", [])],
            fechas=[c.upper() for c in d.get("fechas", [])],
            a_texto=[c.upper() for c in d.get("a_texto", [])],
            ignorar=[c.upper() for c in d.get("ignorar", [])],
            mapeo={k.upper(): v.upper() for k, v in (d.get("mapeo") or {}).items()},
            lote=int(d.get("lote", 5000)),
            tolerancia_filas=int(d.get("tolerancia_filas", 0)),
            max_caida_pct=float(d.get("max_caida_pct", 50)),
            vaciado=str(d.get("vaciado", "truncate")).lower(),
            texto_max={k.upper(): int(v) for k, v in (d.get("texto_max") or {}).items()},
            contar_origen=bool(d.get("contar_origen", True)),
            limpiar_al_final=bool(d.get("limpiar_al_final", False)),
        )
    parametros = {k: str(v) for k, v in (crudo.get("parametros") or {}).items()}
    for nombre, var in (("PROD_A", "PRODUCTOS_GRUPOA"), ("PROD_B", "PRODUCTOS_GRUPOB")):
        if env.get(var):
            parametros[nombre] = env[var]
    for k, v in parametros.items():
        if not _PARAM_OK.match(v):
            raise ErrorConfig(f"Parámetro {k} inválido: solo enteros separados por coma, sin espacios")
    return Config(
        entorno=(entorno or env.get("ETL_ENTORNO", "dev")).lower(),
        raiz=raiz,
        dev_dir=Path(dev_dir) if dev_dir else raiz / "dev_data",
        tablas=tablas,
        orden=[o.upper() for o in crudo.get("orden", list(tablas))],
        parametros=parametros,
        logs=(Path(dev_dir).parent / "logs") if dev_dir else raiz / "logs",
        encoding=defaults.get("encoding", "cp1252"),
        env=env,
    )


def cadena_odbc_origen(cfg):
    """Con ORIGEN_DRIVER se conecta sin DSN (como en NodeJS: DB_DRIVER + sistema); si no, usa el DSN."""
    e = cfg.env
    cred = f"UID={e['ORIGEN_USUARIO']};PWD={e['ORIGEN_PASSWORD']}"
    if e.get("ORIGEN_DRIVER"):
        if not e.get("ORIGEN_SISTEMA"):
            raise ErrorConfig("Con ORIGEN_DRIVER se requiere ORIGEN_SISTEMA (host o IP del iSeries)")
        extra = e.get("ORIGEN_OPCIONES", "").strip(";")
        return (f"DRIVER={{{e['ORIGEN_DRIVER'].strip('{}')}}};SYSTEM={e['ORIGEN_SISTEMA']};{cred}"
                + (f";{extra}" if extra else ""))
    return f"DSN={e['ORIGEN_DSN']};{cred}"


def cadena_odbc_destino(cfg):
    e = cfg.env
    return (f"DRIVER={{{e.get('DESTINO_DRIVER', 'ODBC Driver 17 for SQL Server')}}};"
            f"SERVER={e['DESTINO_SERVIDOR']};DATABASE={e.get('DESTINO_BASE', 'vigia')};"
            f"UID={e['DESTINO_USUARIO']};PWD={e['DESTINO_PASSWORD']}")
