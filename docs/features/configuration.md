# 9. Cấu hình: `.env`, đường dẫn, credentials

[← Bản đồ](../index.md)

Mọi đường dẫn, credentials và kết nối database được quyết định trong `config.py`, từ biến môi
trường và các file `.env`.

## Thứ tự nạp `.env` (`config.load_env_files`)

Biến đã `export` trong shell luôn thắng. Sau đó file đứng trước thắng file đứng sau:

1. `$TELE_ENV_FILE`
2. `.env` của source checkout (chỉ khi `pyproject.toml` tồn tại, tức không phải binary)
3. `~/.config/tele/.env`

`cli/runner.py:run` gọi `load_env_files()` **trước** khi build parser, vì một số giá trị mặc
định của option đọc biến môi trường (ví dụ `TELE_CACHE_MAX_AGE`).

## Biến môi trường

| Biến | Mặc định | Hàm |
|---|---|---|
| `TELE_API_ID`, `TELE_API_HASH` | `~/.config/tele/config.json` | `load_credentials` (phải đặt cả hai) |
| `TELE_SESSION` | `~/.local/share/tele/telegram(.session)` | `session_path`, `session_file_path` |
| `TELE_DATABASE_URL` | trống = SQLite | `database_url` |
| `TELE_DB` | `~/.local/share/tele/messages.db` | `database_path` |
| `TELE_MEDIA_DIR` (cũ: `TELE_DOWNLOAD_DIR`) | `media_dir` trong config.json, rồi `~/telegram-files` | `default_media_root` |
| `TELE_CACHE_MAX_AGE` | `300` | `cli/tele.py:_default_max_age` |
| `TELE_ENV_FILE` | — | `env_files` |
| `XDG_CONFIG_HOME`, `XDG_DATA_HOME` | `~/.config`, `~/.local/share` | `config_dir`, `data_dir` |

Biến chỉ dùng cho container PostgreSQL (`POSTGRES_*`, `TELE_DB_BIND`, `TELE_DB_PORT`) xem
[shared-postgres.md](shared-postgres.md).

## File liên quan

| File | Vai trò |
|---|---|
| `src/tele_cli/config.py` | `PROJECT_ROOT`, `config_dir`, `data_dir`, `config_path`, `session_path`, `env_files`, `_parse_env_line` (hỗ trợ `export`, ngoặc kép, comment ` #`), `load_env_files`, `database_url`, `database_path`, `default_media_root`, credentials, `secure_*` |
| `src/tele_cli/cli/runner.py` | `os.umask(0o077)` để mọi file tạo ra chỉ chủ sở hữu đọc được; gọi `load_env_files()` |
| `src/tele_cli/cli/tele.py` | `_default_max_age` |
| `.env.example` | Mẫu `.env` có chú thích; `make env` copy ra `.env` với mật khẩu ngẫu nhiên |
| `.gitignore` | Loại `.env`, `.venv`, `build/`, `dist/`, `backups/` khỏi git |

## Lưu ý khi sửa

- Thêm biến mới: đọc trong `config.py`, ghi chú vào `.env.example` và bảng "Dữ liệu" trong `README.md`.
- `load_env_files` dùng `os.environ.setdefault`: không ghi đè biến đã có.
