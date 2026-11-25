"""Adaptador WSGI (Flask) para rodar a demo no Vercel.

O demo local continua sendo ``python -m src.demo_server``. Aqui apenas
reaproveitamos o mesmo roteador (:func:`src.demo_server.responder`), sem
duplicar nenhuma regra de atendimento.

No Vercel o disco do projeto é somente-leitura; por isso a configuração, a base
e os artefatos são copiados para ``/tmp`` na primeira requisição de cada
instância. Esses dados são efêmeros: reiniciam quando a instância é reciclada.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from flask import Flask, Response, request

ROOT = Path(__file__).resolve().parent


def _preparar_ambiente() -> None:
    """Aponta config/base/saídas para uma cópia gravável em ``/tmp``."""
    if os.getenv("VERCEL") != "1":
        return

    base = Path(tempfile.gettempdir()) / "lovel-digital"
    base.mkdir(parents=True, exist_ok=True)

    config = base / "lovel.json"
    kb = base / "knowledge_base.json"
    if not config.exists():
        shutil.copyfile(ROOT / "config" / "lovel.json", config)
    if not kb.exists():
        shutil.copyfile(ROOT / "data" / "knowledge_base.json", kb)

    os.environ.setdefault("LOVEL_CONFIG", str(config))
    os.environ.setdefault("LOVEL_KB", str(kb))
    os.environ.setdefault("LOVEL_DEMO_DIR", str(base / "outputs" / "demo"))
    os.environ.setdefault("LOVEL_OUTPUT_DIR", str(base / "outputs"))
    # Demo pública: sem chave configurada, o mock é o padrão seguro.
    os.environ.setdefault("MOCK_LLM", "1")


_preparar_ambiente()

from src.demo_server import responder  # noqa: E402  (precisa vir após o ambiente)

app = Flask(__name__)

METODOS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


@app.route("/", defaults={"caminho": ""}, methods=METODOS)
@app.route("/<path:caminho>", methods=METODOS)
def lovel(caminho: str) -> Response:
    rota = f"/{caminho}" if caminho else "/"
    status, headers, corpo = responder(request.method, rota, request.get_data())
    return Response(corpo, status=status, headers=headers)
