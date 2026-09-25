# 8. Nhiều máy, nhiều tài khoản dùng chung database

[← Bản đồ](../index.md)

Nhiều server có thể trỏ vào cùng một PostgreSQL, kể cả khi đăng nhập **các tài khoản Telegram
khác nhau**. Mỗi máy được định danh bằng "node" = hostname + user OS.

## Cơ chế

- **Node**: `config.current_node()` trả `Node(socket.gethostname(), getpass.getuser())`.
- **Máy ↔ tài khoản**: bảng `nodes`. `Store.__init__` đọc `account_id` của node hiện tại;
  `None` nghĩa là máy này chưa từng kết nối Telegram.
- **Ghi nhận tài khoản**: `register_node()` được gọi bởi `tele auth`, `tele status` và lần kết
  nối đầu tiên trong `resolve_chat()`; các lần sau chỉ `touch_node()` cập nhật `last_seen_at`.
- **Tách dữ liệu**: `messages`, `media`, `media_files`, `dialogs`, `sync_*` đều có
  `account_id`; mọi truy vấn trong `Store` lọc theo `self.account`. `peers` dùng chung.
- **Bản sao file**: `media_files` ghi `(host, os_user, path)`; chỉ bản của node hiện tại được
  coi là dùng được.
- **Máy không có session** (`tele-local` trên server chỉ đọc): `require_account()` dùng tài
  khoản duy nhất trong `nodes`; nhiều tài khoản thì báo `ambiguous_account`, chọn bằng
  `tele-local --account ID`.
- **Đồng thời**: `lock_chat()` và lock migration dùng `pg_advisory_xact_lock` trên PostgreSQL;
  SQLite dùng `BEGIN IMMEDIATE`.

## File liên quan

| File | Vai trò |
|---|---|
| `src/tele_cli/config.py` | Dataclass `Node`, `current_node()` |
| `src/tele_cli/store/repository.py` | `Store.__init__` (đọc account của node), `register_node`, `touch_node`, `require_account`, `nodes`, `account`, `node_copies`, `record_file`, `lock_chat` |
| `src/tele_cli/telegram/client.py` | `resolve_chat` (ghi nhận node lần đầu), `authorize`, `status` |
| `src/tele_cli/cli/local.py` | Option `--account`, lệnh `info` in danh sách node |
| `src/tele_cli/store/database.py` | `lock()` cho từng dialect |
| `src/tele_cli/store/migrations.py` | Bảng `nodes`, `media_files` |
| `src/tele_cli/store/payloads.py` | `media_payload` tách bản của node hiện tại (`local_path`) với mọi node (`files`) |

## Lưu ý khi sửa

- ID tin nhắn chỉ duy nhất trong một tài khoản + chat: mọi bảng dữ liệu tin mới phải có
  `account_id` trong khoá chính.
- Không dùng `store.account` trước khi đã có tài khoản (sẽ ném `RuntimeError`).
