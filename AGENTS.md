# Convenções do Projeto

- Nenhuma resposta automática pode ser gerada sem consultar a base de conhecimento.
- Toda mensagem gera registro em outputs/atendimentos.csv.
- Todo transbordo gera ticket em outputs/tickets/.
- Testes devem rodar com MOCK_LLM=1 sem rede.
- Prompts do LLM vivem em src/classifier.py e src/responder.py como constantes (não inline).
- Formatação: black + ruff.
- Comando de teste: `make test`.
- Comando de demo: `make demo`.
- Comando da demo web: `make demo-ui` (ou `python -m src.demo_server --open`).

## Estrutura

- `src/main.py` — orquestra o fluxo e expõe `processar_mensagem(payload, output_dir)`.
- `src/models.py` — dataclasses e enums de domínio.
- `src/classifier.py` — prompt do classificador + validação defensiva do JSON.
- `src/knowledge_base.py` — busca TF-IDF com limiar configurável em `config/lovel.json`.
- `src/responder.py` — prompt do gerador restrito + normalização de `NAO_COBERTO`.
- `src/escalation.py` — regras determinísticas de transbordo e montagem do ticket.
- `src/recorder.py` — CSV, tickets e webhooks (sem dependência de rede).
- `src/llm_client.py` — wrapper OpenAI `gpt-4o-mini` e mock determinístico.
- `src/config.py` — configuração única, validação e gravação atômica.
- `src/export.py` — exportação dos atendimentos em CSV e XML.
- `src/demo_server.py` — roteador HTTP comum (`responder`) + servidor local (stdlib).
- `src/demo_ui.html` — UI da demo (chat + pipeline + configurações), servida pelo server.
- `app.py` — adaptador WSGI/Flask para o Vercel, usando o mesmo `responder`.
- `assets/` — logo original (`logo.png`), marca do cabeçalho e favicon.

## Invariantes

1. O gerador de resposta recebe **somente** os trechos recuperados da base.
   Exceção determinística: saudações exatas (`oi`/`ola`) usam uma resposta fixa
   da configuração e **não** chamam o gerador.
2. Saída inválida do classificador vira `fora_de_escopo` + `media`.
3. `NAO_COBERTO` (vazio, exato ou dentro do texto) sempre transborda.
4. O status é `TRANSBORDADO` sempre que a regra de transbordo disparar, mesmo que
   o LLM tenha produzido uma resposta.
5. `resposta_gerada` é `null` no resultado e vazia no CSV quando há transbordo.
6. Os artefatos de saída são determinísticos em `MOCK_LLM=1`.
7. A demo web usa o mesmo `processar_mensagem` e grava em `outputs/demo/`;
   nunca altera `outputs/atendimentos.csv`, `outputs/tickets/` nem
   `outputs/webhook_payloads/`.
8. O Vercel usa o mesmo roteador (`responder`) via `app.py`; no deploy, config,
   base e saídas são copiadas para `/tmp` (disco efêmero) por
   `LOVEL_CONFIG` / `LOVEL_KB` / `LOVEL_DEMO_DIR` / `LOVEL_OUTPUT_DIR`.
