# 12. Chạy từ source, build và phân phối binary

[← Bản đồ](../index.md)

Chạy thẳng từ repo bằng launcher trong `bin/`, hoặc build hai binary một file bằng
PyInstaller để chia sẻ cho máy không có Python.

## Cách chạy

| Cách | Lệnh |
|---|---|
| Chuẩn bị môi trường | `uv sync` (tạo `.venv` theo `uv.lock`) |
| Chạy từ source | `bin/tele ...`, `bin/tele-local ...` (symlink vào `~/bin` vẫn chạy được) |
| Build | `make build` hoặc `./scripts/build-standalone` → `dist/tele`, `dist/tele-local`, `dist/SHA256SUMS` |
| Cài vào máy | `make install` (mặc định `~/bin`, đổi bằng `INSTALL_DIR=...`) |
| Lint / format | `make lint`, `uv run ruff format` |

## File liên quan

| File | Vai trò |
|---|---|
| `bin/tele` | Launcher: tự `execv` sang `.venv/bin/python` nếu chưa ở trong venv, thêm `src/` vào `sys.path`, gọi `cli.tele.main`. Báo lỗi nếu chưa `uv sync` |
| `bin/tele-local` | Tương tự cho `cli.local.main`; vẫn chạy được không có `.venv` (SQLite chỉ cần stdlib) |
| `scripts/build-standalone` | `uv sync` rồi PyInstaller `--onefile` cho từng entry point, kèm `--hidden-import psycopg_binary`; ghi `SHA256SUMS` |
| `scripts/entrypoints/tele.py` | Entry point PyInstaller cho `tele` |
| `scripts/entrypoints/tele_local.py` | Entry point PyInstaller cho `tele-local` (không chứa Telethon) |
| `pyproject.toml` | Tên, phiên bản, `requires-python`, dependency (`telethon`, `cryptg`, `aiohttp`, `psycopg[binary]`), nhóm `dev` (`pyinstaller`, `ruff`), cấu hình ruff |
| `uv.lock` | Chốt chính xác version + hash mọi dependency; commit cùng `pyproject.toml` |
| `.python-version` | Phiên bản Python uv dùng cho `.venv` |
| `Makefile` | Target `build`, `install`, `lint` |
| `src/tele_cli/config.py` | `PROJECT_ROOT`: trong binary không có `pyproject.toml` nên bỏ qua `.env` của source |

## Lưu ý khi sửa

- Thêm dependency: `uv add <gói>` (cập nhật cả `pyproject.toml` và `uv.lock`). Nếu gói có
  import động, có thể cần thêm `--hidden-import` trong `scripts/build-standalone`.
- Binary không chứa credentials/session; người nhận chạy `tele configure` và `tele auth`.
