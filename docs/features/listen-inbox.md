# 5. Listener liên tục và inbox cho agent

[← Bản đồ](../index.md)

`tele listen` giữ một kết nối Telegram duy nhất, lưu tin đến từ whitelist vào database và không
kết thúc sau tin đầu tiên. Claude, Antigravity và Codex đọc cùng inbox bằng `tele-local`, nên
không cần MCP và không mở thêm Telegram connection khi chờ.

## Luồng chuẩn

```text
tele listen ──Telegram──► messages + inbox_events
                              │
agent ──tele-local inbox/wait─┘
agent ──tele send──Unix socket──► connection đang mở của tele listen
```

| Lệnh | Việc |
|---|---|
| `tele listen` | Chạy liên tục, lưu incoming message từ whitelist; log không chứa nội dung mặc định |
| `tele listen --print-events` | In thêm mỗi tin dưới dạng một dòng NDJSON |
| `tele-local inbox -l 20` | Xem các event mới nhất |
| `tele-local inbox --after N` | Đọc tiếp theo thứ tự thời gian sau cursor `N` |
| `tele-local wait --after N --timeout 900` | Chờ database có event mới, không gọi Telegram; không có listener sống thì báo `listener_not_running` (exit 12) |
| `tele-local wait --after N -s 30` | Có tin rồi thì gom tiếp tới khi 30 giây không có tin mới (mặc định; tối đa 4 × 30 giây, `-s 0` trả ngay) |
| `tele-local wait --timeout 900` | Snapshot cursor hiện tại rồi chỉ chờ tin đến sau đó |
| `tele-local wait --from nam172` | Chỉ tin của một người gửi, kể cả trong group; dùng cả với `inbox` |
| `tele-local wait --consumer claude-nam172` | Cursor có tên lưu trong DB (`inbox_consumers`): thiếu `--after` thì đọc tiếp sau nó, trả tin xong tự tiến; tên mới bắt đầu từ event mới nhất |
| `tele-local wait -u <chat>` | Chat ngoài `TELE_LISTEN_CHATS` báo ngay `chat_not_listened` (exit 2); username/ID so chéo qua peer đã cache, whitelist trống thì bỏ qua |
| `tele-local listeners` | Xem process listener và heartbeat |
| `tele send ...` | Có file socket thì gửi qua listener, không có thì tự kết nối Telegram |

`event_id` tăng dần trong phạm vi một Telegram account và được lưu bền vững. Một message
chỉ tạo một `inbox_events` row, nên reconnect hoặc `catch_up()` không tạo task trùng. Cursor cấp
từ `inbox_sequence` chứ không từ `MAX(event_id)`: khi `tele get` thấy tin đã bị xoá, event của nó
bị xoá theo (`ON DELETE CASCADE`) nhưng cursor đó không bao giờ bị cấp lại.

## Listener

`listener.py:listen_forever` thực hiện:

1. từ chối listener thứ hai nếu heartbeat hiện tại còn sống;
2. resolve `TELE_LISTEN_CHATS` và đăng ký `NewMessage(chats=..., incoming=True)`;
3. gọi `catch_up()` sau khi handler đã được đăng ký;
4. ghi message và inbox cursor trong cùng transaction, trên thread và connection DB riêng để
   database chậm không chặn event loop giữ kết nối Telegram;
5. giữ event loop bằng `run_until_disconnected()` và heartbeat mỗi 10 giây;
6. mở private Unix socket để `tele send` dùng chung Telegram client.

Whitelist là danh sách hội thoại, nên hỗ trợ cả chat 1-1 và group/channel:

```dotenv
# ~/.config/tele/.env
TELE_LISTEN_CHATS=amacvn,123456789,-1001234567890
```

Numeric marked ID ổn định hơn username và cần thiết cho private group không có username; lấy ID
bằng `tele dialogs`. Danh sách trống hoặc có target không resolve được làm listener fail-closed,
không tự chuyển sang nghe mọi chat. Đây chỉ là lọc nguồn tin; phân quyền agent xử lý/gửi tin là lớp
riêng.

## Chạy bằng systemd user service

```bash
make install
make start-listener
loginctl enable-linger "$USER"
make listener-status
make listener-logs
```

Unit chạy `<INSTALL_DIR>/tele listen` (mặc định `~/bin/tele`; `make install` điền đường
dẫn theo `INSTALL_DIR`) với `Restart=always`: thoát ngoài ý muốn thì chạy lại, khoảng chờ giãn dần
từ 5 giây tới 5 phút; `systemctl stop` không kích hoạt restart. Exit 2, 3, 4, 5 (cấu hình sai,
session chưa đăng nhập, chat không resolve được) không tự restart: sửa cấu hình rồi
`make start-listener`. Exit 11 (lease đang thuộc listener khác) vẫn thử lại, nên máy này tự nhận
lease khi listener kia dừng. Journal mặc định không có nội dung.
Đổi socket bằng `TELE_LISTENER_SOCKET`; mặc định là
`~/.local/share/tele/listener.sock` với quyền chỉ user hiện tại truy cập.

Vận hành:

- **Cài lại binary**: systemd không theo dõi file binary, process cũ chạy tiếp code cũ.
  `make install` tự `systemctl --user restart` service nếu nó đang
  chạy (`restart_listener` trong `Makefile`); cài bằng cách khác thì tự restart.
- **`.env`**: binary chỉ đọc `~/.config/tele/.env` (hoặc `TELE_ENV_FILE`). `make install`
  copy `.env` của project sang đó **một lần**, không ghi đè; sửa `.env` sau đó phải sửa cả file
  này rồi restart service.
- **Sau SIGKILL/OOM**: lease cùng host/user có PID đã chết được thu hồi ngay. Socket cũ bị từ chối
  kết nối làm `tele send` fallback sang kết nối Telegram trực tiếp.
- **Heartbeat/lease**: IPC không giữ store lock trong lúc chờ network. Heartbeat phải update đúng
  một row; nếu process mất lease thì dừng với `listener_lease_lost` (exit 11).
- **Lỗi xử lý message**: không lấy được chat hoặc người gửi từ Telegram thì tin vẫn được lưu (chat
  lấy từ whitelist, người gửi chỉ còn `sender_id`). Lỗi khác được thử lại 3 lần (chờ 1 s, 2 s);
  vẫn hỏng thì lỗi DB/kết nối làm listener dừng để systemd restart, lỗi còn lại được log
  `message_error` rồi chạy tiếp.
- **Lệnh `tele` chạy song song**: `get`, `dialogs`, `status`, `wait` và `send` trực tiếp dùng bản
  sao session trong bộ nhớ (`client._SessionSnapshot`), nên không giữ khoá file session và không
  ghi đè update state của listener; entity mới học được ghi lại vào file khi đóng.
- **Sau khi request đã ghi vào socket**: timeout, EOF hoặc acknowledgement hỏng đều là
  `delivery_unknown` (exit 7). Kiểm tra hội thoại trước khi gửi lại.

## Cách agent dùng

Agent giữ `cursor.after_event_id` trong working context:

```bash
tele-local inbox --after 0 -l 20 --meta
tele-local wait --after 42 --timeout 900 --meta
tele-local wait -u 123456789 --consumer claude-123456789 --timeout 900 --meta
tele-local messages -u 123456789 -l 20 --meta
tele send -u 123456789 --reply-to 987 -m "Nội dung"
```

`wait` gom các tin gửi liên tiếp theo `--settle` (mặc định 30 giây im lặng), nên agent nhận đủ
một yêu cầu bị tách thành nhiều tin thay vì phản hồi theo tin đầu tiên. Thời gian im lặng tính từ
`received_at` của tin mới nhất, nên backlog cũ trả về ngay.

Với `--consumer`, cursor nằm trong database: agent chỉ cần nhớ tên (một tên cho mỗi luồng việc,
không dùng chung cho hai waiter chạy song song). Cursor của tên đó được lưu ngay khi lệnh bắt đầu,
nên tin đến giữa hai lần gọi không bị bỏ sót, kể cả khi lần trước timeout.

Nếu agent bị restart, gọi lại `inbox --after <cursor cuối>` để lấy phần chưa xử lý. Không dùng
`tele wait` khi listener đang chạy: lệnh cũ vẫn là fallback one-shot cho máy chưa chạy daemon.

## File liên quan

| File | Vai trò |
|---|---|
| `src/tele_cli/telegram/listener.py` | Telegram event handler, heartbeat, Unix socket, IPC send |
| `src/tele_cli/telegram/messaging.py` | Gửi trực tiếp hoặc qua client đang mở |
| `src/tele_cli/store/migrations.py` | `inbox_events`, `listeners` |
| `src/tele_cli/store/repository.py` | cursor, dedupe, lease và query inbox |
| `src/tele_cli/cli/tele.py` | `tele listen`, IPC-first `tele send` |
| `src/tele_cli/cli/local.py` | `inbox`, `wait`, `listeners` |
| `systemd/tele-listener.service` | user service chạy listener 24/7 |
