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
    escritura: str = "parametros"
    espera_bloqueo_seg: int = 120
    hilos_escritura: int = 1
    particion: dict = field(default_factory=dict)   # {tabla, columna}
    lectores: int = 1
    fecha_defecto: str = ""          # ayer | hoy | "" (sin filtro de fecha por defecto)
    permitir_origen_vacio: bool = False
    tablock: bool = True
    reconstruir_indices: bool = False


@dataclass
class Promocion:
    """Paso TMP_* -> tabla definitiva (grupo de programas `promover`)."""
    nombre: str
    origen: str
    destino: str
    lote: int = 50000
    alertas: bool = True
    borrar_promovidas: bool = True


OPCIONES_PROMOCION = {"origen", "destino", "lote", "alertas", "borrar_promovidas"}


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
    promociones: dict = field(default_factory=dict)

    def promocion(self, nombre):
        clave = nombre.upper()
        if clave not in self.promociones:
            raise ErrorConfig(f"Promoción desconocida: {nombre}. Disponibles: {', '.join(self.promociones) or 'ninguna'}")
        return self.promociones[clave]

    def tabla(self, nombre):
        clave = nombre.upper()
        if clave not in self.tablas:
            raise ErrorConfig(f"Tabla desconocida: {nombre}. Disponibles: {', '.join(self.tablas)}")
        return self.tablas[clave]

    def tiene_filtro_fecha(self, tabla):
        return "@@FILTRO_FECHA@@" in tabla.sql.read_text(encoding="utf-8")

    def sql_origen(self, tabla, filtro=""):
        """Lee el SQL de origen y sustituye @@PARAM@@ sin editar el archivo. @@FILTRO_FECHA@@ (opcional) se
        reemplaza por `filtro` (vacío = sin efecto), que usa la lectura por particiones de fecha."""
        texto = tabla.sql.read_text(encoding="utf-8").replace("@@FILTRO_FECHA@@", filtro)
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


OPCIONES = {"sql", "staging", "destino", "clave", "fechas", "a_texto", "ignorar", "mapeo", "lote", "tolerancia_filas",
            "max_caida_pct", "vaciado", "texto_max", "contar_origen", "limpiar_al_final", "encoding", "escritura", "espera_bloqueo_seg", "hilos_escritura",
            "particion", "lectores", "fecha_defecto", "permitir_origen_vacio", "tablock", "reconstruir_indices"}
SECCIONES = {"defaults", "parametros", "orden", "tablas", "promociones"}


def _validar_claves(crudo):
    """Una clave mal escrita (p. ej. limpiar_al_fin) se ignoraría en silencio: se rechaza con un mensaje claro."""
    if not isinstance(crudo, dict):
        raise ErrorConfig("config.yaml debe ser un mapa YAML (clave: valor)")
    raras = [k for k in crudo if k not in SECCIONES]
    if raras:
        raise ErrorConfig(f"Secciones desconocidas en la configuración: {raras} (válidas: {sorted(SECCIONES)})")
    for donde, bloque in [("defaults", crudo.get("defaults") or {})] + [
            (f"tablas.{n}", t or {}) for n, t in (crudo.get("tablas") or {}).items()]:
        malas = [k for k in bloque if k not in OPCIONES]
        if malas:
            raise ErrorConfig(f"Opciones desconocidas en '{donde}': {malas} (válidas: {sorted(OPCIONES)})")


def _fusionar(base, extra):
    """Mezcla recursiva: los mapas se combinan clave por clave; listas y valores simples se reemplazan."""
    if isinstance(base, dict) and isinstance(extra, dict):
        salida = dict(base)
        for k, v in extra.items():
            salida[k] = _fusionar(base[k], v) if k in base else v
        return salida
    return extra


def cargar(raiz=None, dev_dir=None, entorno=None):
    raiz = Path(raiz or RAIZ)
    env = leer_env(raiz / ".env")
    env.update({k: v for k, v in os.environ.items()})  # el entorno del proceso manda
    crudo = yaml.safe_load((raiz / "config.yaml").read_text(encoding="utf-8"))
    local = raiz / "config.local.yaml"
    if local.exists():
        # Ajustes propios del equipo (orden, parámetros, opciones por tabla): el instalador nunca los pisa.
        try:
            extra = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ErrorConfig(f"config.local.yaml tiene un error de formato: {e}") from e
        if not isinstance(extra, dict):
            raise ErrorConfig("config.local.yaml debe ser un mapa YAML (clave: valor)")
        crudo = _fusionar(crudo, extra)
    _validar_claves(crudo)
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
            escritura=str(d.get("escritura", "parametros")).lower(),
            espera_bloqueo_seg=int(d.get("espera_bloqueo_seg", 120)),
            hilos_escritura=int(d.get("hilos_escritura", 1)),
            particion={k: str(v) for k, v in (d.get("particion") or {}).items()},
            lectores=int(d.get("lectores", 1)),
            fecha_defecto=str(d.get("fecha_defecto") or "").lower().replace("ninguna", ""),
            permitir_origen_vacio=bool(d.get("permitir_origen_vacio", False)),
            tablock=bool(d.get("tablock", True)),
            reconstruir_indices=bool(d.get("reconstruir_indices", False)),
        )
    for t in tablas.values():
        if not 1 <= t.hilos_escritura <= 16:
            raise ErrorConfig(f"{t.nombre}: hilos_escritura debe estar entre 1 y 16 (valor: {t.hilos_escritura})")
        if not 1 <= t.lectores <= 16:
            raise ErrorConfig(f"{t.nombre}: lectores debe estar entre 1 y 16 (valor: {t.lectores})")
        if t.fecha_defecto not in ("", "ayer", "hoy"):
            raise ErrorConfig(f"{t.nombre}: fecha_defecto debe ser ayer, hoy o ninguna (valor: {t.fecha_defecto})")
        if (t.lectores > 1 or t.fecha_defecto) and not (t.particion.get("tabla") and t.particion.get("columna")):
            raise ErrorConfig(f"{t.nombre}: lectores/fecha_defecto requieren 'particion: {{tabla: ..., columna: ...}}'")
        for v in t.particion.values():
            if not re.fullmatch(r"[A-Za-z0-9_.]+", v):
                raise ErrorConfig(f"{t.nombre}: particion solo admite nombres (letras, números, _ y .)")
    promociones = {}
    for nombre, p in (crudo.get("promociones") or {}).items():
        p = p or {}
        malas = [k for k in p if k not in OPCIONES_PROMOCION]
        if malas or not p.get("origen") or not p.get("destino"):
            raise ErrorConfig(f"promociones.{nombre}: requiere origen y destino; opciones válidas: {sorted(OPCIONES_PROMOCION)}"
                              + (f" (desconocidas: {malas})" if malas else ""))
        promociones[nombre.upper()] = Promocion(
            nombre=nombre.upper(), origen=p["origen"], destino=p["destino"], lote=int(p.get("lote", 50000)),
            alertas=bool(p.get("alertas", True)), borrar_promovidas=bool(p.get("borrar_promovidas", True)))
        if promociones[nombre.upper()].lote < 1:
            raise ErrorConfig(f"promociones.{nombre}: lote debe ser mayor que 0")
    parametros = {k: str(v) for k, v in (crudo.get("parametros") or {}).items()}
    for nombre, var in (("PROD_A", "PRODUCTOS_GRUPOA"), ("PROD_B", "PRODUCTOS_GRUPOB")):
        if env.get(var):
            parametros[nombre] = env[var]
    for k, v in parametros.items():
        if not _PARAM_OK.match(v):
            raise ErrorConfig(f"Parámetro {k} inválido: solo enteros separados por coma, sin espacios")
    orden = [o.upper() for o in crudo.get("orden", list(tablas))]
    desconocidas = [o for o in orden if o not in tablas]
    if desconocidas:
        raise ErrorConfig(f"'orden' menciona tablas que no existen en 'tablas': {desconocidas}")
    if len(set(orden)) != len(orden):
        raise ErrorConfig("'orden' repite alguna tabla")
    return Config(
        entorno=(entorno or env.get("ETL_ENTORNO", "dev")).lower(),
        raiz=raiz,
        dev_dir=Path(dev_dir) if dev_dir else raiz / "dev_data",
        tablas=tablas,
        orden=orden,
        parametros=parametros,
        logs=(Path(dev_dir).parent / "logs") if dev_dir else raiz / "logs",
        encoding=defaults.get("encoding", "cp1252"),
        env=env,
        promociones=promociones,
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
