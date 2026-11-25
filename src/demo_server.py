"""Demo web do fluxo de atendimento (sem dependências externas no modo local).

Sobe uma UI em http://127.0.0.1:8000 que simula o WhatsApp, executa o mesmo
``processar_mensagem`` do CLI e permite configurar todo o fluxo (empresa,
modelo, limiar de busca, regras de transbordo, prazos e registros).

O roteamento HTTP fica em :func:`responder`, independente do servidor. Assim o
mesmo código atende o ``http.server`` local e o adaptador WSGI usado no Vercel
(``app.py``), sem duplicar regra de negócio.

Os artefatos da demo vão para ``outputs/demo/`` (ou para o diretório indicado
por ``LOVEL_DEMO_DIR``) para não alterar os arquivos canônicos de ``outputs/``.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import re
import sys
import threading
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import config as config_mod
from . import export, knowledge_base, llm_client
from .main import CASES_DIR, limpar_saidas, processar_mensagem

DEMO_DIR = Path(
    os.getenv("LOVEL_DEMO_DIR") or (config_mod.ROOT / "outputs" / "demo")
).expanduser()
UI_PATH = Path(__file__).with_name("demo_ui.html")
ASSETS_DIR = config_mod.ROOT / "assets"
LOCK = threading.RLock()

TIPOS_ASSET = {
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
}


# --------------------------------------------------------------------------
# estado
# --------------------------------------------------------------------------


def _ler_csv() -> list[dict]:
    caminho = DEMO_DIR / "atendimentos.csv"
    if not caminho.exists():
        return []
    with caminho.open(encoding="utf-8", newline="") as arquivo:
        return list(csv.DictReader(arquivo))


def _estado() -> dict:
    return {
        "rows": _ler_csv(),
        "tickets": len(list((DEMO_DIR / "tickets").glob("TKT-*.json"))),
        "webhooks": len(list((DEMO_DIR / "webhook_payloads").glob("*.json"))),
    }


def _casos() -> list[dict]:
    casos = []
    for arquivo in sorted(CASES_DIR.glob("case_*.json")):
        payload = json.loads(arquivo.read_text(encoding="utf-8"))
        casos.append({"arquivo": arquivo.name, **payload})
    return casos


def _exibir(caminho: Path) -> str:
    """Caminho relativo à raiz quando possível (mais legível na UI)."""
    try:
        return str(caminho.relative_to(config_mod.ROOT))
    except ValueError:
        return str(caminho)


def _meta() -> dict:
    return {
        "config_path": _exibir(config_mod.caminho()),
        "kb_path": _exibir(knowledge_base.caminho_base()),
        "demo_dir": _exibir(DEMO_DIR),
        "saida_dir": _exibir(config_mod.diretorio_saida()),
        "cases_dir": _exibir(CASES_DIR),
    }


def _bootstrap() -> dict:
    return {
        "config": config_mod.carregar(),
        "padroes": copy.deepcopy(config_mod.PADROES),
        "kb": knowledge_base.carregar_base(),
        "cases": _casos(),
        "llm": llm_client.status_llm(),
        "meta": _meta(),
        **_estado(),
    }


# --------------------------------------------------------------------------
# execução
# --------------------------------------------------------------------------


def _serializar(resultado) -> dict:
    return {
        "mensagem": asdict(resultado.mensagem),
        "classificacao": {
            "intencao": resultado.classificacao.intencao.value,
            "urgencia": resultado.classificacao.urgencia.value,
            "justificativa": resultado.classificacao.justificativa,
        },
        "status": resultado.status.value,
        "resposta_gerada": resultado.resposta_gerada,
        "ticket_id": resultado.ticket_id,
        "motivo_transbordo": resultado.motivo_transbordo,
    }


def _executar(payload: dict) -> dict:
    resultado = processar_mensagem(payload, output_dir=DEMO_DIR)
    trechos = knowledge_base.buscar_faqs(payload["mensagem"])

    ticket = None
    if resultado.ticket_id:
        caminho = DEMO_DIR / "tickets" / f"{resultado.ticket_id}.json"
        if caminho.exists():
            ticket = json.loads(caminho.read_text(encoding="utf-8"))

    return {
        "resultado": _serializar(resultado),
        "trechos": trechos,
        "ticket": ticket,
    }


# --------------------------------------------------------------------------
# roteamento (comum ao servidor local e ao Vercel)
# --------------------------------------------------------------------------


def _json(payload: dict, status: int = 200) -> tuple[int, dict[str, str], bytes]:
    corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return status, {"Content-Type": "application/json; charset=utf-8"}, corpo


def _asset(nome: str) -> tuple[int, dict[str, str], bytes]:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", nome):
        return _json({"error": "Arquivo não encontrado."}, 404)
    arquivo = ASSETS_DIR / nome
    if not arquivo.is_file():
        return _json({"error": "Arquivo não encontrado."}, 404)
    tipo = TIPOS_ASSET.get(arquivo.suffix.lower(), "application/octet-stream")
    return (
        200,
        {"Content-Type": tipo, "Cache-Control": "public, max-age=3600"},
        arquivo.read_bytes(),
    )


def _json_do_corpo(corpo: bytes) -> dict:
    if not corpo:
        raise ValueError("Corpo da requisição vazio.")
    if len(corpo) > 1_000_000:
        raise ValueError("Payload muito grande.")
    try:
        dados = json.loads(corpo.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("JSON inválido no corpo da requisição.") from exc
    if not isinstance(dados, dict):
        raise ValueError("O payload deve ser um objeto JSON.")
    return dados


def _get(rota: str) -> tuple[int, dict[str, str], bytes]:
    if rota in ("/", "/index.html"):
        if not UI_PATH.exists():
            return _json({"error": "Arquivo demo_ui.html não encontrado."}, 500)
        return (
            200,
            {"Content-Type": "text/html; charset=utf-8"},
            UI_PATH.read_bytes(),
        )
    if rota == "/favicon.ico":
        return _asset("favicon.png")
    if rota.startswith("/assets/"):
        return _asset(rota[len("/assets/") :])
    if rota == "/api/bootstrap":
        with LOCK:
            return _json(_bootstrap())
    if rota == "/api/cases":
        with LOCK:
            return _json({"cases": _casos(), "llm": llm_client.status_llm(), **_estado()})
    if rota == "/api/state":
        with LOCK:
            return _json({"llm": llm_client.status_llm(), **_estado()})
    if rota == "/api/kb":
        with LOCK:
            return _json({"kb": knowledge_base.carregar_base()})
    if rota == "/api/config":
        with LOCK:
            return _json({"config": config_mod.carregar()})
    if rota == "/api/export/csv":
        with LOCK:
            dados = export.ler_atendimentos(DEMO_DIR)
            corpo = export.exportar_csv(dados).encode("utf-8")
        return (
            200,
            {
                "Content-Type": "text/csv; charset=utf-8",
                "Content-Disposition": 'attachment; filename="atendimentos.csv"',
            },
            corpo,
        )
    if rota == "/api/export/xml":
        with LOCK:
            dados = export.ler_atendimentos(DEMO_DIR)
            corpo = export.exportar_xml(dados).encode("utf-8")
        return (
            200,
            {
                "Content-Type": "application/xml; charset=utf-8",
                "Content-Disposition": 'attachment; filename="atendimentos.xml"',
            },
            corpo,
        )
    return _json({"error": "Rota não encontrada."}, 404)


def _post(rota: str, corpo: bytes) -> tuple[int, dict[str, str], bytes]:
    if rota == "/api/process":
        payload = _json_do_corpo(corpo)
        with LOCK:
            item = _executar(payload)
            estado = _estado()
        return _json({**item, **estado, "llm": llm_client.status_llm()})

    if rota == "/api/run-all":
        casos = _casos()
        with LOCK:
            limpar_saidas(DEMO_DIR)
            itens = [_executar(caso) for caso in casos]
            estado = _estado()
        return _json(
            {
                "resultados": itens,
                "total": len(itens),
                "llm": llm_client.status_llm(),
                **estado,
            }
        )

    if rota == "/api/reset":
        with LOCK:
            limpar_saidas(DEMO_DIR)
            estado = _estado()
        return _json({"llm": llm_client.status_llm(), **estado})

    if rota == "/api/config":
        parcial = _json_do_corpo(corpo)
        with LOCK:
            atual = config_mod.carregar()
            novo = config_mod.validar(config_mod.mesclar(atual, parcial))
            config_mod.salvar(novo)
        return _json(
            {"config": novo, "llm": llm_client.status_llm(), "meta": _meta()}
        )

    if rota == "/api/config/reset":
        with LOCK:
            novo = config_mod.restaurar()
        return _json(
            {"config": novo, "llm": llm_client.status_llm(), "meta": _meta()}
        )

    if rota == "/api/kb":
        dados = _json_do_corpo(corpo)
        base = dados["kb"] if isinstance(dados.get("kb"), dict) else dados
        with LOCK:
            knowledge_base.salvar_base(base)
            limpa = knowledge_base.carregar_base()
        return _json({"kb": limpa})

    if rota == "/api/kb/test":
        payload = _json_do_corpo(corpo)
        mensagem = str(payload.get("mensagem") or "").strip()
        if not mensagem:
            raise ValueError("Digite uma mensagem para testar.")
        with LOCK:
            diagnostico = knowledge_base.pontuar_faqs(mensagem)
        return _json(diagnostico)

    return _json({"error": "Rota não encontrada."}, 404)


def responder(
    metodo: str, caminho: str, corpo: bytes = b""
) -> tuple[int, dict[str, str], bytes]:
    """Resolve uma requisição HTTP e devolve ``(status, headers, corpo)``.

    É a única camada de roteamento: o servidor local e o Vercel chamam esta
    função, então as duas execuções têm exatamente as mesmas rotas e regras.
    """
    rota = urlparse(caminho).path or "/"
    try:
        if metodo == "GET":
            status, headers, dados = _get(rota)
        elif metodo == "POST":
            status, headers, dados = _post(rota, corpo)
        else:
            status, headers, dados = _json({"error": "Método não suportado."}, 405)
    except (config_mod.ConfigError, knowledge_base.BaseInvalida, ValueError) as exc:
        status, headers, dados = _json({"error": str(exc)}, 400)
    except Exception as exc:  # demo: devolve a mensagem para facilitar o debug
        status, headers, dados = _json({"error": f"Erro interno: {exc}"}, 500)

    headers.setdefault("Cache-Control", "no-store")
    return status, headers, dados


# --------------------------------------------------------------------------
# servidor local (stdlib)
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "LovelDemo/2.0"
    protocol_version = "HTTP/1.1"

    def _enviar(self) -> None:
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho > 0 else b""
        status, headers, dados = responder(self.command, self.path, corpo)
        self.send_response(status)
        for chave, valor in headers.items():
            self.send_header(chave, valor)
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self) -> None:  # noqa: N802 (API do http.server)
        self._enviar()

    def do_POST(self) -> None:  # noqa: N802 (API do http.server)
        self._enviar()

    def log_message(self, formato: str, *args) -> None:
        print(f"[demo] {self.address_string()} - {formato % args}")


class DemoHTTPServer(ThreadingHTTPServer):
    """Servidor da demo.

    O keep-alive do navegador fecha conexões sem avisar; o ``socketserver``
    trataria isso como erro e imprimiria um traceback a cada aba fechada.
    Aqui esse caso (e só ele) é silenciado: a demo continua e o console fica
    legível.
    """

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        erro = sys.exc_info()[1]
        if isinstance(erro, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def main(argv: list[str] | None = None) -> int:
    cfg_demo = config_mod.carregar()["demo"]
    parser = argparse.ArgumentParser(description="Demo web do atendimento Lovel Digital.")
    parser.add_argument("--host", default=cfg_demo["host"], help="Host (padrão: %(default)s)")
    parser.add_argument(
        "--port", type=int, default=cfg_demo["port"], help="Porta (padrão: %(default)s)"
    )
    parser.add_argument("--open", action="store_true", help="Abre o navegador automaticamente")
    args = parser.parse_args(argv)

    url = f"http://{args.host}:{args.port}"
    try:
        servidor = DemoHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"Não foi possível abrir {url}: {exc}")
        print("Dica: use --port 8001 (ou outra porta livre).")
        return 1

    status = llm_client.status_llm()
    modo = "mock (offline)" if status["mock"] else f"OpenAI {status['model']}"
    print("=" * 64)
    print(" LOVEL DIGITAL - DEMO DE ATENDIMENTO")
    print("=" * 64)
    print(f" URL          : {url}")
    print(f" LLM          : {modo}")
    print(f" Config       : {config_mod.caminho()}")
    print(f" Base         : {knowledge_base.caminho_base()}")
    print(f" Artefatos    : {DEMO_DIR}")
    print(" Encerrar     : Ctrl+C")
    print("=" * 64)

    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando demo...")
    finally:
        servidor.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
