# Bản đồ tính năng

Đọc file này trước khi sửa code: tìm tính năng ở bảng dưới, mở file tính năng tương ứng để
thấy luồng xử lý và danh sách file liên quan. Hướng dẫn dùng lệnh cho người dùng cuối nằm
ở [`README.md`](../README.md).

## Tính năng

| # | Tính năng | Lệnh / điểm vào | File tài liệu |
|---|---|---|---|
| 1 | Thiết lập, đăng nhập, kiểm tra session | `tele configure`, `tele auth`, `tele status` | [setup-auth.md](features/setup-auth.md) |
| 2 | Đọc tin nhắn, ưu tiên đọc database | `tele get` | [get-messages.md](features/get-messages.md) |
| 3 | Tải media không trùng lặp | `tele get --download-media`, `-d` | [media-download.md](features/media-download.md) |
| 4 | Gửi tin và chờ phản hồi | `tele send`, `tele wait` | [send-wait.md](features/send-wait.md) |
| 5 | Danh sách hội thoại | `tele dialogs` | [dialogs.md](features/dialogs.md) |
| 6 | Truy vấn dữ liệu đã lưu (không gọi Telegram) | `tele-local chats/messages/search/media/sql/info` | [local-query.md](features/local-query.md) |
| 7 | Database: SQLite / PostgreSQL, schema, migration | `Store.open()` | [database.md](features/database.md) |
| 8 | Nhiều máy, nhiều tài khoản dùng chung database | bảng `nodes`, `tele-local --account` | [nodes-accounts.md](features/nodes-accounts.md) |
| 9 | Cấu hình: `.env`, đường dẫn, credentials | `config.py` | [configuration.md](features/configuration.md) |
| 10 | Khung CLI: parse, in JSON/text, mã lỗi | `cli/runner.py` | [cli-output-errors.md](features/cli-output-errors.md) |
| 11 | Vận hành PostgreSQL dùng chung | `make up/down/backup/...` | [shared-postgres.md](features/shared-postgres.md) |
| 12 | Chạy từ source, build và phân phối binary | `bin/`, `make build/install` | [build-distribution.md](features/build-distribution.md) |

## Cần sửa gì thì đọc file nào

| Muốn sửa | Đọc tính năng | File chính |
|---|---|---|
| Thêm option cho một lệnh `tele` | 10, rồi tính năng của lệnh đó | `src/tele_cli/cli/tele.py` |
| Thêm lệnh / option cho `tele-local` | 6, 10 | `src/tele_cli/cli/local.py` |
| Khi nào `tele get` trả từ database, khi nào gọi Telegram | 2 | `src/tele_cli/telegram/history.py` |
| Cách đặt tên file media, giới hạn dung lượng, `download_status` | 3 | `src/tele_cli/telegram/media.py` |
| Nội dung / điều kiện gửi tin, logic `tele wait` | 4 | `src/tele_cli/telegram/messaging.py` |
| Thêm cột, bảng, index | 7 | `src/tele_cli/store/migrations.py`, `store/repository.py` |
| Lưu thêm một trường của tin nhắn Telegram | 2, 7 | `telegram/records.py` → `migrations.py` → `repository.py` → `store/payloads.py` |
| Đổi dạng JSON in ra | 10 | `src/tele_cli/store/payloads.py`, `src/tele_cli/output.py` |
| Tìm kiếm không dấu, hàm `fold()` | 6, 7 | `src/tele_cli/store/database.py` |
| Thêm biến môi trường, đổi đường dẫn mặc định | 9 | `src/tele_cli/config.py`, `.env.example` |
| Thêm mã lỗi / exit code | 10 | `src/tele_cli/errors.py` + nơi `raise TeleError` |
| Parse `-u`, `--since`, `--until` | 10 | `src/tele_cli/values.py`, `src/tele_cli/cli/args.py` |
| Cấu hình container PostgreSQL, lệnh `make` | 11 | `compose.yaml`, `Makefile` |
| Build binary, thêm dependency | 12 | `scripts/build-standalone`, `pyproject.toml` |

## File → tính năng

| File | Dùng cho tính năng |
|---|---|
| `src/tele_cli/cli/tele.py` | 1, 2, 3, 4, 5, 10 |
| `src/tele_cli/cli/local.py` | 6, 8, 10 |
| `src/tele_cli/cli/args.py` | 6, 10 |
| `src/tele_cli/cli/runner.py` | 9, 10 |
| `src/tele_cli/telegram/client.py` | 1, 2, 4, 8 |
| `src/tele_cli/telegram/history.py` | 2, 3 |
| `src/tele_cli/telegram/media.py` | 3 |
| `src/tele_cli/telegram/messaging.py` | 4 |
| `src/tele_cli/telegram/dialogs.py` | 5 |
| `src/tele_cli/telegram/records.py` | 2, 3, 4, 5 |
| `src/tele_cli/store/repository.py` | 2, 3, 4, 5, 6, 7, 8 |
| `src/tele_cli/store/database.py` | 6, 7 |
| `src/tele_cli/store/migrations.py` | 7 |
| `src/tele_cli/store/payloads.py` | 2, 3, 6, 10 |
| `src/tele_cli/config.py` | 1, 3, 8, 9 |
| `src/tele_cli/values.py` | 6, 10 |
| `src/tele_cli/output.py` | 10 |
| `src/tele_cli/errors.py` | 10 |
| `compose.yaml`, `Makefile`, `.env.example` | 9, 11, 12 |
| `bin/tele`, `bin/tele-local`, `scripts/` | 12 |

## Quy ước kiến trúc (áp dụng cho mọi thay đổi)

- Hai lớp tách biệt: `telegram/` là nơi duy nhất import Telethon; `store/` và
  `cli/local.py` **không** import `telegram/` hay Telethon (để `tele-local` chạy không cần
  Telethon). `cli/__init__.py` phải để trống vì lý do này.
- psycopg chỉ được import bên trong `PostgresDatabase`, nên SQLite chạy chỉ với thư viện chuẩn.
- Mọi SQL viết một lần, dùng cú pháp cả SQLite và PostgreSQL đều hiểu, placeholder `?` và
  `:name`.
- Mọi dữ liệu tin nhắn được khoá theo `account_id`.
- Lỗi cho người dùng luôn là `TeleError` (có `code`, `exit_code`, `hint`), in ra stderr dạng JSON.
- Dự án **không có test tự động**: kiểm tra bằng lệnh thật (`bin/tele ...`,
  `bin/tele-local ...`) và `make lint`.
