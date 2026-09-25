# 2. Đọc tin nhắn, ưu tiên đọc database (`tele get`)

[← Bản đồ](../index.md)

Trả về `-l` tin mới nhất của một chat. Nếu database đã đủ dữ liệu và còn "tươi" thì trả
thẳng từ database, **không kết nối Telegram**; nếu không thì chỉ lấy phần còn thiếu.

## Lệnh

`tele get -u <target> [-l 1..100] [--max-age GIÂY] [--refresh] [--download-media] [-d DIR] [--max-media-mb N] [--format json|text]`

## Luồng xử lý (`telegram/history.py:get_messages`)

1. Nếu máy đã biết tài khoản (`store.account_id`), tìm chat trong database bằng
   `store.find_peer()`.
2. Trả từ database (`cache.source = "local"`) khi đủ ba điều kiện:
   - `_is_fresh`: `sync_state.checked_at` trong vòng `--max-age` giây và chưa có tin nào mới
     hơn `head_message_id` được lưu (ví dụ do `send`/`wait`);
   - `_covers_latest`: khoảng liên tục chứa tin mới nhất (`head_range`) có đủ `-l` tin
     hoặc bắt đầu từ đầu lịch sử (`start_id = 0`);
   - nếu có `--download-media`: mọi media đều giải quyết được tại máy (xem
     [media-download.md](media-download.md)).
3. Ngược lại, mở một kết nối và dùng `_Sync`:
   - `newer(limit)`: lấy tin mới hơn head (tối đa `limit`), cập nhật head;
   - `older(limit)`: nếu khoảng head chưa đủ `limit` tin thì lấy thêm tin cũ hơn;
   - `refresh(limit)` (khi `--refresh`): lấy lại cả cửa sổ, xoá tin đã bị xoá trên Telegram.
4. `_Sync._save()` trong một transaction: khoá chat (`lock_chat`), xoá tin local không còn
   trên Telegram trong khoảng đã lấy đầy đủ, upsert tin, thêm/gộp khoảng vào `sync_ranges`,
   đặt head.
5. Đọc lại cửa sổ bằng `_latest_window()` và dựng JSON bằng `_payload()` (khối `cache`,
   `media_summary`).

## File liên quan

| File | Vai trò |
|---|---|
| `src/tele_cli/telegram/history.py` | Toàn bộ logic đọc database trước / đồng bộ phần thiếu: `_is_fresh`, `_covers_latest`, `_latest_window`, class `_Sync`, `_payload`, `get_messages` |
| `src/tele_cli/cli/tele.py` | Subcommand `get`, mặc định `--max-age` lấy từ `TELE_CACHE_MAX_AGE` (`_default_max_age`), giới hạn `-l` 1..100 |
| `src/tele_cli/telegram/client.py` | `authorized_client()`, `resolve_chat()` (resolve target, lưu chat vào `peers`, ghi nhận máy lần đầu) |
| `src/tele_cli/telegram/records.py` | `message_record()` / `media_record()` đổi tin Telethon thành bản ghi theo bảng |
| `src/tele_cli/store/repository.py` | `save_messages`, `delete_messages_except`, `lock_chat`, `sync_state`, `set_head`, `add_range`, `head_range`, `count_messages`, `newest_message_id`, `latest_messages`, `MAX_MESSAGE_ID` |
| `src/tele_cli/store/migrations.py` | Bảng `messages`, `sync_state`, `sync_ranges` |
| `src/tele_cli/store/payloads.py` | `messages_payload`, `peer_payload` dựng JSON gọn |
| `src/tele_cli/output.py` | `render_messages` cho `--format text` |

## Lưu ý khi sửa

- Bất biến của `sync_ranges`: mọi tin có `start_id <= id <= end_id` **đều có** trong
  `messages`. Chỉ gọi `add_range` cho khoảng Telegram đã trả về đầy đủ.
- `delete_messages_except` chỉ được gọi trên khoảng `[complete_from, complete_to]` đã lấy
  đủ, nếu không sẽ xoá nhầm tin chưa lấy.
- Lần đầu chạy trên một máy (chưa có `account_id`) luôn kết nối Telegram.
- Nhiều máy có thể sync cùng chat: mọi ghi phải nằm trong `store.transaction()` và sau
  `lock_chat()`.
