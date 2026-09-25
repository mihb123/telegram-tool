# 6. Truy vấn dữ liệu đã lưu (`tele-local`)

[← Bản đồ](../index.md)

CLI chỉ đọc database mà `tele` đã ghi. **Không bao giờ gọi Telegram, không import Telethon.**
Chat chưa từng được `tele get` sẽ báo `not_cached` (exit 5).

## Lệnh

| Lệnh | Việc | Hàm xử lý |
|---|---|---|
| `chats [-l]` | Các chat đã lưu, hoạt động gần nhất trước | `store.chats()` |
| `messages -u X [--before\|--after\|--around ID] [--since] [--until] [--from] [--incoming\|--outgoing] [--with-media] [--oldest-first]` | Tin của một chat theo bộ lọc | `_messages()` → `store.messages()` |
| `search "từ khoá" [-u X] [--since] [--until] [--from]` | Tìm trong nội dung và tên file, không phân biệt hoa thường/dấu | `_search()` → `store.messages(terms=...)` |
| `media [-u X] [--type] [--downloaded\|--missing]` | Media và máy nào giữ file | `_media()` → `store.media_list()` |
| `sql "SELECT ..." [--max-rows]` | SQL chỉ-đọc tuỳ ý | `_sql()` → `db.read_only_query()` |
| `info` | Backend, schema version, kích thước, số bản ghi, các máy | `store.info()`, `store.nodes()` |

Option chung: `--account ID` chọn tài khoản; `--format text` in bảng tab.

## Luồng xử lý

`cli/local.py:dispatch` mở `Store` → `info` và `sql` chạy ngay (không cần tài khoản) → các
lệnh khác gọi `store.require_account()` (xem [nodes-accounts.md](nodes-accounts.md)) → resolve
`-u`/`--from` bằng `_peer()` (chỉ tra database) → dựng `MessageQuery` → trả JSON qua
`store/payloads.py`.

- `--around ID`: nửa `limit` lấy các tin `<= ID`, nửa còn lại lấy tin `> ID`, rồi sắp xếp lại.
- `search`: `fold(query).split()` thành các từ; mỗi từ phải khớp `fold(text)` hoặc
  `fold(media.name)` với `LIKE` (đã escape `%`, `_`).
- `sql`: SQLite mở kết nối riêng `mode=ro`; PostgreSQL chạy trong transaction
  `READ ONLY` với `statement_timeout = 30s`. Lấy `max_rows + 1` dòng để biết `truncated`.

## File liên quan

| File | Vai trò |
|---|---|
| `src/tele_cli/cli/local.py` | Parser `tele-local`, `_add_time_filters`, `_peer` (lỗi `not_cached`), `_query`, `_messages`, `_search`, `_media`, `_sql`, `dispatch`, `_render_text` |
| `src/tele_cli/cli/args.py` | `bounded_int`, `positive_message_id`, `time_bound`, `add_target`, `add_format` |
| `src/tele_cli/values.py` | `parse_target`, `parse_time_bound` (`30m`, `2h`, `3d`, `1w`, ngày, ISO; `--until` với ngày trần = hết ngày đó) |
| `src/tele_cli/store/repository.py` | `MessageQuery`, `MESSAGE_SELECT`, `messages`, `chats`, `media_list`, `files_for`, `find_peer`, `sync_state`, `info`, `nodes`, `require_account` |
| `src/tele_cli/store/database.py` | `fold()` (Python, đăng ký vào SQLite), `_fold_translation` + `after_migrate` (tạo `fold()` trong PostgreSQL), `read_only_query` từng dialect, `invalid_sql` |
| `src/tele_cli/store/payloads.py` | `compact`, `peer_payload`, `media_payload`, `messages_payload` (`include_chat` khi tìm trên mọi chat) |
| `src/tele_cli/output.py` | `render_messages`, `render_table` |
| `bin/tele-local`, `scripts/entrypoints/tele_local.py` | Điểm vào từ source / binary |

## Lưu ý khi sửa

- Không import gì từ `tele_cli.telegram` trong `cli/local.py` hay `store/`.
- Thêm bộ lọc mới: thêm trường vào `MessageQuery` và mệnh đề trong `Store.messages()`, rồi
  thêm option trong `build_parser()`.
- Thời gian lưu dạng ISO-8601 UTC dạng text, nên so sánh chuỗi = so sánh thời gian.
