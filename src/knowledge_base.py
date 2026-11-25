"""Busca na base de conhecimento fictícia usando TF-IDF (scikit-learn).

O limiar e o ``top_k`` vêm de ``config/lovel.json`` e são lidos a cada busca,
para que alterações feitas na UI valham sem reiniciar o servidor.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from . import config as config_mod

# O scikit-learn só traz stopwords embutidas em inglês. Sem esta lista, palavras
# funcionais ("de", "para", "com") geram similaridade espúria em documentos
# curtos — foi o que fazia "cartucho de tinta" casar com a FAQ de reembolso.
STOPWORDS_PT = [
    "a", "acho", "agora", "ai", "alguem", "alguma", "algumas", "algum", "alguns",
    "ao", "aos", "aquela", "aquelas", "aquele", "aqueles", "aqui", "as", "assim",
    "ate", "bem", "boa", "bom", "com", "como", "da", "das", "de", "dela", "dele",
    "deles", "demais", "depois", "do", "dos", "e", "ela", "elas", "ele", "eles",
    "em", "entre", "era", "essa", "essas", "esse", "esses", "esta", "estas",
    "este", "estes", "eu", "foi", "fui", "ha", "isso", "isto", "ja", "la", "lhe",
    "mais", "mas", "me", "mesmo", "meu", "minha", "muito", "na", "nao", "nas",
    "nem", "no", "nos", "nossa", "nosso", "o", "os", "ou", "para", "pela",
    "pelo", "por", "qual", "quando", "que", "quem", "se", "sem", "seu", "sua",
    "suas", "sao", "tambem", "tem", "tenho", "ter", "teu", "tua", "um", "uma",
    "voce", "voces", "oi", "ola", "dia", "tarde", "noite", "preciso", "quero",
    "gostaria", "poderia", "consigo", "fazer", "favor",
]

_LIMITES = {"id": 60, "topico": 80, "resposta_padrao": 1200}
_ID_VALIDO = re.compile(r"^[a-z0-9_\-]{3,60}$")


class BaseInvalida(ValueError):
    """Base de conhecimento fora do schema (mensagem pronta para o usuário)."""


# --------------------------------------------------------------------------
# leitura / escrita
# --------------------------------------------------------------------------


def caminho_base() -> Path:
    """Arquivo da base em uso.

    ``LOVEL_KB`` tem prioridade para permitir uma cópia gravável em ambientes
    serverless (o arquivo original do repositório é somente-leitura).
    """
    alternativo = os.getenv("LOVEL_KB")
    if alternativo:
        return Path(alternativo).expanduser()
    return config_mod.caminho_base()


def carregar_base(caminho: str | Path | None = None) -> dict[str, Any]:
    arquivo = Path(caminho) if caminho is not None else caminho_base()
    if not arquivo.exists():
        return {"empresa": "", "faqs": []}
    with arquivo.open(encoding="utf-8") as aberto:
        return json.load(aberto)


def salvar_base(base: dict[str, Any], caminho: str | Path | None = None) -> Path:
    """Valida e grava a base (mesmo esquema atômico da configuração)."""
    limpa = validar_base(base)
    arquivo = Path(caminho) if caminho is not None else caminho_base()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_suffix(arquivo.suffix + ".tmp")
    temporario.write_text(
        json.dumps(limpa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporario, arquivo)
    return arquivo


def validar_base(base: Any) -> dict[str, Any]:
    if not isinstance(base, dict):
        raise BaseInvalida("A base deve ser um objeto JSON com a chave 'faqs'.")

    faqs = base.get("faqs")
    if not isinstance(faqs, list) or not faqs:
        raise BaseInvalida("A base precisa de pelo menos uma FAQ.")

    vistas: set[str] = set()
    limpas: list[dict[str, Any]] = []
    for indice, faq in enumerate(faqs, start=1):
        local = f"FAQ #{indice}"
        if not isinstance(faq, dict):
            raise BaseInvalida(f"{local}: deve ser um objeto.")

        identificador = str(faq.get("id") or "").strip().lower()
        if not _ID_VALIDO.match(identificador):
            raise BaseInvalida(
                f"{local}: 'id' deve ter de 3 a 60 caracteres, apenas letras "
                "minúsculas, números, hífen ou sublinhado."
            )
        if identificador in vistas:
            raise BaseInvalida(f"O id '{identificador}' está repetido.")
        vistas.add(identificador)

        topico = str(faq.get("topico") or "").strip()
        if not topico:
            raise BaseInvalida(f"{local}: 'topico' é obrigatório.")
        if len(topico) > _LIMITES["topico"]:
            raise BaseInvalida(f"{local}: 'topico' passa de {_LIMITES['topico']} caracteres.")

        palavras = faq.get("palavras_chave")
        if not isinstance(palavras, list) or not palavras:
            raise BaseInvalida(f"{local}: informe ao menos uma palavra-chave.")
        chaves = [str(item).strip().lower() for item in palavras if str(item).strip()]
        if not chaves:
            raise BaseInvalida(f"{local}: as palavras-chave estão vazias.")
        if len(chaves) > 30:
            raise BaseInvalida(f"{local}: use no máximo 30 palavras-chave.")

        resposta = str(faq.get("resposta_padrao") or "").strip()
        if not resposta:
            raise BaseInvalida(f"{local}: 'resposta_padrao' é obrigatória.")
        if len(resposta) > _LIMITES["resposta_padrao"]:
            raise BaseInvalida(
                f"{local}: a resposta passa de {_LIMITES['resposta_padrao']} caracteres."
            )

        limpas.append(
            {
                "id": identificador,
                "topico": topico,
                "palavras_chave": chaves,
                "resposta_padrao": resposta,
            }
        )

    empresa = str(base.get("empresa") or "").strip()
    return {"empresa": empresa, "faqs": limpas}


# --------------------------------------------------------------------------
# busca
# --------------------------------------------------------------------------


def parametros() -> tuple[float, int]:
    """(limiar, top_k) efetivos, vindos da configuração."""
    base_cfg = config_mod.carregar()["base"]
    return float(base_cfg["limiar"]), int(base_cfg["top_k"])


def _documento(faq: dict[str, Any]) -> str:
    return " ".join(
        [
            faq["topico"],
            " ".join(faq["palavras_chave"]),
            faq["resposta_padrao"],
        ]
    )


def _pontuar(mensagem: str, faqs: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
    if not faqs or not mensagem.strip():
        return [(0.0, faq) for faq in faqs]

    vetorizador = TfidfVectorizer(
        strip_accents="unicode",
        ngram_range=(1, 2),
        sublinear_tf=True,
        stop_words=STOPWORDS_PT,
    )
    matriz = vetorizador.fit_transform([_documento(faq) for faq in faqs])
    consulta = vetorizador.transform([mensagem])
    similaridades = cosine_similarity(consulta, matriz).ravel()
    return sorted(
        zip((float(score) for score in similaridades), faqs),
        key=lambda par: par[0],
        reverse=True,
    )


def buscar_faqs(
    mensagem: str,
    base: dict[str, Any] | None = None,
    limiar: float | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Retorna até ``top_k`` FAQs com similaridade >= ``limiar``.

    Lista vazia significa "não coberto pela base" — o chamador deve transbordar.
    """
    base = base or carregar_base()
    limiar_cfg, top_k_cfg = parametros()
    limiar = limiar_cfg if limiar is None else float(limiar)
    top_k = top_k_cfg if top_k is None else int(top_k)

    trechos: list[dict[str, Any]] = []
    for score, faq in _pontuar(mensagem, base.get("faqs") or [])[:top_k]:
        if score < limiar:
            continue
        trechos.append(
            {
                "id": faq["id"],
                "topico": faq["topico"],
                "resposta_padrao": faq["resposta_padrao"],
                "score": round(score, 4),
            }
        )
    return trechos


def pontuar_faqs(mensagem: str, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Diagnóstico para a UI: score de todas as FAQs contra uma mensagem."""
    base = base or carregar_base()
    limiar, top_k = parametros()
    resultados = [
        {
            "id": faq["id"],
            "topico": faq["topico"],
            "score": round(score, 4),
            "cobre": score >= limiar,
        }
        for score, faq in _pontuar(mensagem, base.get("faqs") or [])
    ]
    return {
        "mensagem": mensagem,
        "limiar": limiar,
        "top_k": top_k,
        "resultados": resultados,
        "cobre": any(item["cobre"] for item in resultados),
    }
