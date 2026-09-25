# Bật/tắt PostgreSQL dùng chung cho tele và các việc thường làm. Chạy `make` để xem danh sách.
SHELL := bash
COMPOSE := docker compose
INSTALL_DIR ?= $(HOME)/bin
BACKUP_DIR ?= backups

.DEFAULT_GOAL := help
.PHONY: help env up down restart status logs psql backup client-env build install lint

help: ## Liệt kê các lệnh
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*## "} {printf "  make %-11s %s\n", $$1, $$2}'

env: ## Tạo .env từ .env.example với mật khẩu ngẫu nhiên (không ghi đè .env có sẵn)
	@if [ -f .env ]; then echo ".env đã tồn tại, giữ nguyên."; exit 0; fi; \
	password=$$(openssl rand -hex 16); \
	sed "s/change-me/$$password/g" .env.example > .env && chmod 600 .env; \
	echo "Đã tạo .env với POSTGRES_PASSWORD ngẫu nhiên. Xem lại TELE_DB_BIND trước khi 'make up'."

.env:
	@echo "Chưa có .env: chạy 'make env' (hoặc cp .env.example .env rồi sửa)." >&2; exit 1

up: .env ## Bật database server và chờ tới khi sẵn sàng
	$(COMPOSE) up -d --wait
	@$(MAKE) --no-print-directory status

down: ## Tắt database server (dữ liệu vẫn giữ trong volume)
	$(COMPOSE) down

restart: .env ## Khởi động lại database server
	$(COMPOSE) down
	$(COMPOSE) up -d --wait

status: ## Trạng thái container và kết nối của tele tới database
	@$(COMPOSE) ps
	@if [ -x .venv/bin/python ]; then bin/tele-local info; else echo "(chạy 'uv sync' để kiểm tra kết nối bằng tele-local)"; fi

logs: ## Xem log database server
	$(COMPOSE) logs -f --tail=100 db

psql: ## Mở psql vào database
	$(COMPOSE) exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

backup: ## Dump database ra backups/tele-<thời gian>.sql.gz
	@mkdir -p $(BACKUP_DIR)
	$(COMPOSE) exec -T db sh -c 'pg_dump -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' \
		| gzip > $(BACKUP_DIR)/tele-$$(date +%Y%m%d-%H%M%S).sql.gz
	@ls -lh $(BACKUP_DIR) | tail -n 1

client-env: .env ## In dòng TELE_DATABASE_URL để dán vào .env của server khác
	@set -a; . ./.env; set +a; \
	host=$$(hostname -I 2>/dev/null | awk '{print $$1}'); host=$${host:-$$(hostname)}; \
	echo "TELE_DATABASE_URL=postgresql://$$POSTGRES_USER:$$POSTGRES_PASSWORD@$$host:$${TELE_DB_PORT:-5432}/$$POSTGRES_DB"

build: ## Build binary dist/tele và dist/tele-local
	./scripts/build-standalone

install: build ## Build rồi cài tele, tele-local vào ~/bin (đổi bằng INSTALL_DIR=...)
	install -d $(INSTALL_DIR)
	install -m 755 dist/tele dist/tele-local $(INSTALL_DIR)/

lint: ## ruff check + kiểm tra format
	uv run ruff check
	uv run ruff format --check
