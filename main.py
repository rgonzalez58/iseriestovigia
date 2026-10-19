"""CLI de etl_vigia_py."""
import argparse
import sys
from datetime import date, datetime

from etl import config, diagnostico, panel, promocion, runner


def _agregar_fechas(e):
    g = e.add_argument_group("fechas (tablas con filtro de fecha, p. ej. TRANSACCIONES; sin indicar nada se usa fecha_defecto)")
    g.add_argument("--fecha", type=_fecha, help="una sola fecha AAAA-MM-DD (corrida diaria o reproceso de un día)")
    g.add_argument("--desde", type=_fecha, help="reproceso: primera fecha AAAA-MM-DD (inclusive)")
    g.add_argument("--hasta", type=_fecha, help="reproceso: última fecha AAAA-MM-DD (inclusive)")
    g.add_argument("--mes", type=_mes, help="reproceso de un mes completo AAAA-MM")
    g.add_argument("--todas-las-fechas", action="store_true", help="sin filtro de fecha (toda la tabla de origen)")


def _fecha(texto):
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"fecha inválida '{texto}' (use AAAA-MM-DD)")


def _mes(texto):
    try:
        d = datetime.strptime(texto, "%Y-%m").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"mes inválido '{texto}' (use AAAA-MM)")
    fin = date(d.year + d.month // 12, d.month % 12 + 1, 1)
    return (d, date.fromordinal(fin.toordinal() - 1))


def _rango_fechas(a, parser):
    """None = usar fecha_defecto de cada tabla; "todas" = sin filtro; (desde, hasta) inclusive."""
    dadas = [bool(a.fecha), bool(a.desde or a.hasta), bool(a.mes), bool(a.todas_las_fechas)]
    if sum(dadas) > 1:
        parser.error("use solo una forma: --fecha, --desde/--hasta, --mes o --todas-las-fechas")
    if a.todas_las_fechas:
        return "todas"
    if a.fecha:
        return (a.fecha, a.fecha)
    if a.mes:
        return a.mes
    if a.desde or a.hasta:
        if not (a.desde and a.hasta):
            parser.error("--desde y --hasta van juntos")
        if a.desde > a.hasta:
            parser.error("--desde no puede ser posterior a --hasta")
        return (a.desde, a.hasta)
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description="ETL VIGIA (iSeries -> SQL Server)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listar")
    sub.add_parser("conexiones")
    pn = sub.add_parser("panel", help="panel web de seguimiento (solo lectura)")
    pn.add_argument("--puerto", type=int, default=8765)
    pn.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 solo si la red es de confianza")
    pn.add_argument("--max", type=int, default=300, help="registros a leer de cada tabla")
    m = sub.add_parser("medir", help="mide dónde se va el tiempo con una muestra (no modifica tablas reales)")
    m.add_argument("--tabla", required=True)
    m.add_argument("--filas", type=int, default=20000)
    m.add_argument("--lectores", type=int, default=0, help="prueba además la lectura del iSeries por particiones de fecha con N conexiones")
    m.add_argument("--hilos", type=int, default=0, help="prueba además la escritura con N conexiones en paralelo")
    m.add_argument("--max-seg", type=int, default=60, help="tope de segundos por prueba de escritura")
    c = sub.add_parser("comparar", help="compara el SQL de origen de una tabla con un SQL alterno (solo lectura)")
    c.add_argument("--tabla", required=True)
    c.add_argument("--sql", required=True, help="ruta del SQL alterno (p. ej. sql/origen/transacciones_optimizado.sql)")
    _agregar_fechas(c)
    d = sub.add_parser("describir")
    d.add_argument("--tabla", required=True)
    pm = sub.add_parser("promover", help="copia una tabla TMP_* a la definitiva validando (reemplaza PR_CARGA*)")
    pm.add_argument("--tabla", required=True)
    pm.add_argument("--dry-run", action="store_true", help="solo calcula y valida; no escribe en tablas reales")
    for nombre in ("ejecutar", "todas"):
        e = sub.add_parser(nombre)
        if nombre == "ejecutar":
            e.add_argument("--tabla", required=True)
        e.add_argument("--dry-run", action="store_true")
        e.add_argument("--forzar", action="store_true")
        _agregar_fechas(e)
        e.add_argument("--limpiar-al-final", action=argparse.BooleanOptionalAction, default=None,
                       help="vaciar la(s) tabla(s) destino al terminar bien; sin indicarlo se usa limpiar_al_final de config.yaml")
    a = p.parse_args(argv)
    try:
        cfg = config.cargar()
    except config.ErrorConfig as e:
        print(f"Error de configuración: {e}")
        return 3

    if a.cmd == "listar":
        if (cfg.raiz / "config.local.yaml").exists():
            print("config.local.yaml aplicado sobre config.yaml")
        # En el orden en que las ejecuta 'todas' (config.yaml: orden); luego las que no están en 'orden'.
        for i, nombre in enumerate(cfg.orden, 1):
            t = cfg.tablas[nombre]
            print(f"{i:2d}. {t.nombre:20s} {t.destino}")
        for t in cfg.tablas.values():
            if t.nombre not in cfg.orden:
                print(f" -  {t.nombre:20s} {t.destino}   (fuera de 'orden': no la ejecuta 'todas')")
        for p in cfg.promociones.values():
            print(f" >  {p.nombre:20s} {p.origen} -> {p.destino}   (promover --tabla {p.nombre})")
        return 0
    if a.cmd == "promover":
        return promocion.ejecutar_promocion(cfg, a.tabla, a.dry_run)
    if a.cmd == "conexiones":
        return diagnostico.conexiones(cfg)
    if a.cmd == "panel":
        return panel.servir(cfg, a.host, a.puerto, a.max)
    if a.cmd == "comparar":
        return diagnostico.comparar(cfg, a.tabla, a.sql, _rango_fechas(a, p))
    if a.cmd == "medir":
        return diagnostico.medir(cfg, a.tabla, a.filas, a.max_seg, a.hilos, a.lectores)
    if a.cmd == "describir":
        return diagnostico.describir(cfg, a.tabla)
    if a.cmd == "ejecutar":
        return runner.ejecutar_tabla(cfg, a.tabla, a.dry_run, a.forzar, limpiar=a.limpiar_al_final,
                                     fechas=_rango_fechas(a, p))
    codigo, _ = runner.ejecutar_todas(cfg, a.dry_run, a.forzar, limpiar=a.limpiar_al_final, fechas=_rango_fechas(a, p))
    return codigo


if __name__ == "__main__":
    sys.exit(main())
