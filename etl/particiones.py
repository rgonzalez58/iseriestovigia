"""Filtro por fecha del origen: rango de fechas de la corrida y particiones para leer con varias conexiones.

El SQL de origen incluye el marcador @@FILTRO_FECHA@@ en el WHERE de cada rama (vacío = sin efecto). Aquí se
construye el texto que lo sustituye.

- Sin parametros: toda la tabla (`fecha_defecto` vacio; opcional ayer/hoy). Reproceso: varias fechas (--desde/--hasta/--mes).
  Con rango, el filtro es `columna >= desde AND columna < hasta+1` y se parte por días (o por tramos si son muchos).
- Sin rango y con varios lectores (tabla completa): una partición mensual (diaria si hay pocos meses) entre la fecha mínima y la máxima, más una
  abierta por cada extremo y otra de fechas nulas, de modo que NINGUNA fila queda fuera.
Las filas repetidas tienen la misma fecha, así que caen en la misma partición y el UNION conserva su resultado.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

ANIO_MIN = date(1753, 1, 1)
MAX_PARTICIONES = 240


def hoy():
    return date.today()


def a_fecha(v):
    """AAAAMMDD numérico/texto, date o datetime -> date; None si no es una fecha válida (0, blancos, 99999999...)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(int(v)) if isinstance(v, (int, float, Decimal)) else str(v).strip()
    s = s.replace("-", "")[:8]
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None
    return None


def tipo_de(v):
    """Cómo se escriben los literales de fecha según el tipo de la columna: num (AAAAMMDD), txt ('AAAAMMDD'), iso."""
    if isinstance(v, type):
        if issubclass(v, (datetime, date)):
            return "iso"
        return "num" if issubclass(v, (int, float, Decimal)) else "txt"
    if isinstance(v, (datetime, date)):
        return "iso"
    if isinstance(v, (int, float, Decimal)):
        return "num"
    return "txt"


def literal(tipo, d):
    if tipo == "num":
        return d.strftime("%Y%m%d")
    if tipo == "txt":
        return f"'{d.strftime('%Y%m%d')}'"
    return f"'{d.isoformat()}'"


def _mes_siguiente(d, n=1):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


# ---------------------------------------------------------------- rango de la corrida
def resolver_rango(tabla, fechas=None):
    """(desde, hasta) inclusive que aplica a la tabla, o None = sin filtro de fecha.
    `fechas`: None (usar fecha_defecto), "todas" (sin filtro) o (desde, hasta) dados por la línea de comandos."""
    if not tabla.particion or fechas == "todas":
        return None
    if fechas:
        return fechas
    if tabla.fecha_defecto == "ayer":
        d = hoy() - timedelta(days=1)
        return (d, d)
    if tabla.fecha_defecto == "hoy":
        return (hoy(), hoy())
    return None


def texto_rango(rango):
    if not rango:
        return "todas las fechas"
    return rango[0].isoformat() if rango[0] == rango[1] else f"{rango[0].isoformat()} a {rango[1].isoformat()}"


def filtros_rango(columna, tipo, desde, hasta, partir=True, sub=None, lectores=1, mod=None):
    """Un filtro por día entre desde y hasta (inclusive); si son más de MAX_PARTICIONES días, por tramos.
    Con partir=False (un solo lector) es un único filtro para todo el rango.
    Si hay menos particiones de fecha que 2 × lectores (p. ej. una sola fecha) y se indica la columna `sub`, cada
    partición se reparte además en `lectores` trozos por el resto de `sub` (mod(sub, lectores) = 0..lectores-1) más
    un trozo con lo que ningún resto cubre (nulos, fracciones), de modo que ninguna fila queda fuera."""
    dias = (hasta - desde).days + 1
    paso = max(1, -(-dias // MAX_PARTICIONES)) if partir else dias
    filtros, lo = [], desde
    while lo <= hasta:
        hi = min(lo + timedelta(days=paso), hasta + timedelta(days=1))
        filtros.append(f" and {columna} >= {literal(tipo, lo)} and {columna} < {literal(tipo, hi)}")
        lo = hi
    if partir and sub and mod and lectores > 1 and len(filtros) < 2 * lectores:
        trozos = []
        restos = ", ".join(str(i) for i in range(lectores))
        for f in filtros:
            trozos += [f + f" and {mod(sub, lectores)} = {i}" for i in range(lectores)]
            trozos.append(f + f" and ({sub} is null or {mod(sub, lectores)} not in ({restos}))")
        return trozos
    return filtros


# ---------------------------------------------------------------- tabla completa (sin rango)
def construir_filtros(columna, tipo, dmin, dmax, lectores=1):
    """Lista de textos para @@FILTRO_FECHA@@ (uno por partición) que cubren TODAS las filas.
    Mensuales; si hay menos de 2 × lectores meses (p. ej. la tabla solo trae un mes) se parte por días, para que
    todos los lectores tengan trabajo hasta el final."""
    lit = lambda d: literal(tipo, d)  # noqa: E731
    inicio = max(dmin, ANIO_MIN)
    inicio = date(inicio.year, inicio.month, 1)
    fin = _mes_siguiente(date(dmax.year, dmax.month, 1))
    meses = (fin.year - inicio.year) * 12 + (fin.month - inicio.month)
    if lectores > 1 and meses < 2 * lectores:
        d0, d1 = max(dmin, ANIO_MIN), dmax + timedelta(days=1)
        paso = max(1, -(-(d1 - d0).days // MAX_PARTICIONES))
        filtros = [f" and {columna} < {lit(d0)}"]
        lo = d0
        while lo < d1:
            hi = min(lo + timedelta(days=paso), d1)
            filtros.append(f" and {columna} >= {lit(lo)} and {columna} < {lit(hi)}")
            lo = hi
        filtros.append(f" and {columna} >= {lit(d1)}")
        filtros.append(f" and {columna} is null")
        return filtros
    paso = max(1, -(-meses // MAX_PARTICIONES))
    filtros = [f" and {columna} < {lit(inicio)}"]                               # fechas anteriores o inválidas
    lo = inicio
    while lo < fin:
        hi = min(_mes_siguiente(lo, paso), fin)
        filtros.append(f" and {columna} >= {lit(lo)} and {columna} < {lit(hi)}")
        lo = hi
    filtros.append(f" and {columna} >= {lit(fin)}")                             # posteriores o inválidas
    filtros.append(f" and {columna} is null")
    return filtros


def detectar(origen, particion):
    """(tipo, dmin, dmax) de la columna de fecha de la tabla de origen. Una consulta MIN/MAX (y otra si el mínimo
    es inválido, p. ej. 0)."""
    tabla, col = particion["tabla"], particion["columna"]
    cur = origen.cursor()
    cur.execute(f"SELECT MIN({col}), MAX({col}) FROM {tabla}")
    vmin, vmax = cur.fetchone()
    tipo = tipo_de(vmin if vmin is not None else vmax)
    dmin, dmax = a_fecha(vmin), a_fecha(vmax)
    if dmin is None and vmax is not None:
        cur.execute(f"SELECT MIN({col}) FROM {tabla} WHERE {col} >= {literal(tipo, ANIO_MIN)}")
        dmin = a_fecha(cur.fetchone()[0])
    if dmax is None:
        dmax = hoy() + timedelta(days=366)
    if dmin is None:
        dmin = dmax
    if dmin > dmax:
        dmin = dmax
    return tipo, dmin, dmax


def tipo_columna(origen, particion):
    """Tipo de la columna de fecha sin leer filas ni recorrer la tabla (solo la descripción de la consulta)."""
    cur = origen.cursor()
    cur.execute(f"SELECT {particion['columna']} FROM {particion['tabla']} WHERE 1 = 0")
    return tipo_de(cur.description[0][1])


def filtros_para(origen, tabla, rango=None, mod=None):
    """Filtros de lectura según la configuración: por rango de fechas (días), por partición mensual de la tabla
    completa (varios lectores) o ninguno ([""])."""
    p = tabla.particion
    if rango:
        return filtros_rango(p["columna"], tipo_columna(origen, p), rango[0], rango[1], partir=tabla.lectores > 1,
                             sub=p.get("subcolumna"), lectores=tabla.lectores, mod=mod)
    if tabla.lectores > 1:
        tipo, dmin, dmax = detectar(origen, p)
        return construir_filtros(p["columna"], tipo, dmin, dmax, tabla.lectores)
    return [""]
