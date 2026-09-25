# 11. Vận hành PostgreSQL dùng chung

[← Bản đồ](../index.md)

Một máy chạy PostgreSQL bằng Docker Compose; các server khác đặt `TELE_DATABASE_URL` trỏ về
máy đó. Các lệnh vận hành gom trong `Makefile`.

## Lệnh

| Lệnh | Việc |
|---|---|
| `make env` | Tạo `.env` từ `.env.example`, thay `change-me` bằng mật khẩu ngẫu nhiên, quyền 600 (không ghi đè `.env` có sẵn) |
| `make up` / `make down` / `make restart` | Bật (chờ healthcheck) / tắt / khởi động lại; dữ liệu giữ trong volume `db-data` |
| `make status` | `docker compose ps` + `bin/tele-local info` để kiểm tra kết nối |
| `make logs`, `make psql` | Xem log, mở psql trong container |
| `make backup` | `pg_dump` ra `backups/tele-<thời gian>.sql.gz` |
| `make client-env` | In dòng `TELE_DATABASE_URL` (dùng IP đầu tiên của máy) để dán sang server khác |

## File liên quan

| File | Vai trò |
|---|---|
| `compose.yaml` | Service `db` (`postgres:17-alpine`), port `${TELE_DB_BIND}:${TELE_DB_PORT}:5432`, volume `db-data`, healthcheck `pg_isready`. Bắt buộc có `POSTGRES_PASSWORD` |
| `Makefile` | Các target ở bảng trên; biến `COMPOSE`, `BACKUP_DIR` đổi được khi gọi `make` |
| `.env.example` | `TELE_DATABASE_URL`, `POSTGRES_USER/PASSWORD/DB`, `TELE_DB_BIND`, `TELE_DB_PORT` |
| `src/tele_cli/store/database.py` | `PostgresDatabase` (kết nối, keepalive, `application_name=tele`), `mask_password` (ẩn mật khẩu trong `info`/lỗi), `connection_hint` |

## Lưu ý khi sửa

- Kết nối chỉ dùng mật khẩu, không TLS: khi `TELE_DB_BIND=0.0.0.0` chỉ mở port trong mạng
  nội bộ/VPN.
- Mật khẩu có ký tự đặc biệt phải URL-encode trong `TELE_DATABASE_URL`.
- Chạy một PostgreSQL riêng để thử: `make up COMPOSE="docker compose -p tele-test"`.
