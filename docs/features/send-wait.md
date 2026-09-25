# 4. Gửi tin và chờ phản hồi

[← Bản đồ](../index.md)

Gửi tin nhắn văn bản thuần và chờ tin trả lời đầu tiên. Mọi tin gửi/nhận đều được lưu vào
database, nên `tele get` ngay sau đó sẽ tự biết có tin mới.

## Lệnh

| Lệnh | Việc |
|---|---|
| `tele send -u X (-m TEXT \| --stdin \| --file PATH) [--reply-to ID] [--link-preview] [--dry-run]` | Gửi văn bản (không parse Markdown/HTML), tối đa 4096 ký tự; `--dry-run` chỉ resolve người nhận |
| `tele wait -u X [--after ID] [--timeout 1..86400]` | Trả `event: "message"` khi có tin đến mới hơn `--after`, hoặc `event: "timeout"` |

Quy trình chuẩn: `tele send` → lấy `message.id` → `tele wait --after <id>` → `tele get`.

## Luồng xử lý

- **send** (`messaging.py:send_text`): resolve chat → nếu `--dry-run` trả `request` → gửi
  với `parse_mode=None` → lưu tin vừa gửi (`_stored_payload`) → trả `message`.
  Lỗi mạng khi đang gửi thành `delivery_unknown` (exit 7): **không tự gửi lại**.
- **wait** (`messaging.py:wait_for_message`): kết nối với `auto_reconnect=True`; mốc là
  `--after` hoặc ID tin mới nhất hiện tại; đăng ký handler `NewMessage(incoming=True)`
  **trước**, rồi quét lịch sử sau mốc để không lọt tin đến trong khoảng hở; sau đó chờ
  hàng đợi đến hết timeout. Bỏ qua tin gửi đi (`out`).

## File liên quan

| File | Vai trò |
|---|---|
| `src/tele_cli/telegram/messaging.py` | `send_text`, `wait_for_message`, `_stored_payload` (lưu tin rồi đọc lại từ database để JSON giống `get`) |
| `src/tele_cli/cli/tele.py` | Subcommand `send`, `wait`; `_message_text()` đọc `-m`/`--stdin`/`--file`, bỏ xuống dòng cuối, kiểm tra rỗng và giới hạn 4096 |
| `src/tele_cli/telegram/client.py` | `authorized_client(auto_reconnect=...)`, `resolve_chat` |
| `src/tele_cli/telegram/records.py` | `message_record` |
| `src/tele_cli/store/repository.py` | `save_messages`, `message` |
| `src/tele_cli/store/database.py` | `PostgresDatabase._execute` tự kết nối lại một lần khi kết nối bị rớt trong lúc `wait` chờ lâu |

## Lưu ý khi sửa

- Tin lưu bởi send/wait không thêm vào `sync_ranges` (có thể có khoảng hở); điều này làm
  `_is_fresh` trong `history.py` trả false để lần `get` sau hỏi Telegram. Giữ nguyên hành vi này.
- Chỉ gửi văn bản thuần; không thêm gửi file/sửa/xoá/forward nếu không được yêu cầu rõ.
