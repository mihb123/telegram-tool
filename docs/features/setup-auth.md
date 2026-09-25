# 1. Thiết lập, đăng nhập, kiểm tra session

[← Bản đồ](../index.md)

Lưu API ID/hash của Telegram, đăng nhập tài khoản cá nhân (tạo file session), và kiểm tra
session còn hợp lệ không.

## Lệnh

| Lệnh | Việc |
|---|---|
| `tele configure [--api-id] [--api-hash]` | Hỏi (hoặc nhận) API ID/hash, ghi `~/.config/tele/config.json` quyền 600 |
| `tele auth [--phone]` | Đăng nhập tương tác (số điện thoại, mã OTP, mật khẩu 2FA). Bắt buộc chạy trong terminal |
| `tele status` | Kết nối Telegram, trả tài khoản đang đăng nhập, tên máy và database đang dùng |

## Luồng xử lý

1. `cli/tele.py:dispatch` — `configure` chạy trước mọi thứ khác (không cần database). Các
   lệnh còn lại gọi `_credentials_or_error()` rồi mở `Store`.
2. `auth` kiểm tra `sys.stdin.isatty()`; không có terminal thì báo
   `interactive_terminal_required` (exit 6).
3. `telegram/client.py:authorize` / `status` kết nối, lấy `get_me()`, gọi
   `store.register_node(account)` để ghi máy này đang dùng tài khoản nào (xem
   [nodes-accounts.md](nodes-accounts.md)).
4. Sau mỗi lần kết nối, `secure_session_file()` đặt lại quyền 600 cho file session.

## File liên quan

| File | Vai trò |
|---|---|
| `src/tele_cli/cli/tele.py` | Khai báo subcommand `configure`, `auth`, `status`; `_credentials_or_error()` đổi `ConfigError` thành `TeleError` |
| `src/tele_cli/telegram/client.py` | `_client()` tạo `TelegramClient` (retry, timeout, không tự ngủ khi flood); `authorized_client()` context manager kết nối + kiểm tra đã đăng nhập + map lỗi mạng/flood/RPC; `authorize()`; `status()` |
| `src/tele_cli/config.py` | `validate_credentials`, `load_credentials` (biến môi trường `TELE_API_ID`/`TELE_API_HASH` thắng file), `save_credentials` (ghi atomic, quyền 600), `session_path`, `secure_data_directory`, `secure_session_file` |
| `src/tele_cli/telegram/records.py` | `peer_record()` đổi entity tài khoản thành bản ghi `peers` |
| `src/tele_cli/store/repository.py` | `Store.register_node()` lưu tài khoản vào `peers` và `nodes` |

## Lưu ý khi sửa

- Không bao giờ in API hash hay đường dẫn session ra log/JSON ngoài những gì đang có.
- Mỗi server cần session riêng; không copy file `.session` giữa các máy.
- `flood_sleep_threshold=0`: Telethon không tự chờ, lỗi flood được trả về ngay với
  `retry_after_seconds` (exit 75) để agent tự quyết định.
