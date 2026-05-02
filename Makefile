PYTHON := python3
PIP    := $(PYTHON) -m pip
ANDYRIA_CONFIG ?= config.yaml

.PHONY: setup run serve ask test lint build-rust clean dev deploy-pi deploy-pi-auto self-install

setup:
	$(PIP) install -e "python/[dev]"
	cd rust && cargo build

setup-llm:
	$(PIP) install -e "python/[llm,dev]"

run: serve

serve:
	$(PYTHON) -m andyria serve --config $(ANDYRIA_CONFIG)

ask:
	$(PYTHON) -m andyria ask "$(PROMPT)"

test:
	cd python && $(PYTHON) -m pytest tests/ -v
	cd rust && cargo test

lint:
	cd python && $(PYTHON) -m ruff check andyria/
	cd rust && cargo clippy -- -D warnings

build-rust:
	cd rust && cargo build --release

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	cd rust && cargo clean

docker-up:
	docker compose up --build

# Dev mode: live source editing + hot-reload + code-server at http://localhost:8080
dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

docker-peer:
	docker compose --profile peer up --build

deploy-pi:
	bash deploy/raspberry-pi/deploy.sh

deploy-pi-auto:
	bash deploy/raspberry-pi/deploy.sh --non-interactive --host $(PI_HOST)

self-install:
	bash deploy/self-install.sh

# Download a minimal GGUF model for edge/Raspberry Pi testing
model-tiny:
	mkdir -p ~/.andyria/models
	curl -L -o ~/.andyria/models/tinyllama-1.1b-chat-v1.0.Q2_K.gguf \
	  "https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q2_K.gguf"

model-server:
	mkdir -p ~/.andyria/models
	curl -L -o ~/.andyria/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf \
	  "https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"
