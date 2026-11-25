"""Orquestra o fluxo de atendimento (CLI e API programática)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import classifier, config, escalation, knowledge_base, recorder, responder
from .models import (
    Classificacao,
    Intencao,
    MensagemCliente,
    ResultadoAtendimento,
    Status,
    Urgencia,
)

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "data" / "simulated_messages"

CAMPOS_OBRIGATORIOS = ("cliente_id", "nome", "telefone", "mensagem", "timestamp")

SISTEMA_SAUDACAO = "saudacao"
RESPOSTA_SAUDACAO = "Olá! Como posso te ajudar hoje?"


def validar_payload(payload: object) -> None:
    """Valida o payload de entrada; levanta ValueError se inválido."""
    if not isinstance(payload, dict):
        raise ValueError("Payload inválido: esperado um objeto JSON.")

    ausentes = [campo for campo in CAMPOS_OBRIGATORIOS if campo not in payload]
    if ausentes:
        raise ValueError(
            "Payload inválido: campos obrigatórios ausentes: " + ", ".join(ausentes)
        )

    for campo in CAMPOS_OBRIGATORIOS:
        valor = payload[campo]
        if not isinstance(valor, str) or not valor.strip():
            raise ValueError(f"Payload inválido: campo '{campo}' deve ser texto não vazio.")


def processar_mensagem(
    payload: dict,
    output_dir: str | Path | None = None,
) -> ResultadoAtendimento:
    """Executa classificação -> busca -> resposta -> transbordo -> registro."""
    validar_payload(payload)

    destino = Path(output_dir) if output_dir is not None else config.diretorio_saida()
    registro = config.carregar()["registro"]

    mensagem = MensagemCliente(
        **{campo: payload[campo].strip() for campo in CAMPOS_OBRIGATORIOS}
    )

    # Atalho de saudação: "oi" / "ola" (exato) responde automaticamente,
    # sem buscar na base, sem transbordo e com resposta amigável fixa.
    saudacao_exata = mensagem.mensagem.strip().lower() in {"oi", "ola"}
    if saudacao_exata:
        classificacao = Classificacao(
            intencao=Intencao.SAUDACAO,
            urgencia=Urgencia.BAIXA,
            justificativa="Saudação simples atendida automaticamente.",
        )
        trechos: list = []
        resposta = RESPOSTA_SAUDACAO
        transbordar, motivo = False, None
    else:
        classificacao = classifier.classificar(mensagem.mensagem)

        trechos = knowledge_base.buscar_faqs(mensagem.mensagem)
        resposta = responder.gerar_resposta(trechos, mensagem.mensagem)

        transbordar, motivo = escalation.deve_transbordar(
            classificacao, resposta, mensagem.mensagem
        )

    ticket = None
    if transbordar:
        ticket = escalation.montar_ticket(
            ticket_id=recorder.proximo_ticket_id(destino),
            criado_em=recorder.agora_iso(),
            mensagem=mensagem,
            classificacao=classificacao,
            motivo=motivo or "nao_especificado",
        )
        if registro["tickets"]:
            recorder.salvar_ticket(ticket, destino)
        status = Status.TRANSBORDADO
        resposta_final = None
    else:
        status = Status.RESPONDIDO_AUTOMATICO
        resposta_final = resposta

    resultado = ResultadoAtendimento(
        mensagem=mensagem,
        classificacao=classificacao,
        status=status,
        resposta_gerada=resposta_final,
        ticket_id=ticket["ticket_id"] if ticket else None,
        motivo_transbordo=motivo,
    )

    if registro["csv"]:
        recorder.registrar_atendimento(resultado, destino)
    if registro["webhook"]:
        recorder.salvar_webhook(resultado, destino)
    return resultado


def limpar_saidas(output_dir: str | Path | None = None) -> None:
    """Limpa apenas os artefatos gerados pelo app (mantém o resto da pasta)."""
    destino = Path(output_dir) if output_dir is not None else config.diretorio_saida()
    outputs = recorder.garantir_diretorios(destino)

    csv_path = outputs / recorder.CSV_FILENAME
    if csv_path.exists():
        csv_path.unlink()
    for padrao in ("tickets/TKT-*.json", "webhook_payloads/*.json"):
        for arquivo in outputs.glob(padrao):
            arquivo.unlink()


def _encurtar(texto: str, limite: int = 110) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= limite else texto[: limite - 3] + "..."


def _imprimir_resultado(arquivo: Path, resultado: ResultadoAtendimento) -> None:
    print(f"\n[{arquivo.name}]")
    print(f"  cliente : {resultado.mensagem.cliente_id} ({resultado.mensagem.nome})")
    print(
        "  triagem : "
        f"{resultado.classificacao.intencao.value} | "
        f"urgencia={resultado.classificacao.urgencia.value}"
    )
    if resultado.status == Status.TRANSBORDADO:
        print(
            f"  decisao : TRANSBORDADO -> {resultado.ticket_id} "
            f"(motivo: {resultado.motivo_transbordo})"
        )
        print("  resposta: (nao enviada ao cliente — atendimento humano)")
    else:
        print(f"  decisao : {resultado.status.value}")
        print(f"  resposta: {_encurtar(resultado.resposta_gerada or '')}")


def rodar_arquivo(
    arquivo: str | Path, output_dir: str | Path | None = None
) -> ResultadoAtendimento:
    caminho = Path(arquivo)
    if not caminho.is_absolute():
        candidatos = [Path.cwd() / caminho, ROOT / caminho]
        caminho = next((c for c in candidatos if c.exists()), caminho)
    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo de caso não encontrado: {arquivo}")

    payload = json.loads(caminho.read_text(encoding="utf-8"))
    resultado = processar_mensagem(payload, output_dir=output_dir)
    _imprimir_resultado(caminho, resultado)
    return resultado


def rodar_todos(output_dir: str | Path | None = None) -> list[ResultadoAtendimento]:
    arquivos = sorted(CASES_DIR.glob("case_*.json"))
    if not arquivos:
        raise FileNotFoundError(f"Nenhum caso encontrado em {CASES_DIR}")

    destino = Path(output_dir) if output_dir is not None else config.diretorio_saida()
    limpar_saidas(destino)
    print("=" * 66)
    print(" AURORA DIGITAL - ATENDIMENTO AUTOMATICO (WhatsApp simulado)")
    print("=" * 66)

    resultados: list[ResultadoAtendimento] = []
    for arquivo in arquivos:
        resultados.append(rodar_arquivo(arquivo, output_dir=destino))

    transbordados = sum(1 for r in resultados if r.status == Status.TRANSBORDADO)
    print("\n" + "-" * 66)
    print(f"Casos processados : {len(resultados)}")
    print(f"Respondidos auto  : {len(resultados) - transbordados}")
    print(f"Transbordados     : {transbordados}")
    print(f"CSV               : {destino / recorder.CSV_FILENAME}")
    print(f"Tickets           : {len(list(destino.glob('tickets/TKT-*.json')))} arquivo(s)")
    print(
        "Webhooks          : "
        f"{len(list(destino.glob('webhook_payloads/*.json')))} arquivo(s)"
    )
    return resultados


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Atendimento automático Lovel Digital (WhatsApp simulado)."
    )
    parser.add_argument(
        "arquivo",
        nargs="?",
        help="JSON de mensagem simulada (ex.: data/simulated_messages/case_01_simples.json)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Processa todos os casos de data/simulated_messages/ e limpa outputs/ antes.",
    )
    args = parser.parse_args(argv)

    if not args.all and not args.arquivo:
        parser.error("informe um arquivo de caso ou use --all")
    if args.all and args.arquivo:
        parser.error("use --all ou um arquivo, não ambos")

    if args.all:
        rodar_todos()
    else:
        rodar_arquivo(args.arquivo)
        print(f"\nCSV: {config.diretorio_saida() / recorder.CSV_FILENAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
