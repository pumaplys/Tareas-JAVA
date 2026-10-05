"""El diagnostico del piloto: informa de presencia, nunca de valores.

Lo que se prueba es la propiedad que lo hace seguro de pegar en un informe: que
el JSON que produce **no contiene el valor de ninguna credencial**, ni siquiera
cuando esas variables estan definidas.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from diagnostico_piloto import diagnosticar, main  # noqa: E402

#: Valores que NO deben aparecer en la salida por ningun motivo.
VALORES = {
    "OPENAI_API_KEY": "sk-valor-secreto-de-prueba",
    "ELEVENLABS_API_KEY": "el-valor-secreto-de-elevenlabs",
    "YOUTUBE_CLIENT_SECRET": "secreto-de-cliente-de-prueba",
    "PUBLISH_STAGING_SECRET_ACCESS_KEY": "clave-secreta-de-staging",
}


@pytest.fixture
def entorno_con_claves(monkeypatch, tmp_path: Path):
    for nombre, valor in VALORES.items():
        monkeypatch.setenv(nombre, valor)
    monkeypatch.setenv("OPENAI_MODEL", "modelo-de-prueba")
    monkeypatch.setenv("VIRALGEN_DATA_DIR", str(tmp_path / "datos"))
    (tmp_path / "datos").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_el_diagnostico_no_filtra_ningun_valor(entorno_con_claves) -> None:
    informe = diagnosticar()
    texto = json.dumps(informe, ensure_ascii=False)
    for nombre, valor in VALORES.items():
        assert valor not in texto, f"se filtro el valor de {nombre}"
        # El NOMBRE si puede aparecer: es lo que el operador necesita saber.
    assert informe["secrets_shown"] is False


def test_declara_presencia_por_etapa(entorno_con_claves) -> None:
    informe = diagnosticar()
    etapas = informe["configuracion"]["por_etapa"]
    assert etapas["1_guion"]["variables"]["OPENAI_API_KEY"] is True
    assert etapas["1_guion"]["variables"]["OPENAI_MODEL"] is True
    # El montaje no necesita credenciales.
    assert etapas["4_montaje"]["variables"] == {}
    # Y lo que falta se enumera por su nombre exacto.
    assert "OPENAI_IMAGE_MODEL" in informe["configuracion"]["faltan"]


def test_un_coste_desconocido_sigue_siendo_desconocido(entorno_con_claves) -> None:
    informe = diagnosticar()
    tarifas = informe["tarifas"]
    assert tarifas["texto_por_1m_tokens_usd"] == {"entrada": None, "salida": None}
    assert tarifas["voz_por_1k_caracteres_usd"] is None
    assert "DESCONOCIDO" in tarifas["nota"]


def test_no_inventa_un_voice_id(entorno_con_claves) -> None:
    """El catalogo interno los trae vacios, y eso es lo que debe decir."""
    informe = diagnosticar()
    assert informe["voz"]["voice_id_en_catalogo"] is False
    assert informe["voz"]["resoluble"] is False
    assert "no inventa" in informe["voz"]["nota"]


def test_informa_del_estado_de_la_verificacion_por_destino(entorno_con_claves) -> None:
    informe = diagnosticar()
    bloqueos = informe["verificacion"]["bloqueos_por_destino"]
    assert bloqueos["youtube"] == []
    assert bloqueos["instagram"]
    assert bloqueos["staging"] == ["s3_presign_expiry"]
    assert informe["veredicto"]["youtube_sin_bloqueos_documentales"] is True


def test_mide_el_disco_y_lo_compara_con_el_minimo(entorno_con_claves) -> None:
    disco = diagnosticar()["disco"]
    assert disco["libre_mb"] >= 0
    assert disco["min_free_disk_mb"] > 0
    assert isinstance(disco["suficiente"], bool)


def test_el_comando_sale_con_cero_y_escribe_json(entorno_con_claves, capsys) -> None:
    assert main(["--json"]) == 0
    datos = json.loads(capsys.readouterr().out)
    assert datos["command"] == "diagnostico_piloto"
    assert "veredicto" in datos
