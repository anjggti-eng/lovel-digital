"""Geração de resposta restrita aos trechos recuperados (RAG fechado)."""

from __future__ import annotations

from . import llm_client

NAO_COBERTO = "NAO_COBERTO"

SYSTEM_PROMPT = """Você é um atendente da Lovel Digital. Responda ao cliente usando SOMENTE as informações dos TRECHOS DA BASE DE CONHECIMENTO fornecidos abaixo.
Regras rígidas:
1. Se os trechos não contiverem a resposta, responda EXATAMENTE com a string: NAO_COBERTO
2. Não invente prazos, valores, políticas ou procedimentos.
3. Seja cordial, objetivo, no máximo 4 frases.
4. Não use markdown.
5. Nunca mencione "base de conhecimento" nem "trechos".

TRECHOS:
{trechos_recuperados}"""


def formatar_trechos(trechos: list[dict]) -> str:
    """Formata os trechos para o prompt (id + tópico + resposta oficial)."""
    return "\n".join(
        f"[{trecho['id']}] {trecho['topico']}: {trecho['resposta_padrao']}"
        for trecho in trechos
    )


def gerar_resposta(trechos: list[dict], mensagem: str) -> str:
    """Gera a resposta ou retorna ``NAO_COBERTO``.

    Sem trechos, o LLM nem é chamado. Resposta vazia ou contendo ``NAO_COBERTO``
    também é normalizada para ``NAO_COBERTO`` (força transbordo).
    """
    if not trechos:
        return NAO_COBERTO

    system_prompt = SYSTEM_PROMPT.replace(
        "{trechos_recuperados}", formatar_trechos(trechos)
    )

    try:
        texto = llm_client.gerar_resposta(trechos, mensagem, system_prompt)
    except llm_client.LLMError:
        return NAO_COBERTO

    texto = (texto or "").strip()
    if not texto or NAO_COBERTO in texto.upper():
        return NAO_COBERTO
    return texto
