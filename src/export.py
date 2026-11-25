"""Exportação de atendimentos em CSV e XML.

Lê ``outputs/demo/atendimentos.csv`` (gerado pela demo web) e converte o
conteúdo para CSV (idêntico, com cabeçalho) e para XML estruturado.
"""

from __future__ import annotations

import csv
import html
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

from .recorder import CSV_FILENAME

DEMO_DIR = Path(__file__).resolve().parents[1] / "outputs" / "demo"


def ler_atendimentos(demo_dir: str | Path | None = None) -> list[dict]:
    """Lê todas as linhas de atendimentos.csv como lista de dicionários."""
    diretorio = Path(demo_dir) if demo_dir else DEMO_DIR
    caminho = diretorio / CSV_FILENAME
    if not caminho.exists():
        return []
    with caminho.open(encoding="utf-8", newline="") as arquivo:
        return list(csv.DictReader(arquivo))


def exportar_csv(dados: list[dict]) -> str:
    """Gera o texto CSV a partir dos dicionários de atendimento."""
    campos = [
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
    linhas = [";".join(campos)]
    for item in dados:
        valores = [html.escape(str(item.get(campo, "") or "")) for campo in campos]
        linhas.append(";".join(valores))
    return "\n".join(linhas) + "\n"


def _elemento_texto(pai: ET.Element, tag: str, valor: str | None) -> None:
    filho = ET.SubElement(pai, tag)
    filho.text = valor if valor is not None else ""


def exportar_xml(dados: list[dict]) -> str:
    """Gera o texto XML a partir dos dicionários de atendimento."""
    raiz = ET.Element("atendimentos")
    raiz.set("total", str(len(dados)))

    for item in dados:
        atendimento = ET.SubElement(raiz, "atendimento")
        _elemento_texto(atendimento, "timestamp", item.get("timestamp"))
        _elemento_texto(atendimento, "cliente_id", item.get("cliente_id"))
        _elemento_texto(atendimento, "nome", item.get("nome"))
        _elemento_texto(atendimento, "intencao", item.get("intencao"))
        _elemento_texto(atendimento, "urgencia", item.get("urgencia"))
        _elemento_texto(atendimento, "status", item.get("status"))
        _elemento_texto(atendimento, "resposta_gerada", item.get("resposta_gerada"))
        _elemento_texto(atendimento, "ticket_id", item.get("ticket_id"))
        _elemento_texto(
            atendimento, "motivo_transbordo", item.get("motivo_transbordo")
        )

    bruto = ET.tostring(raiz, encoding="unicode")
    return minidom.parseString(bruto).toprettyxml(indent="  ", encoding=None)


def caminho_csv_demo(demo_dir: str | Path | None = None) -> Path:
    """Caminho do CSV da demo."""
    return (Path(demo_dir) if demo_dir else DEMO_DIR) / CSV_FILENAME
