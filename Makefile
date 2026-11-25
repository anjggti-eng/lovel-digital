.PHONY: install test run demo demo-ui

install:
	pip install -r requirements.txt

test:
	MOCK_LLM=1 pytest -v

run:
	MOCK_LLM=1 python -m src.main data/simulated_messages/case_$(case)_*.json

demo:
	MOCK_LLM=1 python -m src.main --all

demo-ui:
	MOCK_LLM=1 python -m src.demo_server --open
