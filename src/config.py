"""Configuração única do projeto, em ``config/lovel.json``.

A UI de Configurações e o CLI leem o mesmo arquivo. Nada de constante
espalhada: modelo, limiar de busca, regras de transbordo, prazos, empresa e
diretório de saída vivem aqui.

Precedência (da maior para a menor):

1. ``MOCK_LLM=1`` no ambiente (força o modo offline).
2. ``LOVEL_CONFIG=<caminho>`` no ambiente (arquivo alternativo).
3. ``config/lovel.json``.
4. ``PADROES`` (usado quando o arquivo não existe ou uma chave está faltando).

O arquivo é escrito de forma atômica (tmp + replace) para nunca ficar pela
metade se algo falhar no meio do save.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CAMINHO_PADRAO = ROOT / "config" / "lovel.json"

URGENCIAS = ("critica", "alta", "media", "baixa")
MODOS_LLM = ("auto", "mock", "real")

PADROES: dict[str, Any] = {
    "empresa": {
        "nome": "Lovel Digital",
        "horario_atendimento": "Segunda a sexta, 9h às 18h (horário de Brasília).",
    },
    "llm": {
        # auto = usa a OpenAI quando OPENAI_API_KEY existir; senão, mock.
        "modo": "auto",
        "model": "gpt-4o-mini",
        "temperature": 0.0,
        "max_tokens_classificacao": 200,
        "max_tokens_resposta": 300,
    },
    "base": {
        "arquivo": "data/knowledge_base.json",
        "limiar": 0.10,
        "top_k": 3,
    },
    "transbordo": {
        "reclamacao_critica": True,
        "termos_criticos_urgentes": True,
        "sem_cobertura": True,
        "fora_de_escopo_urgente": True,
        "termos_criticos": [
            "cancelar",
            "cancelamento",
            "reembolso",
            "estorno",
            "dinheiro de volta",
            "processo",
            "procon",
            "advogado",
            "jurídico",
            "processar",
            "reclamação no procon",
        ],
        "prazos": {
            "critica": "Contato humano em até 1 hora útil.",
            "alta": "Contato humano em até 4 horas úteis.",
            "media": "Contato humano em até 1 dia útil.",
            "baixa": "Contato humano em até 2 dias úteis.",
        },
    },
    "registro": {
        "csv": True,
        "tickets": True,
        "webhook": True,
        "diretorio": "outputs",
    },
    "demo": {
        "host": "127.0.0.1",
        "port": 8000,
    },
    "whatsapp": {
        "enabled": False,
        "api_url": "https://graph.facebook.com/v21.0/me/messages",
        "token": "",
        "phone_number_id": "",
        "versao_api": "v21.0",
    },
}


class ConfigError(ValueError):
    """Configuração inválida (mensagem pronta para mostrar ao usuário)."""


# --------------------------------------------------------------------------
# caminho e leitura
# --------------------------------------------------------------------------


def caminho() -> Path:
    """Arquivo de configuração em uso (env ``LOVEL_CONFIG`` tem prioridade)."""
    alternativo = os.getenv("LOVEL_CONFIG")
    return Path(alternativo).expanduser() if alternativo else CAMINHO_PADRAO


def _merge(base: Any, patch: Any) -> Any:
    """Mescla dicionários recursivamente; listas e escalares são substituídos."""
    if isinstance(base, dict) and isinstance(patch, dict):
        resultado = copy.deepcopy(base)
        for chave, valor in patch.items():
            resultado[chave] = _merge(resultado.get(chave), valor)
        return resultado
    return copy.deepcopy(patch)


def mesclar(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """API pública de ``_merge`` (usada pela UI para salvar só o que mudou)."""
    return _merge(base, patch)


def carregar() -> dict[str, Any]:
    """Configuração efetiva: arquivo mesclado sobre os padrões."""
    arquivo = caminho()
    if not arquivo.exists():
        return copy.deepcopy(PADROES)
    try:
        bruto = json.loads(arquivo.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{arquivo} não é um JSON válido (linha {exc.lineno}, coluna {exc.colno})."
        ) from exc
    if not isinstance(bruto, dict):
        raise ConfigError(f"{arquivo} deve conter um objeto JSON.")
    return _merge(PADROES, bruto)


def salvar(cfg: dict[str, Any]) -> Path:
    """Valida e grava a configuração de forma atômica."""
    validado = validar(cfg)
    arquivo = caminho()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_suffix(arquivo.suffix + ".tmp")
    temporario.write_text(
        json.dumps(validado, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporario, arquivo)
    return arquivo


def restaurar() -> dict[str, Any]:
    """Grava os padrões por cima do arquivo atual e devolve o resultado."""
    salvar(copy.deepcopy(PADROES))
    return copy.deepcopy(PADROES)


# --------------------------------------------------------------------------
# validação
# --------------------------------------------------------------------------


def _texto(valor: Any, campo: str) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise ConfigError(f"'{campo}' deve ser um texto não vazio.")
    return valor.strip()


def _booleano(valor: Any, campo: str) -> bool:
    if not isinstance(valor, bool):
        raise ConfigError(f"'{campo}' deve ser true ou false.")
    return valor


def _numero(valor: Any, campo: str, *, minimo: float, maximo: float) -> float:
    try:
        numero = float(valor)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"'{campo}' deve ser um número.") from exc
    if not minimo <= numero <= maximo:
        raise ConfigError(f"'{campo}' deve ficar entre {minimo} e {maximo}.")
    return numero


def _inteiro(valor: Any, campo: str, *, minimo: int, maximo: int) -> int:
    numero = _numero(valor, campo, minimo=minimo, maximo=maximo)
    if numero != int(numero):
        raise ConfigError(f"'{campo}' deve ser um número inteiro.")
    return int(numero)


def validar(cfg: Any) -> dict[str, Any]:
    """Normaliza tipos e valida faixas; levanta ``ConfigError`` se algo fugir."""
    if not isinstance(cfg, dict):
        raise ConfigError("A configuração deve ser um objeto JSON.")

    cfg = _merge(PADROES, cfg)
    empresa = cfg["empresa"]
    empresa["nome"] = _texto(empresa.get("nome"), "empresa.nome")
    empresa["horario_atendimento"] = _texto(
        empresa.get("horario_atendimento"), "empresa.horario_atendimento"
    )

    llm = cfg["llm"]
    modo = str(llm.get("modo", "auto")).strip().lower()
    if modo not in MODOS_LLM:
        raise ConfigError("'llm.modo' deve ser auto, mock ou real.")
    llm["modo"] = modo
    llm["model"] = _texto(llm.get("model"), "llm.model")
    llm["temperature"] = _numero(llm.get("temperature"), "llm.temperature", minimo=0, maximo=2)
    llm["max_tokens_classificacao"] = _inteiro(
        llm.get("max_tokens_classificacao"), "llm.max_tokens_classificacao", minimo=1, maximo=4000
    )
    llm["max_tokens_resposta"] = _inteiro(
        llm.get("max_tokens_resposta"), "llm.max_tokens_resposta", minimo=1, maximo=4000
    )
    if modo == "real" and not os.getenv("OPENAI_API_KEY"):
        raise ConfigError(
            "Modo real exige OPENAI_API_KEY no ambiente (.env). "
            "Defina a chave ou mantenha o modo auto/mock."
        )

    base = cfg["base"]
    base["arquivo"] = _texto(base.get("arquivo"), "base.arquivo")
    base["limiar"] = _numero(base.get("limiar"), "base.limiar", minimo=0, maximo=1)
    base["top_k"] = _inteiro(base.get("top_k"), "base.top_k", minimo=1, maximo=20)

    transbordo = cfg["transbordo"]
    for regra in (
        "reclamacao_critica",
        "termos_criticos_urgentes",
        "sem_cobertura",
        "fora_de_escopo_urgente",
    ):
        transbordo[regra] = _booleano(transbordo.get(regra), f"transbordo.{regra}")

    termos = transbordo.get("termos_criticos")
    if not isinstance(termos, list):
        raise ConfigError("'transbordo.termos_criticos' deve ser uma lista.")
    limpos: list[str] = []
    for item in termos:
        texto = _texto(item, "transbordo.termos_criticos[]").lower()
        if texto not in limpos:
            limpos.append(texto)
    transbordo["termos_criticos"] = limpos

    prazos = transbordo.get("prazos")
    if not isinstance(prazos, dict):
        raise ConfigError("'transbordo.prazos' deve ser um objeto.")
    transbordo["prazos"] = {
        urgencia: _texto(prazos.get(urgencia), f"transbordo.prazos.{urgencia}")
        for urgencia in URGENCIAS
    }

    registro = cfg["registro"]
    for chave in ("csv", "tickets", "webhook"):
        registro[chave] = _booleano(registro.get(chave), f"registro.{chave}")
    registro["diretorio"] = _texto(registro.get("diretorio"), "registro.diretorio")

    demo = cfg["demo"]
    demo["host"] = _texto(demo.get("host"), "demo.host")
    demo["port"] = _inteiro(demo.get("port"), "demo.port", minimo=1, maximo=65535)

    whatsapp = cfg["whatsapp"]
    whatsapp["enabled"] = _booleano(whatsapp.get("enabled"), "whatsapp.enabled")
    whatsapp["api_url"] = _texto(whatsapp.get("api_url"), "whatsapp.api_url")
    whatsapp["token"] = whatsapp.get("token") or ""
    whatsapp["phone_number_id"] = _texto(
        whatsapp.get("phone_number_id"), "whatsapp.phone_number_id"
    )
    whatsapp["versao_api"] = _texto(
        whatsapp.get("versao_api"), "whatsapp.versao_api"
    )

    return cfg


# --------------------------------------------------------------------------
# helpers de leitura
# --------------------------------------------------------------------------


def secao(nome: str) -> dict[str, Any]:
    return carregar()[nome]


def diretorio_saida() -> Path:
    """Diretório de saída configurado.

    ``LOVEL_OUTPUT_DIR`` tem prioridade (útil em ambientes com disco
    somente-leitura, como o Vercel, onde a escrita vai para ``/tmp``).
    """
    alternativo = os.getenv("LOVEL_OUTPUT_DIR")
    if alternativo:
        return Path(alternativo).expanduser()
    bruto = Path(carregar()["registro"]["diretorio"]).expanduser()
    return bruto if bruto.is_absolute() else ROOT / bruto


def caminho_base() -> Path:
    bruto = Path(carregar()["base"]["arquivo"]).expanduser()
    return bruto if bruto.is_absolute() else ROOT / bruto
