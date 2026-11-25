"""Classificação de intenção e urgência via LLM com validação defensiva."""

from __future__ import annotations

from . import llm_client
from .models import Classificacao, Intencao, Urgencia

SYSTEM_PROMPT = """Você é um classificador de mensagens de clientes da Lovel Digital.
Sua ÚNICA função é retornar um JSON válido, sem texto adicional, sem markdown, sem explicações.

Formato de saída obrigatório:
{"intencao": "<valor>", "urgencia": "<valor>", "justificativa": "<até 120 caracteres>"}

Valores permitidos para "intencao":
- "duvida_comercial"
- "suporte_tecnico"
- "reclamacao_critica"
- "fora_de_escopo"

Valores permitidos para "urgencia":
- "baixa"
- "media"
- "alta"
- "critica"

Regras:
1. Se a mensagem for ambígua, incompleta ou não corresponder a nenhuma intenção clara, use "fora_de_escopo" com urgencia "media".
2. Se contiver raiva explícita, palavrões, ameaças (Procon, advogado, processo) ou pedido de cancelamento imediato, use "reclamacao_critica" com urgencia "critica" ou "alta".
3. Problemas técnicos (login, senha, integração, bug) → "suporte_tecnico".
4. Perguntas sobre prazo, preço, frete, produto, política → "duvida_comercial".
5. NUNCA invente categorias fora da lista."""


def _parse_intencao(valor: object) -> Intencao | None:
    try:
        return Intencao(str(valor).strip().lower())
    except ValueError:
        return None


def _parse_urgencia(valor: object) -> Urgencia | None:
    try:
        return Urgencia(str(valor).strip().lower())
    except ValueError:
        return None


def classificar(mensagem: str) -> Classificacao:
    """Classifica a mensagem; qualquer saída inválida cai em fora_de_escopo/media."""
    try:
        dados = llm_client.classificar(mensagem, SYSTEM_PROMPT)
    except llm_client.LLMError:
        return Classificacao(
            intencao=Intencao.FORA_DE_ESCOPO,
            urgencia=Urgencia.MEDIA,
            justificativa="Falha na chamada ao LLM; encaminhado por segurança.",
        )

    intencao = _parse_intencao(dados.get("intencao"))
    urgencia = _parse_urgencia(dados.get("urgencia"))

    if intencao is None or urgencia is None:
        return Classificacao(
            intencao=Intencao.FORA_DE_ESCOPO,
            urgencia=Urgencia.MEDIA,
            justificativa="Saída do LLM fora do schema; encaminhado por segurança.",
        )

    justificativa = str(dados.get("justificativa") or "").strip()[:120]
    if not justificativa:
        justificativa = "Sem justificativa fornecida pelo classificador."

    return Classificacao(
        intencao=intencao,
        urgencia=urgencia,
        justificativa=justificativa,
    )
