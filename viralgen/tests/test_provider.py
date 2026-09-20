"""Proveedor real: clasificacion de errores, reintentos acotados y presupuesto.

No se abre ninguna conexion: se inyecta un cliente falso y se construyen las
excepciones del SDK sin pasar por su __init__ (evita depender de httpx).
"""

from __future__ import annotations

from types import SimpleNamespace

import openai
import pytest

from viralgen.config import Settings
from viralgen.errors import (
    CallBudgetExceededError,
    ProviderIncompleteError,
    ProviderPermanentError,
    ProviderRefusalError,
    ProviderTransientError,
)
from viralgen.providers.base import CallBudget, ProviderRequest
from viralgen.providers.openai_provider import OpenAIProvider
from viralgen.schemas.provider import ProviderIdeaBatch


def _error(cls, **atributos):
    """Instancia de una excepcion del SDK sin invocar su constructor."""
    exc = cls.__new__(cls)
    for nombre, valor in atributos.items():
        setattr(exc, nombre, valor)
    return exc


def _respuesta_valida():
    lote = ProviderIdeaBatch.model_validate(
        {
            "ideas": [
                {
                    "idea_ref": "idea_1",
                    "title": "Titulo",
                    "premise": "Premisa",
                    "topic": "tema",
                    "promise": "promesa",
                    "possible_ending": "final",
                    "visual_concept": "visual",
                    "educational_goal": None,
                    "fact_ids": [],
                    "ratings": {
                        "hook": {"score": 4, "rationale": "r"},
                        "clarity": {"score": 4, "rationale": "r"},
                        "payoff": {"score": 4, "rationale": "r"},
                        "visual_potential": {"score": 4, "rationale": "r"},
                    },
                }
            ]
        }
    )
    return SimpleNamespace(
        id="resp_123",
        status="completed",
        output=[],
        output_parsed=lote,
        model="modelo-de-prueba",
        usage=SimpleNamespace(input_tokens=120, output_tokens=340),
        incomplete_details=None,
    )


class ClienteFalso:
    def __init__(self, resultados):
        self.resultados = list(resultados)
        self.llamadas = []
        self.responses = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.llamadas.append(kwargs)
        resultado = self.resultados.pop(0)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado


@pytest.fixture
def ajustes() -> Settings:
    return Settings(
        _env_file=None,
        openai_api_key="sk-de-prueba",
        openai_model="modelo-de-prueba",
        max_transport_retries=2,
        log_level="ERROR",
    )


def _peticion() -> ProviderRequest:
    return ProviderRequest(
        stage="ideas",
        instructions="instrucciones",
        input_text="datos",
        schema_model=ProviderIdeaBatch,
        payload={},
    )


def _proveedor(ajustes, resultados, dormidas=None):
    cliente = ClienteFalso(resultados)
    proveedor = OpenAIProvider(
        settings=ajustes,
        client=cliente,
        sleep=(dormidas.append if dormidas is not None else (lambda _s: None)),
    )
    return proveedor, cliente


def test_respuesta_correcta(ajustes: Settings) -> None:
    proveedor, cliente = _proveedor(ajustes, [_respuesta_valida()])
    resultado = proveedor.generate_structured(_peticion(), CallBudget(6))
    assert isinstance(resultado.parsed, ProviderIdeaBatch)
    assert resultado.usage.input_tokens == 120
    assert resultado.usage.request_id == "resp_123"
    # temperature NO se envia si no esta habilitado explicitamente.
    assert "temperature" not in cliente.llamadas[0]
    assert cliente.llamadas[0]["store"] is False


def test_temperature_solo_si_esta_habilitado() -> None:
    ajustes = Settings(
        _env_file=None,
        openai_api_key="sk",
        openai_model="m",
        send_temperature=True,
        temperature=0.7,
    )
    proveedor, cliente = _proveedor(ajustes, [_respuesta_valida()])
    proveedor.generate_structured(_peticion(), CallBudget(6))
    assert cliente.llamadas[0]["temperature"] == 0.7


def test_reintenta_solo_lo_transitorio_y_de_forma_acotada(ajustes: Settings) -> None:
    dormidas: list[float] = []
    fallo = _error(openai.APITimeoutError)
    proveedor, cliente = _proveedor(ajustes, [fallo, fallo, _respuesta_valida()], dormidas)
    presupuesto = CallBudget(6)
    proveedor.generate_structured(_peticion(), presupuesto)
    assert len(cliente.llamadas) == 3  # 1 intento + 2 reintentos
    assert presupuesto.used == 3  # los reintentos SI cuentan
    assert len(dormidas) == 2 and all(espera > 0 for espera in dormidas)


def test_se_agotan_los_reintentos(ajustes: Settings) -> None:
    fallo = _error(openai.APIConnectionError)
    proveedor, cliente = _proveedor(ajustes, [fallo] * 3)
    with pytest.raises(ProviderTransientError, match="Se agotaron los reintentos"):
        proveedor.generate_structured(_peticion(), CallBudget(6))
    assert len(cliente.llamadas) == 3


def test_el_presupuesto_corta_antes_de_emitir(ajustes: Settings) -> None:
    fallo = _error(openai.APITimeoutError)
    proveedor, cliente = _proveedor(ajustes, [fallo] * 5)
    presupuesto = CallBudget(2)
    with pytest.raises(ProviderTransientError):
        proveedor.generate_structured(_peticion(), presupuesto)
    assert presupuesto.used == 2
    assert len(cliente.llamadas) == 2


def test_presupuesto_ya_agotado(ajustes: Settings) -> None:
    proveedor, cliente = _proveedor(ajustes, [_respuesta_valida()])
    presupuesto = CallBudget(1)
    presupuesto.spend("ideas")
    with pytest.raises(CallBudgetExceededError):
        proveedor.generate_structured(_peticion(), presupuesto)
    assert cliente.llamadas == []


@pytest.mark.parametrize(
    "excepcion",
    [
        _error(openai.AuthenticationError),
        _error(openai.PermissionDeniedError),
        _error(openai.NotFoundError),
        _error(openai.BadRequestError),
        _error(openai.RateLimitError, code="insufficient_quota"),
    ],
    ids=["clave", "permisos", "modelo", "peticion", "cuota"],
)
def test_errores_permanentes_no_se_reintentan(ajustes: Settings, excepcion) -> None:
    proveedor, cliente = _proveedor(ajustes, [excepcion, _respuesta_valida()])
    with pytest.raises(ProviderPermanentError):
        proveedor.generate_structured(_peticion(), CallBudget(6))
    assert len(cliente.llamadas) == 1


def test_rate_limit_temporal_si_se_reintenta(ajustes: Settings) -> None:
    temporal = _error(openai.RateLimitError, code="rate_limit_exceeded", response=None)
    proveedor, cliente = _proveedor(ajustes, [temporal, _respuesta_valida()])
    proveedor.generate_structured(_peticion(), CallBudget(6))
    assert len(cliente.llamadas) == 2


def test_respeta_retry_after(ajustes: Settings) -> None:
    dormidas: list[float] = []
    temporal = _error(
        openai.RateLimitError,
        code="rate_limit_exceeded",
        response=SimpleNamespace(headers={"retry-after": "7"}),
    )
    proveedor, _ = _proveedor(ajustes, [temporal, _respuesta_valida()], dormidas)
    proveedor.generate_structured(_peticion(), CallBudget(6))
    assert dormidas == [7.0]


def test_respuesta_incompleta(ajustes: Settings) -> None:
    respuesta = _respuesta_valida()
    respuesta.status = "incomplete"
    respuesta.incomplete_details = SimpleNamespace(reason="max_output_tokens")
    proveedor, cliente = _proveedor(ajustes, [respuesta, _respuesta_valida()])
    with pytest.raises(ProviderIncompleteError, match="max_output_tokens"):
        proveedor.generate_structured(_peticion(), CallBudget(6))
    assert len(cliente.llamadas) == 1  # no se reintenta


def test_rechazo_explicito(ajustes: Settings) -> None:
    respuesta = _respuesta_valida()
    respuesta.output = [
        SimpleNamespace(content=[SimpleNamespace(refusal="No puedo ayudar con eso", type="refusal")])
    ]
    proveedor, _ = _proveedor(ajustes, [respuesta])
    with pytest.raises(ProviderRefusalError, match="No puedo ayudar"):
        proveedor.generate_structured(_peticion(), CallBudget(6))


def test_sin_contenido_estructurado(ajustes: Settings) -> None:
    respuesta = _respuesta_valida()
    respuesta.output_parsed = None
    proveedor, _ = _proveedor(ajustes, [respuesta])
    with pytest.raises(Exception, match="no devolvio contenido estructurado"):
        proveedor.generate_structured(_peticion(), CallBudget(6))


def test_uso_desconocido_no_se_cuenta_como_cero(ajustes: Settings) -> None:
    from viralgen.providers.base import UsageTotals

    respuesta = _respuesta_valida()
    respuesta.usage = None
    proveedor, _ = _proveedor(ajustes, [respuesta])
    resultado = proveedor.generate_structured(_peticion(), CallBudget(6))
    assert resultado.usage.known is False

    totales = UsageTotals()
    totales.add(resultado.usage)
    # Sin consumo conocido no se puede estimar coste aunque haya tarifas.
    assert totales.estimated_cost_usd((1.0, 2.0)) is None


def test_coste_null_sin_tarifas() -> None:
    from viralgen.providers.base import ProviderUsage, UsageTotals

    totales = UsageTotals()
    totales.add(ProviderUsage(input_tokens=1_000_000, output_tokens=500_000, known=True))
    assert totales.estimated_cost_usd(None) is None
    assert totales.estimated_cost_usd((2.0, 8.0)) == pytest.approx(6.0)


def test_faltan_claves_en_modo_real() -> None:
    from viralgen.errors import ConfigError

    ajustes = Settings(_env_file=None)
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        ajustes.require_provider_settings()
