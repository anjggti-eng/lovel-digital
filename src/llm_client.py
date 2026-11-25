"""Wrapper do LLM (OpenAI) com mock determinístico.

O modelo, a temperatura e os limites de tokens vêm de ``config/lovel.json``.
``MOCK_LLM=1`` no ambiente sempre vence e força o modo offline.
"""

from __future__ import annotations

import json
import os
import re

from dotenv import load_dotenv

from . import config as config_mod

load_dotenv()

NAO_COBERTO = "NAO_COBERTO"

_TERMOS_CRITICOS_MOCK = ("cancel", "procon", "advogado", "processo")
_TERMOS_LOGIN_MOCK = ("login", "senha", "acesso", "entrar")
_TERMOS_ENTREGA_MOCK = ("prazo", "entrega", "cep", "frete")
_TERMOS_REEMBOLSO_MOCK = ("reembolso", "estorno")


class LLMError(RuntimeError):
    """Falha de comunicação ou de parsing com o provedor de LLM."""


def _tem_chave() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def modo_llm() -> str:
    """Modo efetivo: ``mock``, ``real`` ou ``real_sem_chave``."""
    if os.getenv("MOCK_LLM") == "1":
        return "mock"
    modo = config_mod.carregar()["llm"]["modo"]
    if modo == "mock":
        return "mock"
    if modo == "real":
        return "real" if _tem_chave() else "real_sem_chave"
    return "real" if _tem_chave() else "mock"


def mock_ativo() -> bool:
    return modo_llm() != "real"


def modelo() -> str:
    return config_mod.carregar()["llm"]["model"]


def status_llm() -> dict:
    """Estado para a UI: modo efetivo, modelo e o porquê."""
    cfg = config_mod.carregar()["llm"]
    modo = modo_llm()
    if os.getenv("MOCK_LLM") == "1":
        motivo = "MOCK_LLM=1 no ambiente: modo offline forçado."
    elif modo == "real":
        motivo = "Chamadas reais para a OpenAI."
    elif modo == "real_sem_chave":
        motivo = "Modo real selecionado, mas OPENAI_API_KEY não está definida."
    elif cfg["modo"] == "mock":
        motivo = "Modo offline escolhido nas configurações."
    else:
        motivo = "Sem OPENAI_API_KEY: usando o mock automático."
    return {
        "modo": modo,
        "mock": modo != "real",
        "model": cfg["model"],
        "motivo": motivo,
        "chave_configurada": _tem_chave(),
    }


def _contem(texto: str, termos: tuple[str, ...]) -> bool:
    return any(termo in texto for termo in termos)


def _classificar_mock(mensagem: str) -> dict:
    texto = mensagem.lower()

    if _contem(texto, _TERMOS_CRITICOS_MOCK):
        return {
            "intencao": "reclamacao_critica",
            "urgencia": "critica",
            "justificativa": "Pedido de cancelamento ou ameaça explícita (mock).",
        }
    if _contem(texto, _TERMOS_LOGIN_MOCK):
        return {
            "intencao": "suporte_tecnico",
            "urgencia": "media",
            "justificativa": "Relato de problema de acesso/login (mock).",
        }
    if _contem(texto, _TERMOS_ENTREGA_MOCK):
        return {
            "intencao": "duvida_comercial",
            "urgencia": "baixa",
            "justificativa": "Dúvida sobre prazo, frete ou entrega (mock).",
        }
    if _contem(texto, _TERMOS_REEMBOLSO_MOCK):
        return {
            "intencao": "duvida_comercial",
            "urgencia": "alta",
            "justificativa": "Solicitação de reembolso/estorno (mock).",
        }

    total_palavras = len(re.findall(r"\w+", mensagem))
    if total_palavras < 5:
        justificativa = "Mensagem curta ou incompleta (mock)."
    else:
        justificativa = "Sem intenção clara mapeada pelas heurísticas (mock)."
    return {
        "intencao": "fora_de_escopo",
        "urgencia": "media",
        "justificativa": justificativa,
    }


def _gerar_resposta_mock(trechos: list[dict], mensagem: str) -> str:
    if not trechos:
        return NAO_COBERTO
    return trechos[0]["resposta_padrao"]


def _cliente_openai():
    from openai import OpenAI

    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def classificar(mensagem: str, system_prompt: str) -> dict:
    """Classifica a mensagem e devolve um dict cru (validado pelo classifier)."""
    if mock_ativo():
        return _classificar_mock(mensagem)

    cfg = config_mod.carregar()["llm"]
    try:
        cliente = _cliente_openai()
        resposta = cliente.chat.completions.create(
            model=cfg["model"],
            temperature=cfg["temperature"],
            max_tokens=cfg["max_tokens_classificacao"],
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": mensagem},
            ],
        )
        conteudo = (resposta.choices[0].message.content or "").strip()
        return json.loads(conteudo)
    except Exception as exc:  # rede, timeout, JSON inválido, SDK ausente...
        raise LLMError(f"Falha ao classificar via LLM: {exc}") from exc


def gerar_resposta(trechos: list[dict], mensagem: str, system_prompt: str) -> str:
    """Gera resposta restrita aos trechos fornecidos (ou NAO_COBERTO)."""
    if mock_ativo():
        return _gerar_resposta_mock(trechos, mensagem)

    if not trechos:
        return NAO_COBERTO

    cfg = config_mod.carregar()["llm"]
    try:
        cliente = _cliente_openai()
        resposta = cliente.chat.completions.create(
            model=cfg["model"],
            temperature=cfg["temperature"],
            max_tokens=cfg["max_tokens_resposta"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": mensagem},
            ],
        )
        return (resposta.choices[0].message.content or "").strip()
    except Exception as exc:
        raise LLMError(f"Falha ao gerar resposta via LLM: {exc}") from exc
