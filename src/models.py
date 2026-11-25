"""Modelos de domínio do fluxo de atendimento."""

from dataclasses import dataclass
from enum import Enum


class Intencao(str, Enum):
    DUVIDA_COMERCIAL = "duvida_comercial"
    SUPORTE_TECNICO = "suporte_tecnico"
    RECLAMACAO_CRITICA = "reclamacao_critica"
    FORA_DE_ESCOPO = "fora_de_escopo"
    SAUDACAO = "saudacao"


class Urgencia(str, Enum):
    BAIXA = "baixa"
    MEDIA = "media"
    ALTA = "alta"
    CRITICA = "critica"


class Status(str, Enum):
    RESPONDIDO_AUTOMATICO = "RESPONDIDO_AUTOMATICO"
    TRANSBORDADO = "TRANSBORDADO"


@dataclass
class MensagemCliente:
    cliente_id: str
    nome: str
    telefone: str
    mensagem: str
    timestamp: str


@dataclass
class Classificacao:
    intencao: Intencao
    urgencia: Urgencia
    justificativa: str


@dataclass
class ResultadoAtendimento:
    mensagem: MensagemCliente
    classificacao: Classificacao
    status: Status
    resposta_gerada: str | None
    ticket_id: str | None
    motivo_transbordo: str | None
