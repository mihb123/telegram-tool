# 5. Danh sách hội thoại (`tele dialogs`)

[← Bản đồ](../index.md)

Liệt kê các hội thoại gần nhất cùng ID, username, số tin chưa đọc. Dùng để tìm ID của group
riêng tư (không có username) và để `tele-local` resolve target mà không cần Telegram.

## Lệnh

`tele dialogs [-l 1..100] [--format json|text]`

## Luồng xử lý

`dialogs.py:fetch_dialogs` → `client.iter_dialogs(limit)` → đổi mỗi dialog thành
`(peer_record, unread_count, last_message_date)` → `store.save_dialogs()` (upsert `peers` và
`dialogs`) → trả danh sách.

## File liên quan

| File | Vai trò |
|---|---|
| `src/tele_cli/telegram/dialogs.py` | `fetch_dialogs` |
| `src/tele_cli/cli/tele.py` | Subcommand `dialogs`; `_render_text` chọn cột cho bảng text |
| `src/tele_cli/telegram/records.py` | `peer_record` |
| `src/tele_cli/store/repository.py` | `save_dialogs`, `save_peers`; `chats()` dùng bảng `dialogs` cho `tele-local chats` |
| `src/tele_cli/store/migrations.py` | Bảng `peers`, `dialogs` |

## Lưu ý khi sửa

- `save_dialogs` dùng `self.account`, nên phải có tài khoản; `authorized_client` đã kết nối
  nhưng lệnh này **không** gọi `resolve_chat`. Nếu máy chưa từng chạy `auth`/`status`/`get`
  thì `account_id` có thể là `None`.
