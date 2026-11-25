"""Regra de transbordo human-in-the-loop (determinística, fora do LLM).

Quais regras valem, os termos críticos e os prazos por urgência vêm de
``config/lovel.json`` — a UI permite ligar/desligar cada regra.
"""

from __future__ import annotations

import unicodedata

from . import config as config_mod
from .models import Classificacao, Intencao, MensagemCliente, Urgencia


def _cfg() -> dict:
    return config_mod.carregar()["transbordo"]


def termos_criticos() -> list[str]:
    return list(_cfg()["termos_criticos"])


def prazos() -> dict[str, str]:
    return dict(_cfg()["prazos"])


def _normalizar(texto: str) -> str:
    """Minúsculas e sem acentos, para casar "juridico" com "jurídico"."""
    decomposto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(char for char in decomposto if not unicodedata.combining(char))


def _contem_termo_critico(mensagem: str) -> bool:
    texto = _normalizar(mensagem)
    return any(_normalizar(termo) in texto for termo in termos_criticos())


def deve_transbordar(
    classificacao: Classificacao,
    resposta: str | None,
    mensagem_original: str,
) -> tuple[bool, str | None]:
    """Aplica as regras fixas de transbordo, na ordem da especificação."""
    cfg = _cfg()

    if cfg["reclamacao_critica"] and classificacao.intencao == Intencao.RECLAMACAO_CRITICA:
        return True, "reclamacao_critica"

    if (
        cfg["termos_criticos_urgentes"]
        and classificacao.urgencia in (Urgencia.ALTA, Urgencia.CRITICA)
        and _contem_termo_critico(mensagem_original)
    ):
        return True, "cancelamento_ou_reembolso_critico"

    resposta_normalizada = (resposta or "").strip()
    if cfg["sem_cobertura"] and (
        not resposta_normalizada or "NAO_COBERTO" in resposta_normalizada.upper()
    ):
        return True, "nao_coberto_pela_base"

    if (
        cfg["fora_de_escopo_urgente"]
        and classificacao.intencao == Intencao.FORA_DE_ESCOPO
        and classificacao.urgencia in (Urgencia.ALTA, Urgencia.CRITICA)
    ):
        return True, "fora_de_escopo_urgente"

    return False, None


def acao_sugerida(urgencia: Urgencia) -> str:
    return prazos()[urgencia.value]


def montar_ticket(
    *,
    ticket_id: str,
    criado_em: str,
    mensagem: MensagemCliente,
    classificacao: Classificacao,
    motivo: str,
) -> dict:
    """Monta o JSON do ticket com resumo estruturado para o humano."""
    acao = acao_sugerida(classificacao.urgencia)
    trecho = mensagem.mensagem.strip()
    if len(trecho) > 180:
        trecho = trecho[:177] + "..."

    resumo = (
        f'Cliente {mensagem.nome} ({mensagem.cliente_id}) relata: "{trecho}". '
        f"Intenção: {classificacao.intencao.value}. "
        f"Urgência: {classificacao.urgencia.value}. "
        f"Motivo do transbordo: {motivo}. "
        f"Ação sugerida: {acao[0].lower() + acao[1:]}"
    )

    return {
        "ticket_id": ticket_id,
        "criado_em": criado_em,
        "status": "ABERTO",
        "cliente": {
            "cliente_id": mensagem.cliente_id,
            "nome": mensagem.nome,
            "telefone": mensagem.telefone,
        },
        "mensagem_original": mensagem.mensagem,
        "classificacao": {
            "intencao": classificacao.intencao.value,
            "urgencia": classificacao.urgencia.value,
            "justificativa": classificacao.justificativa,
        },
        "motivo_transbordo": motivo,
        "resumo_estruturado": resumo,
        "acao_sugerida": acao,
    }
