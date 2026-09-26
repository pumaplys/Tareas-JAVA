#!/usr/bin/env python3
"""Hace, para la demostracion, lo que haria una persona editando el plan.

`publish plan` deja el borrador con requisitos pendientes: el guion de ejemplo
no trae texto para YouTube y no decide la audiencia infantil. Eso no lo
resuelve el programa por su cuenta, y este script tampoco pretende ser la
decision: existe solo para que la demostracion sea reproducible sin abrir un
editor.

Uso:
    python examples/publicacion_simulada/completar_plan_demo.py <publication_plan.json>

Despues del cambio, `publish approve` recalcula los requisitos y la revision.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TITULO_YT = "Los dientes de una cremallera encajan por parejas"
TEXTO_YT = (
    "Por que los dientes de una cremallera encajan por parejas y que detalle "
    "casi nadie mira. Fuentes revisadas en el catalogo del proyecto."
)


def main(ruta: Path) -> int:
    plan = json.loads(ruta.read_text(encoding="utf-8"))
    for destino in plan["destinations"]:
        metadata = destino["metadata"]
        if destino["platform"] == "youtube_shorts":
            # El guion no traia plan editorial para YouTube: lo escribe la
            # persona que publica, no un modelo.
            metadata["title"] = TITULO_YT
            metadata["description"] = TEXTO_YT
        # Decisiones que el guion dejaba sin resolver (made_for_kids=null).
        metadata["audience"] = "not_made_for_kids"
        metadata["synthetic_disclosure"] = "contains_realistic_synthetic_media"
    ruta.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Plan completado: {ruta}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1])))
