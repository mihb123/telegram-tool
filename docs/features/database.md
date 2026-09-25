# 7. Database: SQLite / PostgreSQL, schema, migration

[← Bản đồ](../index.md)

Một lớp lưu trữ duy nhất cho cả hai CLI. Để trống `TELE_DATABASE_URL` là dùng SQLite trên
máy; đặt `postgresql://...` là dùng PostgreSQL dùng chung. Schema tự nâng cấp khi mở.

## Các lớp

```text
Store (repository.py)         mọi câu đọc/ghi nghiệp vụ, gắn với 1 account_id
  └─ Database (database.py)   API chung: execute / all / one / scalar / transaction / lock
       ├─ SQLiteDatabase      stdlib sqlite3, WAL, BEGIN IMMEDIATE
       └─ PostgresDatabase    psycopg, autocommit + transaction, advisory lock
```

`Store.open()` → `open_database()` chọn backend → `db.migrate()` → tạo `Store` với
`current_node()`.

## Schema (`store/migrations.py`)

| Bảng | Nội dung | Khoá |
|---|---|---|
| `nodes` | máy (host + user OS) và tài khoản Telegram của máy đó | `(host, os_user)` |
| `peers` | user/group/channel, dùng chung mọi tài khoản | `id` |
| `dialogs` | danh sách hội thoại theo tài khoản | `(account_id, peer_id)` |
| `messages` | nội dung tin | `(account_id, chat_id, id)` |
| `media` | metadata đính kèm, `file_key` | `(account_id, chat_id, message_id)` |
| `media_files` | bản sao đã tải trên từng máy | `(account_id, chat_id, message_id, host, os_user)` |
| `sync_state` | tin mới nhất và thời điểm hỏi Telegram gần nhất | `(account_id, chat_id)` |
| `sync_ranges` | các khoảng ID đã lưu liên tục | `(account_id, chat_id, start_id)` |
| `schema_migrations` | migration đã chạy | `version` |

## File liên quan

| File | Vai trò |
|---|---|
| `src/tele_cli/store/migrations.py` | Tuple `MIGRATIONS`: mỗi phần tử là một phiên bản schema. `SCHEMA_VERSION = len(MIGRATIONS)` |
| `src/tele_cli/store/database.py` | `Database` (lớp cơ sở, `_guard` đổi lỗi driver thành `store_error` exit 9, `transaction` lồng nhau, `migrate` có lock `tele:schema` và từ chối schema mới hơn code), `SQLiteDatabase`, `PostgresDatabase` (`_pyformat` đổi `?`/`:name` sang `%s`/`%(name)s`, kết nối lại khi rớt, `pg_advisory_xact_lock`), `open_database`, `fold`, `mask_password`, `split_statements` |
| `src/tele_cli/store/repository.py` | Class `Store`: nhóm nodes & accounts, peers & dialogs, messages & media, sync coverage, reads. Các câu SQL upsert dùng chung (`UPSERT_PEER`, `UPSERT_MESSAGE`, `UPSERT_MEDIA`) |
| `src/tele_cli/store/payloads.py` | Đổi dòng database thành JSON in ra |
| `src/tele_cli/store/__init__.py` | Export `Store`, `MessageQuery` |
| `src/tele_cli/config.py` | `database_url()`, `database_path()` |

## Thay đổi schema

1. **Thêm** một chuỗi mới vào cuối `MIGRATIONS` (`ALTER TABLE`, `CREATE INDEX`, backfill...).
   Không bao giờ sửa migration đã chạy.
2. Chỉ dùng SQL cả hai dialect hiểu: `BIGINT/INTEGER/TEXT/DOUBLE PRECISION`, `ON CONFLICT`;
   không `AUTOINCREMENT/SERIAL`, không `WITHOUT ROWID`.
3. Cập nhật câu đọc/ghi trong `repository.py` và nếu cần in ra thì `payloads.py`.
4. Kiểm tra với cả SQLite (`TELE_DB=/tmp/...`) và PostgreSQL
   (`make up COMPOSE="docker compose -p tele-test"`).

## Lưu ý khi sửa

- Placeholder dùng `?` hoặc `:name`; không viết `%s`. Ký tự `%` trong SQL được tự escape cho psycopg.
- Ghi nhiều câu liên quan phải bọc `store.transaction()`; transaction lồng nhau tự nhập vào transaction ngoài.
- Xoá file SQLite là an toàn: lần `tele get` sau sẽ lấy lại, file media trên đĩa vẫn được nhận lại.
