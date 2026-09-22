"""El guard que impide que una integracion saltada se declare verde.

Las tres formas de NO ejecutar la integracion obligatoria —seleccion vacia,
seleccion saltada y seleccion en `xfail`— se comprueban aqui sobre informes
JUnit XML REALES, generados por pytest en un subproceso: no se escriben XML a
mano, porque lo que hay que fijar es como pytest representa cada caso, no como
creo yo que lo representa.

No se renderiza nada: estas pruebas son instantaneas.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from verificar_integracion import (  # noqa: E402
    ReporteInvalido,
    Resumen,
    leer_resumen,
    main,
    motivos_de_rechazo,
)

PYTEST_INI = """[pytest]
markers = ffmpeg: integracion local real
"""


def _proyecto(tmp_path: Path, cuerpo: str) -> Path:
    """Crea un proyecto pytest minimo y devuelve su directorio."""
    (tmp_path / "pytest.ini").write_text(PYTEST_INI, encoding="utf-8")
    (tmp_path / "test_caso.py").write_text(cuerpo, encoding="utf-8")
    return tmp_path


def _informe(directorio: Path) -> tuple[Path, int]:
    """Ejecuta `pytest -m ffmpeg` de verdad y devuelve (xml, codigo)."""
    xml = directorio / "informe.xml"
    proceso = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-m", "ffmpeg",
            "-p", "no:cacheprovider", "-q", f"--junitxml={xml}",
        ],
        cwd=directorio,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return xml, proceso.returncode


# ---------------------------------------------------------------------------
# Las tres formas de no ejecutar
# ---------------------------------------------------------------------------


def test_una_seleccion_vacia_se_rechaza(tmp_path: Path) -> None:
    """Sin pruebas marcadas, pytest sale con 5 y no acredita nada."""
    directorio = _proyecto(
        tmp_path,
        "def test_sin_marca():\n    assert True\n",
    )
    xml, codigo = _informe(directorio)
    assert codigo == 5, "pytest deberia avisar de que no recogio nada"
    resumen = leer_resumen(xml)
    assert resumen.tests == 0
    assert motivos_de_rechazo(resumen, minimo=1)


def test_una_seleccion_entera_saltada_se_rechaza(tmp_path: Path) -> None:
    """El caso que motiva el guard: verde sin haber ejercitado nada."""
    directorio = _proyecto(
        tmp_path,
        "import pytest\n\n"
        "@pytest.mark.ffmpeg\n"
        '@pytest.mark.skip(reason="sin ffmpeg")\n'
        "def test_uno():\n    assert False\n\n"
        "@pytest.mark.ffmpeg\n"
        '@pytest.mark.skip(reason="sin ffmpeg")\n'
        "def test_dos():\n    assert False\n",
    )
    xml, codigo = _informe(directorio)
    # pytest la da por BUENA: codigo 0. Por eso hace falta el guard.
    assert codigo == 0
    resumen = leer_resumen(xml)
    assert resumen.tests == 2
    assert resumen.skipped == 2
    assert resumen.ejecutadas == 0
    motivos = motivos_de_rechazo(resumen, minimo=1)
    assert any("saltadas o en xfail" in motivo for motivo in motivos)


def test_una_seleccion_entera_en_xfail_se_rechaza(tmp_path: Path) -> None:
    """El hueco que un guard de texto NO ve.

    Una seleccion en `xfail` sale con codigo 0 y `-rs` no imprime "SKIPPED",
    asi que `grep SKIPPED` la aprobaria. En el XML si consta.
    """
    directorio = _proyecto(
        tmp_path,
        "import pytest\n\n"
        "@pytest.mark.ffmpeg\n"
        '@pytest.mark.xfail(reason="conocida")\n'
        "def test_uno():\n    assert False\n\n"
        "@pytest.mark.ffmpeg\n"
        '@pytest.mark.xfail(reason="conocida")\n'
        "def test_dos():\n    assert False\n",
    )
    xml, codigo = _informe(directorio)
    assert codigo == 0
    texto = xml.read_text(encoding="utf-8")
    assert "pytest.xfail" in texto, "pytest marca el xfail como skipped tipado"
    resumen = leer_resumen(xml)
    assert resumen.skipped == 2
    assert resumen.ejecutadas == 0
    assert motivos_de_rechazo(resumen, minimo=1)


def test_una_ejecucion_real_se_acredita(tmp_path: Path) -> None:
    """El contrapunto: pruebas que corren hasta un veredicto SI acreditan."""
    directorio = _proyecto(
        tmp_path,
        "import pytest\n\n"
        "@pytest.mark.ffmpeg\n"
        "def test_uno():\n    assert True\n\n"
        "@pytest.mark.ffmpeg\n"
        "def test_dos():\n    assert True\n",
    )
    xml, codigo = _informe(directorio)
    assert codigo == 0
    resumen = leer_resumen(xml)
    assert resumen.tests == 2
    assert resumen.skipped == 0
    assert resumen.ejecutadas == 2
    assert motivos_de_rechazo(resumen, minimo=2) == []


def test_una_prueba_fallida_se_rechaza(tmp_path: Path) -> None:
    directorio = _proyecto(
        tmp_path,
        "import pytest\n\n"
        "@pytest.mark.ffmpeg\n"
        "def test_uno():\n    assert False\n",
    )
    xml, codigo = _informe(directorio)
    assert codigo == 1
    resumen = leer_resumen(xml)
    assert resumen.failures == 1
    assert motivos_de_rechazo(resumen, minimo=1)


# ---------------------------------------------------------------------------
# Suelo de recuento y errores del propio informe
# ---------------------------------------------------------------------------


def test_un_derrumbe_de_la_seleccion_se_rechaza() -> None:
    """Que quede una prueba trivial no sustituye a la integracion entera."""
    resumen = Resumen(tests=1, failures=0, errors=0, skipped=0)
    motivos = motivos_de_rechazo(resumen, minimo=40)
    assert any("al menos 40" in motivo for motivo in motivos)


def test_el_suelo_no_castiga_un_cambio_legitimo() -> None:
    """El minimo es un suelo, no el recuento exacto: puede crecer o menguar."""
    assert motivos_de_rechazo(
        Resumen(tests=45, failures=0, errors=0, skipped=0), minimo=40
    ) == []
    assert motivos_de_rechazo(
        Resumen(tests=80, failures=0, errors=0, skipped=0), minimo=40
    ) == []


def test_varios_testsuite_se_suman(tmp_path: Path) -> None:
    xml = tmp_path / "multi.xml"
    xml.write_text(
        '<testsuites><testsuite tests="3" failures="0" errors="0" skipped="1"/>'
        '<testsuite tests="2" failures="1" errors="0" skipped="0"/></testsuites>',
        encoding="utf-8",
    )
    resumen = leer_resumen(xml)
    assert (resumen.tests, resumen.skipped, resumen.failures) == (5, 1, 1)


def test_un_informe_ilegible_no_se_da_por_bueno(tmp_path: Path) -> None:
    roto = tmp_path / "roto.xml"
    roto.write_text("esto no es XML", encoding="utf-8")
    with pytest.raises(ReporteInvalido):
        leer_resumen(roto)
    # Y por la CLI sale con codigo propio, no con 0.
    assert main([str(roto)]) == 2


def test_un_informe_ausente_no_se_da_por_bueno(tmp_path: Path) -> None:
    assert main([str(tmp_path / "no-existe.xml")]) == 2


def test_la_cli_distingue_acreditado_de_rechazado(tmp_path: Path) -> None:
    bueno = tmp_path / "bueno.xml"
    bueno.write_text(
        '<testsuite tests="5" failures="0" errors="0" skipped="0"/>',
        encoding="utf-8",
    )
    assert main([str(bueno), "--minimo", "5"]) == 0

    saltado = tmp_path / "saltado.xml"
    saltado.write_text(
        '<testsuite tests="5" failures="0" errors="0" skipped="5"/>',
        encoding="utf-8",
    )
    assert main([str(saltado), "--minimo", "1"]) == 1


# ---------------------------------------------------------------------------
# Que las marcas del repositorio esten bien compuestas
# ---------------------------------------------------------------------------


def test_toda_prueba_de_integracion_puede_saltarse_sin_ffmpeg() -> None:
    """Una prueba marcada `ffmpeg` tiene que llevar TAMBIEN su `skipif`.

    Regresion de un defecto real: `pytest.mark.ffmpeg(pytest.mark.skipif(...))`
    parece componer dos marcas y no lo hace —el `skipif` acaba como argumento
    de `ffmpeg` y no salta nunca—. Con las herramientas presentes no se nota;
    sin ellas, 24 pruebas intentaban ejecutarse en vez de saltarse.

    Se comprueba sobre la coleccion REAL de pytest, no leyendo el codigo.
    """
    raiz = Path(__file__).parent.parent
    salida = raiz / "marcas.json"
    script = (
        "import json, sys\n"
        "import pytest\n"
        "class Recolector:\n"
        "    def __init__(self):\n"
        "        self.filas = []\n"
        "    def pytest_collection_modifyitems(self, items):\n"
        "        for item in items:\n"
        "            marcas = {m.name for m in item.iter_markers()}\n"
        "            if 'ffmpeg' in marcas:\n"
        "                self.filas.append([item.nodeid, sorted(marcas)])\n"
        "r = Recolector()\n"
        "pytest.main(['-m', 'ffmpeg', '--collect-only', '-q',\n"
        "             '-p', 'no:cacheprovider', str(sys.argv[1])], plugins=[r])\n"
        "json.dump(r.filas, open(sys.argv[2], 'w'))\n"
    )
    proceso = subprocess.run(
        [sys.executable, "-c", script, str(raiz / "tests"), str(salida)],
        cwd=raiz, capture_output=True, text=True, timeout=180, check=False,
    )
    assert salida.is_file(), proceso.stdout + proceso.stderr
    try:
        filas = json.loads(salida.read_text(encoding="utf-8"))
    finally:
        salida.unlink()

    assert filas, "la marca `ffmpeg` no selecciona nada"
    sin_salto = [nodeid for nodeid, marcas in filas if "skipif" not in marcas]
    assert not sin_salto, (
        "estas pruebas llevan la marca `ffmpeg` pero no pueden saltarse sin las "
        f"herramientas: {sin_salto[:5]}"
    )
