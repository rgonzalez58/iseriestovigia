"""CLI de etl_vigia_py."""
import argparse
import sys

from etl import config, diagnostico, panel, runner


def main(argv=None):
    p = argparse.ArgumentParser(description="ETL VIGIA (iSeries -> SQL Server)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listar")
    sub.add_parser("conexiones")
    pn = sub.add_parser("panel", help="panel web de seguimiento (solo lectura)")
    pn.add_argument("--puerto", type=int, default=8765)
    pn.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 solo si la red es de confianza")
    pn.add_argument("--max", type=int, default=300, help="registros a leer de cada tabla")
    d = sub.add_parser("describir")
    d.add_argument("--tabla", required=True)
    for nombre in ("ejecutar", "todas"):
        e = sub.add_parser(nombre)
        if nombre == "ejecutar":
            e.add_argument("--tabla", required=True)
        e.add_argument("--dry-run", action="store_true")
        e.add_argument("--forzar", action="store_true")
        e.add_argument("--limpiar-al-final", action=argparse.BooleanOptionalAction, default=None,
                       help="vaciar la(s) tabla(s) destino al terminar bien; sin indicarlo se usa limpiar_al_final de config.yaml")
    a = p.parse_args(argv)
    cfg = config.cargar()

    if a.cmd == "listar":
        for t in cfg.tablas.values():
            print(f"{t.nombre:20s} {t.destino}")
        return 0
    if a.cmd == "conexiones":
        return diagnostico.conexiones(cfg)
    if a.cmd == "panel":
        return panel.servir(cfg, a.host, a.puerto, a.max)
    if a.cmd == "describir":
        return diagnostico.describir(cfg, a.tabla)
    if a.cmd == "ejecutar":
        return runner.ejecutar_tabla(cfg, a.tabla, a.dry_run, a.forzar, limpiar=a.limpiar_al_final)
    codigo, _ = runner.ejecutar_todas(cfg, a.dry_run, a.forzar, limpiar=a.limpiar_al_final)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
