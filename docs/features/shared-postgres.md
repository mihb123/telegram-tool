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
| `make sync-db` | Dump database remote ra `backups/remote-<thời gian>.sql.gz` rồi thay toàn bộ PostgreSQL local bằng bản đó (xem dưới) |
| `make client-env` | In dòng `TELE_DATABASE_URL` (dùng IP đầu tiên của máy) để dán sang server khác |

## Bản sao local (`make sync-db`)

Trên máy **không** chạy database chính, `make sync-db` vừa backup vừa giữ một bản sao để đọc
khi mạng tới server có vấn đề:

1. Bật PostgreSQL local của checkout (`docker compose up`, như `make up`).
2. `pg_dump` database nguồn ngay trong container local (cùng bản `postgres:17` với server),
   ghi ra `backups/remote-<thời gian>.sql.gz.part`; chỉ đổi tên thành `.sql.gz` khi dump xong
   và có dòng kết thúc của `pg_dump`. Kết nối không được sau 15 giây thì dừng.
3. Nạp bản dump vào database local trong **một transaction** (`--clean --if-exists`): lỗi giữa
   chừng thì database local giữ nguyên bản cũ.
4. Giữ `SYNC_KEEP` bản dump mới nhất (mặc định 30, `0` = giữ hết), in số tin/dialog/peer.

Database nguồn là `TELE_REMOTE_DATABASE_URL`, không có thì lấy `TELE_DATABASE_URL`; trỏ về
`localhost`/`127.*` thì bị từ chối. `POSTGRES_*`, `TELE_DB_BIND`, `TELE_DB_PORT` trong `.env` là
của bản sao local: đặt `TELE_DB_BIND=127.0.0.1` và đổi `TELE_DB_PORT` nếu 5432 đã bị chiếm.

Khi mất mạng, trỏ tele sang bản sao, ví dụ cho một shell:

```bash
export TELE_DATABASE_URL=postgresql://tele:<POSTGRES_PASSWORD>@localhost:5433/tele
tele-local search "..."
```

Có mạng lại thì bỏ biến đó. `make sync-db` **ghi đè** database local: những gì `tele` ghi vào
bản sao trong lúc mất mạng không được đẩy lên server, nên chạy `make backup` trước nếu cần giữ.
Muốn chạy định kỳ thì gọi `make -C <thư mục repo> sync-db` từ cron hoặc systemd timer.

## File liên quan

| File | Vai trò |
|---|---|
| `compose.yaml` | Service `db` (`postgres:17-alpine`), port `${TELE_DB_BIND}:${TELE_DB_PORT}:5432`, volume `db-data`, healthcheck `pg_isready`. Bắt buộc có `POSTGRES_PASSWORD` |
| `Makefile` | Các target ở bảng trên; biến `COMPOSE`, `BACKUP_DIR`, `SYNC_KEEP` đổi được khi gọi `make` |
| `scripts/sync-db` | Thân của `make sync-db`: kiểm tra URL nguồn, dump, kiểm tra bản dump, restore trong một transaction, xoá bản dump cũ |
| `.env.example` | `TELE_DATABASE_URL`, `POSTGRES_USER/PASSWORD/DB`, `TELE_DB_BIND`, `TELE_DB_PORT` |
| `src/tele_cli/store/database.py` | `PostgresDatabase` (kết nối, keepalive, `application_name=tele`), `mask_password` (ẩn mật khẩu trong `info`/lỗi), `connection_hint` |

## Lưu ý khi sửa

- Kết nối chỉ dùng mật khẩu, không TLS: khi `TELE_DB_BIND=0.0.0.0` chỉ mở port trong mạng
  nội bộ/VPN.
- Mật khẩu có ký tự đặc biệt phải URL-encode trong `TELE_DATABASE_URL`.
- Chạy một PostgreSQL riêng để thử: `make up COMPOSE="docker compose -p tele-test"`.
