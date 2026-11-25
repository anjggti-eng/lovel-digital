"""Registro estruturado: CSV, tickets JSON e payloads de webhook simulados."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import ResultadoAtendimento

CSV_FILENAME = "atendimentos.csv"
CSV_HEADER = [
    "timestamp",
    "cliente_id",
    "nome",
    "intencao",
    "urgencia",
    "status",
    "resposta_gerada",
    "ticket_id",
    "motivo_transbordo",
]


def agora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def garantir_diretorios(output_dir: str | Path) -> Path:
    output_dir = Path(output_dir)
    (output_dir / "tickets").mkdir(parents=True, exist_ok=True)
    (output_dir / "webhook_payloads").mkdir(parents=True, exist_ok=True)
    return output_dir


def proximo_ticket_id(output_dir: str | Path) -> str:
    """Retorna TKT-AAAAMMDD-NNN, sequencial por dia, olhando os arquivos existentes."""
    output_dir = Path(output_dir)
    dia = datetime.now(timezone.utc).strftime("%Y%m%d")
    padrao = re.compile(rf"^TKT-{dia}-(\d{{3}})\.json$")
    maior = 0
    for arquivo in (output_dir / "tickets").glob(f"TKT-{dia}-*.json"):
        casamento = padrao.match(arquivo.name)
        if casamento:
            maior = max(maior, int(casamento.group(1)))
    return f"TKT-{dia}-{maior + 1:03d}"


def registrar_atendimento(resultado: ResultadoAtendimento, output_dir: str | Path) -> Path:
    """Anexa uma linha ao CSV (cria o cabeçalho quando necessário)."""
    output_dir = garantir_diretorios(output_dir)
    caminho = output_dir / CSV_FILENAME

    novo_arquivo = not caminho.exists() or caminho.stat().st_size == 0
    with caminho.open("a", encoding="utf-8", newline="") as arquivo:
        escritor = csv.writer(arquivo, lineterminator="\n")
        if novo_arquivo:
            escritor.writerow(CSV_HEADER)
        escritor.writerow(
            [
                resultado.mensagem.timestamp,
                resultado.mensagem.cliente_id,
                resultado.mensagem.nome,
                resultado.classificacao.intencao.value,
                resultado.classificacao.urgencia.value,
                resultado.status.value,
                resultado.resposta_gerada or "",
                resultado.ticket_id or "",
                resultado.motivo_transbordo or "",
            ]
        )
    return caminho


def _slug_timestamp(timestamp: str) -> str:
    """Remove caracteres inválidos em nomes de arquivo (``:`` no Windows)."""
    return re.sub(r"[:+]", "-", timestamp)


def salvar_webhook(resultado: ResultadoAtendimento, output_dir: str | Path) -> Path:
    """Simula o POST para n8n/Make gravando o payload em disco."""
    output_dir = garantir_diretorios(output_dir)
    timestamp = agora_iso()
    diretorio = output_dir / "webhook_payloads"

    base = f"{resultado.mensagem.cliente_id}_{_slug_timestamp(timestamp)}"
    caminho = diretorio / f"{base}.json"
    contador = 2
    while caminho.exists():
        caminho = diretorio / f"{base}-{contador}.json"
        contador += 1

    payload = {
        "event": "atendimento.processado",
        "timestamp": timestamp,
        "cliente": {
            "cliente_id": resultado.mensagem.cliente_id,
            "nome": resultado.mensagem.nome,
            "telefone": resultado.mensagem.telefone,
        },
        "intencao": resultado.classificacao.intencao.value,
        "urgencia": resultado.classificacao.urgencia.value,
        "status": resultado.status.value,
        "resposta": resultado.resposta_gerada,
        "ticket_id": resultado.ticket_id,
    }

    with caminho.open("w", encoding="utf-8") as arquivo:
        json.dump(payload, arquivo, ensure_ascii=False, indent=2)
        arquivo.write("\n")
    return caminho


def salvar_ticket(ticket: dict, output_dir: str | Path) -> Path:
    output_dir = garantir_diretorios(output_dir)
    caminho = output_dir / "tickets" / f"{ticket['ticket_id']}.json"
    with caminho.open("w", encoding="utf-8") as arquivo:
        json.dump(ticket, arquivo, ensure_ascii=False, indent=2)
        arquivo.write("\n")
    return caminho
