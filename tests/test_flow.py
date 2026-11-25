"""Testes end-to-end do fluxo de atendimento (7 casos + 1 agregado)."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from src.main import processar_mensagem
from src.models import Intencao, Status, Urgencia

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "data" / "simulated_messages"

try:
    from src.export import ler_atendimentos, exportar_csv, exportar_xml
except ImportError:  # pragma: no cover - depende do src estar no path
    ler_atendimentos = None  # type: ignore
    exportar_csv = None  # type: ignore
    exportar_xml = None  # type: ignore


def carregar(relativo: str) -> dict:
    return json.loads((ROOT / relativo).read_text(encoding="utf-8"))


def ler_ticket(tmp_path: Path, ticket_id: str) -> dict:
    return json.loads(
        (tmp_path / "tickets" / f"{ticket_id}.json").read_text(encoding="utf-8")
    )


@pytest.fixture(autouse=True)
def modo_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Força o mock determinístico: nenhum teste depende de rede."""
    monkeypatch.setenv("MOCK_LLM", "1")


def test_case_01_simples_responde_automatico(tmp_path: Path) -> None:
    resultado = processar_mensagem(
        carregar("data/simulated_messages/case_01_simples.json"),
        output_dir=tmp_path,
    )

    assert resultado.status == Status.RESPONDIDO_AUTOMATICO
    assert resultado.classificacao.intencao == Intencao.DUVIDA_COMERCIAL
    assert "dias úteis" in (resultado.resposta_gerada or "").lower()
    assert resultado.ticket_id is None

    assert (tmp_path / "atendimentos.csv").exists()
    assert len(list((tmp_path / "webhook_payloads").glob("*.json"))) == 1


def test_case_02_fora_da_base_transborda(tmp_path: Path) -> None:
    resultado = processar_mensagem(
        carregar("data/simulated_messages/case_02_fora_da_base.json"),
        output_dir=tmp_path,
    )

    assert resultado.status == Status.TRANSBORDADO
    assert resultado.motivo_transbordo == "nao_coberto_pela_base"
    assert resultado.resposta_gerada is None
    assert resultado.ticket_id is not None
    assert re.fullmatch(r"TKT-\d{8}-\d{3}", resultado.ticket_id)

    ticket = ler_ticket(tmp_path, resultado.ticket_id)
    assert ticket["status"] == "ABERTO"
    assert ticket["motivo_transbordo"] == "nao_coberto_pela_base"
    assert ticket["mensagem_original"] == (
        "Vocês vendem cartucho de tinta para impressora Epson?"
    )


def test_case_03_irritado_reclamacao_critica(tmp_path: Path) -> None:
    resultado = processar_mensagem(
        carregar("data/simulated_messages/case_03_irritado.json"),
        output_dir=tmp_path,
    )

    assert resultado.classificacao.intencao == Intencao.RECLAMACAO_CRITICA
    assert resultado.status == Status.TRANSBORDADO
    assert resultado.motivo_transbordo == "reclamacao_critica"
    assert resultado.ticket_id is not None

    ticket = ler_ticket(tmp_path, resultado.ticket_id)
    assert "supervisor" in ticket["resumo_estruturado"].lower()


def test_case_04_ambiguo_nao_coberto(tmp_path: Path) -> None:
    resultado = processar_mensagem(
        carregar("data/simulated_messages/case_04_ambiguo.json"),
        output_dir=tmp_path,
    )

    assert resultado.classificacao.intencao == Intencao.FORA_DE_ESCOPO
    assert resultado.classificacao.urgencia == Urgencia.MEDIA
    assert resultado.status == Status.TRANSBORDADO
    assert resultado.motivo_transbordo == "nao_coberto_pela_base"
    assert resultado.ticket_id is not None


def test_case_05_reembolso_critico_transborda(tmp_path: Path) -> None:
    resultado = processar_mensagem(
        carregar("data/simulated_messages/case_05_reembolso_critico.json"),
        output_dir=tmp_path,
    )

    assert resultado.status == Status.TRANSBORDADO
    assert resultado.motivo_transbordo in {
        "cancelamento_ou_reembolso_critico",
        "reclamacao_critica",
    }
    assert resultado.ticket_id is not None

    ticket = ler_ticket(tmp_path, resultado.ticket_id)
    assert ticket["classificacao"]["urgencia"] in {"alta", "critica"}
    assert "procon" in ticket["mensagem_original"].lower()


def test_case_06_suporte_tecnico_responde(tmp_path: Path) -> None:
    resultado = processar_mensagem(
        carregar("data/simulated_messages/case_06_suporte_tecnico.json"),
        output_dir=tmp_path,
    )

    assert resultado.classificacao.intencao == Intencao.SUPORTE_TECNICO
    assert resultado.status == Status.RESPONDIDO_AUTOMATICO
    assert "Esqueci minha senha" in (resultado.resposta_gerada or "")
    assert resultado.ticket_id is None


def test_case_07_urgencia_alta_coberta_responde(tmp_path: Path) -> None:
    resultado = processar_mensagem(
        carregar("data/simulated_messages/case_07_urgencia_alta_coberta.json"),
        output_dir=tmp_path,
    )

    assert resultado.status == Status.RESPONDIDO_AUTOMATICO
    assert "dias úteis" in (resultado.resposta_gerada or "").lower()
    assert resultado.ticket_id is None
    assert resultado.motivo_transbordo is None


def test_gera_todos_outputs(tmp_path: Path) -> None:
    arquivos = sorted(CASES_DIR.glob("case_*.json"))
    assert len(arquivos) == 7

    resultados = [
        processar_mensagem(
            json.loads(arquivo.read_text(encoding="utf-8")),
            output_dir=tmp_path,
        )
        for arquivo in arquivos
    ]

    # CSV: cabeçalho + 7 linhas, com exatamente 9 colunas.
    with (tmp_path / "atendimentos.csv").open(encoding="utf-8", newline="") as arquivo:
        linhas = list(csv.reader(arquivo))
    assert len(linhas) == 8
    assert linhas[0] == [
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
    assert all(len(linha) == 9 for linha in linhas)

    # Tickets apenas para os casos 02, 03, 04 e 05.
    tickets = sorted((tmp_path / "tickets").glob("TKT-*.json"))
    assert len(tickets) == 4
    ids_transbordados = {r.mensagem.cliente_id for r in resultados if r.ticket_id}
    assert ids_transbordados == {"cli_002", "cli_003", "cli_004", "cli_005"}

    # Um webhook por atendimento, todos com o evento esperado.
    webhooks = sorted((tmp_path / "webhook_payloads").glob("*.json"))
    assert len(webhooks) == 7
    for caminho in webhooks:
        payload = json.loads(caminho.read_text(encoding="utf-8"))
        assert payload["event"] == "atendimento.processado"
        assert payload["status"] in {
            Status.RESPONDIDO_AUTOMATICO.value,
            Status.TRANSBORDADO.value,
        }


def test_saudacao_oi_responde_automatico(tmp_path: Path) -> None:
    """Mensagem exatamente 'oi' responde automaticamente, sem transbordo."""
    resultado = processar_mensagem(
        {
            "cliente_id": "cli_saudacao",
            "nome": "Cliente",
            "telefone": "+55 11 99999-0000",
            "mensagem": "oi",
            "timestamp": "2026-01-01T10:00:00Z",
        },
        output_dir=tmp_path,
    )

    assert resultado.classificacao.intencao == Intencao.SAUDACAO
    assert resultado.classificacao.urgencia == Urgencia.BAIXA
    assert resultado.status == Status.RESPONDIDO_AUTOMATICO
    assert resultado.resposta_gerada == "Olá! Como posso te ajudar hoje?"
    assert resultado.ticket_id is None
    assert resultado.motivo_transbordo is None

    # Registro normal: CSV linha + webhook.
    assert (tmp_path / "atendimentos.csv").exists()
    assert len(list((tmp_path / "webhook_payloads").glob("*.json"))) == 1


def test_saudacao_ola_responde_automatico(tmp_path: Path) -> None:
    """Mensagem exatamente 'ola' responde automaticamente, sem transbordo."""
    resultado = processar_mensagem(
        {
            "cliente_id": "cli_saudacao_ola",
            "nome": "Cliente",
            "telefone": "+55 11 99999-0000",
            "mensagem": "ola",
            "timestamp": "2026-01-01T10:00:00Z",
        },
        output_dir=tmp_path,
    )

    assert resultado.classificacao.intencao == Intencao.SAUDACAO
    assert resultado.status == Status.RESPONDIDO_AUTOMATICO
    assert resultado.resposta_gerada == "Olá! Como posso te ajudar hoje?"
    assert resultado.ticket_id is None


def test_export_csv_gera_cabecalho_e_linhas(tmp_path: Path) -> None:
    """Exportação CSV produz cabeçalho e uma linha por atendimento."""
    if ler_atendimentos is None:
        pytest.skip("src.export não pôde ser importado")

    processar_mensagem(
        {
            "cliente_id": "cli_export",
            "nome": "Export Teste",
            "telefone": "+55 11 99999-0000",
            "mensagem": "oi",
            "timestamp": "2026-01-01T10:00:00Z",
        },
        output_dir=tmp_path,
    )

    dados = ler_atendimentos(tmp_path)
    assert len(dados) == 1
    assert dados[0]["nome"] == "Export Teste"
    assert dados[0]["intencao"] == "saudacao"

    texto_csv = exportar_csv(dados)
    linhas = texto_csv.strip().split("\n")
    assert linhas[0].split(";") == [
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
    assert len(linhas) == 2  # cabeçalho + 1 linha


def test_export_xml_estruturado(tmp_path: Path) -> None:
    """Exportação XML produz raiz <atendimentos> com filhos <atendimento>."""
    if ler_atendimentos is None or exportar_xml is None:
        pytest.skip("src.export não pôde ser importado")

    processar_mensagem(
        {
            "cliente_id": "cli_xml",
            "nome": "XML Teste",
            "telefone": "+55 11 99999-0000",
            "mensagem": "ola",
            "timestamp": "2026-01-01T10:00:00Z",
        },
        output_dir=tmp_path,
    )

    dados = ler_atendimentos(tmp_path)
    texto_xml = exportar_xml(dados)

    assert texto_xml.startswith("<?xml")
    assert "<atendimentos total=\"1\">" in texto_xml
    assert "<nome>XML Teste</nome>" in texto_xml
    assert "<intencao>saudacao</intencao>" in texto_xml
    assert "<status>RESPONDIDO_AUTOMATICO</status>" in texto_xml
