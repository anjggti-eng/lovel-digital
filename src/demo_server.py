"""Demo web local do fluxo de atendimento (sem dependências externas).

Sobe uma UI em http://127.0.0.1:8000 que simula o WhatsApp, executa o mesmo
``processar_mensagem`` do CLI e permite configurar todo o fluxo (empresa,
modelo, limiar de busca, regras de transbordo, prazos e registros).

Os artefatos da demo vão para ``outputs/demo/`` para não alterar os arquivos
canônicos de ``outputs/`` (CSV, tickets e webhooks da entrega).
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
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

DEMO_DIR = config_mod.ROOT / "outputs" / "demo"
UI_PATH = Path(__file__).with_name("demo_ui.html")
LOCK = threading.RLock()


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


def _meta() -> dict:
    return {
        "config_path": str(config_mod.caminho()),
        "kb_path": str(knowledge_base.caminho_base()),
        "demo_dir": str(DEMO_DIR),
        "saida_dir": str(config_mod.diretorio_saida()),
        "cases_dir": str(CASES_DIR),
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
# HTTP
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "LovelDemo/2.0"
    protocol_version = "HTTP/1.1"

    # ---------- helpers de resposta ----------

    def _send_bytes(
        self,
        corpo: bytes,
        content_type: str,
        status: int = 200,
        headers_extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        if headers_extra:
            for chave, valor in headers_extra.items():
                self.send_header(chave, valor)
        self.end_headers()
        self.wfile.write(corpo)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(corpo, "application/json; charset=utf-8", status)

    def _send_html(self) -> None:
        if not UI_PATH.exists():
            self._send_json({"error": "Arquivo demo_ui.html não encontrado."}, 500)
            return
        self._send_bytes(UI_PATH.read_bytes(), "text/html; charset=utf-8")

    def _ler_json(self) -> dict:
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho <= 0:
            raise ValueError("Corpo da requisição vazio.")
        if tamanho > 1_000_000:
            raise ValueError("Payload muito grande.")
        bruto = self.rfile.read(tamanho)
        try:
            dados = json.loads(bruto.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON inválido no corpo da requisição.") from exc
        if not isinstance(dados, dict):
            raise ValueError("O payload deve ser um objeto JSON.")
        return dados

    # ---------- rotas ----------

    def do_GET(self) -> None:  # noqa: N802 (API do http.server)
        rota = urlparse(self.path).path
        try:
            if rota in ("/", "/index.html"):
                self._send_html()
                return
            if rota == "/api/bootstrap":
                with LOCK:
                    self._send_json(_bootstrap())
                return
            if rota == "/api/cases":
                with LOCK:
                    self._send_json(
                        {"cases": _casos(), "llm": llm_client.status_llm(), **_estado()}
                    )
                return
            if rota == "/api/state":
                with LOCK:
                    self._send_json({"llm": llm_client.status_llm(), **_estado()})
                return
            if rota == "/api/kb":
                with LOCK:
                    self._send_json({"kb": knowledge_base.carregar_base()})
                return
            if rota == "/api/config":
                with LOCK:
                    self._send_json({"config": config_mod.carregar()})
                return
            if rota == "/api/export/csv":
                with LOCK:
                    dados = export.ler_atendimentos(DEMO_DIR)
                    corpo = export.exportar_csv(dados).encode("utf-8")
                self._send_bytes(
                    corpo,
                    "text/csv; charset=utf-8",
                    headers_extra={"Content-Disposition": 'attachment; filename="atendimentos.csv"'},
                )
                return
            if rota == "/api/export/xml":
                with LOCK:
                    dados = export.ler_atendimentos(DEMO_DIR)
                    corpo = export.exportar_xml(dados).encode("utf-8")
                self._send_bytes(
                    corpo,
                    "application/xml; charset=utf-8",
                    headers_extra={"Content-Disposition": 'attachment; filename="atendimentos.xml"'},
                )
                return
            self._send_json({"error": "Rota não encontrada."}, 404)
        except (config_mod.ConfigError, knowledge_base.BaseInvalida) as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self._send_json({"error": f"Erro interno: {exc}"}, 500)

    def do_POST(self) -> None:  # noqa: N802 (API do http.server)
        rota = urlparse(self.path).path
        try:
            if rota == "/api/process":
                payload = self._ler_json()
                with LOCK:
                    item = _executar(payload)
                    estado = _estado()
                self._send_json({**item, **estado, "llm": llm_client.status_llm()})
                return

            if rota == "/api/run-all":
                casos = _casos()
                with LOCK:
                    limpar_saidas(DEMO_DIR)
                    itens = [_executar(caso) for caso in casos]
                    estado = _estado()
                self._send_json(
                    {
                        "resultados": itens,
                        "total": len(itens),
                        "llm": llm_client.status_llm(),
                        **estado,
                    }
                )
                return

            if rota == "/api/reset":
                with LOCK:
                    limpar_saidas(DEMO_DIR)
                    estado = _estado()
                self._send_json({"llm": llm_client.status_llm(), **estado})
                return

            if rota == "/api/config":
                parcial = self._ler_json()
                with LOCK:
                    atual = config_mod.carregar()
                    novo = config_mod.validar(config_mod.mesclar(atual, parcial))
                    config_mod.salvar(novo)
                self._send_json(
                    {
                        "config": novo,
                        "llm": llm_client.status_llm(),
                        "meta": _meta(),
                    }
                )
                return

            if rota == "/api/config/reset":
                with LOCK:
                    novo = config_mod.restaurar()
                self._send_json(
                    {
                        "config": novo,
                        "llm": llm_client.status_llm(),
                        "meta": _meta(),
                    }
                )
                return

            if rota == "/api/kb":
                dados = self._ler_json()
                base = dados["kb"] if isinstance(dados.get("kb"), dict) else dados
                with LOCK:
                    knowledge_base.salvar_base(base)
                    limpa = knowledge_base.carregar_base()
                self._send_json({"kb": limpa})
                return

            if rota == "/api/kb/test":
                payload = self._ler_json()
                mensagem = str(payload.get("mensagem") or "").strip()
                if not mensagem:
                    raise ValueError("Digite uma mensagem para testar.")
                with LOCK:
                    diagnostico = knowledge_base.pontuar_faqs(mensagem)
                self._send_json(diagnostico)
                return

            self._send_json({"error": "Rota não encontrada."}, 404)
        except (config_mod.ConfigError, knowledge_base.BaseInvalida, ValueError) as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:  # demo local: devolve a mensagem para facilitar o debug
            self._send_json({"error": f"Erro interno: {exc}"}, 500)

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
    modo = f"mock (offline)" if status["mock"] else f"OpenAI {status['model']}"
    print("=" * 64)
    print(" AURORA DIGITAL - DEMO DE ATENDIMENTO")
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
