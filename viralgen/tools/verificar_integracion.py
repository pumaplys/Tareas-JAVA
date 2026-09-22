"""Comprueba que la integracion obligatoria se EJECUTO de verdad.

Un trabajo de CI puede quedar verde sin haber ejercitado nada: basta con que
las pruebas obligatorias no se recojan, se salten o esten marcadas `xfail`.
Ninguna de esas tres cosas acredita un render.

Por que sobre JUnit XML y no sobre el texto del resumen:

* el codigo de salida 5 solo cubre la coleccion VACIA, no los saltos;
* `-rs` imprime "SKIPPED", pero una seleccion entera en `xfail` sale con
  codigo 0 y NO imprime esa palabra —comprobado—, asi que un guard basado en
  `grep SKIPPED` la aprobaria;
* el texto del resumen es formato de presentacion, no contrato: cambia entre
  versiones y puede venir coloreado.

En el XML, pytest registra tanto un salto como un `xfail` en el atributo
`skipped` del `testsuite` (el `xfail` con `type="pytest.xfail"`), asi que
exigir `skipped == 0` cubre las dos formas de no ejecutar.

Uso:

    python tools/verificar_integracion.py informe.xml --minimo 40
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Resumen:
    """Recuento agregado de una ejecucion de pytest."""

    tests: int
    failures: int
    errors: int
    skipped: int

    @property
    def ejecutadas(self) -> int:
        """Pruebas que realmente corrieron hasta un veredicto."""
        return self.tests - self.skipped

    def describe(self) -> str:
        return (
            f"recogidas={self.tests} ejecutadas={self.ejecutadas} "
            f"saltadas_o_xfail={self.skipped} fallidas={self.failures} "
            f"errores={self.errors}"
        )


class ReporteInvalido(ValueError):
    """El XML no se puede leer o no tiene la forma esperada."""


def leer_resumen(path: Path) -> Resumen:
    """Suma los `testsuite` del informe. Acepta uno o varios."""
    try:
        arbol = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        raise ReporteInvalido(f"no se puede leer {path}: {exc}") from exc

    raiz = arbol.getroot()
    suites = (
        [raiz] if raiz.tag == "testsuite" else list(raiz.iter("testsuite"))
    )
    if not suites:
        raise ReporteInvalido(f"{path} no contiene ningun <testsuite>")

    def entero(suite: ET.Element, nombre: str) -> int:
        crudo = suite.get(nombre, "0")
        try:
            return int(crudo)
        except ValueError as exc:
            raise ReporteInvalido(
                f"atributo {nombre}={crudo!r} no es un entero"
            ) from exc

    return Resumen(
        tests=sum(entero(s, "tests") for s in suites),
        failures=sum(entero(s, "failures") for s in suites),
        errors=sum(entero(s, "errors") for s in suites),
        skipped=sum(entero(s, "skipped") for s in suites),
    )


def motivos_de_rechazo(resumen: Resumen, *, minimo: int) -> list[str]:
    """Devuelve por que NO se acredita la integracion. Vacio = acreditada."""
    motivos: list[str] = []
    if resumen.tests == 0:
        motivos.append(
            "no se recogio ninguna prueba: la marca obligatoria no selecciona nada"
        )
    if resumen.skipped:
        motivos.append(
            f"{resumen.skipped} prueba(s) saltadas o en xfail: saltarse la "
            "integracion no acredita ningun render"
        )
    if resumen.failures or resumen.errors:
        motivos.append(
            f"{resumen.failures} fallida(s) y {resumen.errors} con error"
        )
    if resumen.ejecutadas < minimo:
        motivos.append(
            f"solo {resumen.ejecutadas} prueba(s) llegaron a ejecutarse y se "
            f"exigen al menos {minimo}"
        )
    return motivos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rechaza una integracion obligatoria vacia, saltada o incompleta."
        )
    )
    parser.add_argument("report", type=Path, help="Informe JUnit XML de pytest.")
    parser.add_argument(
        "--minimo",
        type=int,
        default=1,
        help=(
            "Pruebas que deben EJECUTARSE como minimo. Es un suelo contra un "
            "derrumbe silencioso de la seleccion, no el recuento exacto."
        ),
    )
    args = parser.parse_args(argv)

    try:
        resumen = leer_resumen(args.report)
    except ReporteInvalido as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(resumen.describe())
    motivos = motivos_de_rechazo(resumen, minimo=args.minimo)
    if motivos:
        print("ERROR: la integracion obligatoria NO quedo acreditada:", file=sys.stderr)
        for motivo in motivos:
            print(f"  - {motivo}", file=sys.stderr)
        return 1
    print(
        f"integracion acreditada: {resumen.ejecutadas} prueba(s) ejecutadas "
        "hasta un veredicto, sin saltos ni xfail"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
