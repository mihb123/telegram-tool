# tele

Hai CLI dùng tài khoản Telegram cá nhân, kết quả mặc định là JSON để agent dễ xử lý:

- **`tele`**: đọc, gửi và giữ một listener Telegram liên tục. Mọi thứ đã lấy về được lưu
  vào database, lần sau **đọc database trước** và chỉ gọi Telegram cho phần còn thiếu.
- **`tele-local`**: truy vấn, lấy inbox và chờ tin đã được listener lưu. **Không bao giờ
  gọi Telegram**, không cần Telethon; phù hợp cho Claude, Antigravity và Codex.

Database là SQLite trên máy (mặc định) hoặc **PostgreSQL dùng chung** cho nhiều server
(khai báo `TELE_DATABASE_URL` trong `.env`, bật/tắt bằng `make up` / `make down`).

Test tự động nằm trong `tests/`; chạy `uv run pytest` và `make lint` trước khi build.

## Thiết lập lần đầu

Tạo API ID và API hash tại <https://my.telegram.org/apps>, sau đó chạy:

```bash
tele configure
tele auth
tele status
```

Không chia sẻ `~/.config/tele/config.json` hoặc `~/.local/share/tele/telegram.session`.
Mỗi server cần session riêng: chạy `tele auth` trên từng server.

## Database dùng chung (PostgreSQL)

Một máy chạy PostgreSQL (Docker), các server khác trỏ `TELE_DATABASE_URL` về máy đó. Tin
nhắn, media đã tải và trạng thái đồng bộ được dùng chung: server này đã lấy thì server kia
đọc thẳng từ database, không gọi lại Telegram.

**Trên máy chạy database** (cần Docker + Compose):

```bash
make env      # tạo .env từ .env.example, mật khẩu ngẫu nhiên
# sửa .env nếu cần: TELE_DB_BIND (0.0.0.0 = cho server khác kết nối), TELE_DB_PORT
make up       # bật PostgreSQL, chờ sẵn sàng, in trạng thái kết nối
make client-env   # in dòng TELE_DATABASE_URL để dán sang server khác
```

**Trên mỗi server khác**, đặt dòng in ra ở trên vào một trong các file `.env` dưới đây
(thay hostname bằng IP nếu server kia không phân giải được tên máy), rồi chạy
`tele status` để kiểm tra:

- `~/.config/tele/.env` khi chỉ cài binary `~/bin/tele`;
- `.env` trong thư mục source nếu chạy `bin/tele` từ repo.

`tele` đọc `.env` theo thứ tự ưu tiên: biến đã `export` trong shell → `$TELE_ENV_FILE` →
`.env` của source checkout → `~/.config/tele/.env`. Xem đủ các biến trong
[`.env.example`](.env.example). Để trống `TELE_DATABASE_URL` là dùng SQLite local.

| Lệnh | Việc |
|---|---|
| `make up` / `make down` | Bật / tắt database (dữ liệu giữ trong Docker volume) |
| `make restart`, `make status`, `make logs` | Khởi động lại, xem trạng thái, xem log |
| `make psql` | Mở psql vào database |
| `make backup` | Dump ra `backups/tele-<thời gian>.sql.gz` |
| `make sync-db` | Kéo database remote về PostgreSQL local của máy này, giữ bản dump `backups/remote-<thời gian>.sql.gz` ([chi tiết](docs/features/shared-postgres.md#bản-sao-local-make-sync-db)) |
| `make client-env` | In `TELE_DATABASE_URL` cho server khác |
| `make build` | Build binary vào `dist/` |
| `make install` | Build, cài binary vào `~/bin` và systemd user service cho `tele listen` (listener đang chạy thì tự restart) |
| `make start-listener` / `stop-listener` / `restart-listener` | Bật / tắt / khởi động lại listener |
| `make listener-status`, `make listener-logs` | Trạng thái và log của listener |
| `make lint` | `ruff check` + kiểm tra format |

Bảo mật: PostgreSQL chỉ có mật khẩu, không mã hoá kết nối. Khi `TELE_DB_BIND=0.0.0.0`, chỉ mở
port trong mạng nội bộ/VPN (firewall), không để lộ ra internet. `.env` chứa mật khẩu và đã
được `.gitignore`.

Các server đăng nhập **tài khoản Telegram khác nhau** vẫn dùng chung được: dữ liệu được tách
theo tài khoản (ID tin nhắn chỉ duy nhất trong một tài khoản). Bảng `nodes` ghi mỗi máy
(hostname + user OS) đang dùng tài khoản nào; xem bằng `tele-local info`.

## `tele`

`-u` nhận username, `@username`, `me` hoặc ID user/group/channel như `-5539294370`.

Mọi flag đều có short: chữ cái đầu (`--timeout` → `-t`), trùng thì 2 chữ cái đầu (`-fo` cho
`--format` khi `-f` đã là `--from`), vẫn trùng thì chỉ có dạng dài. Short có thể khác nhau giữa
các lệnh, xem `tele <lệnh> -h` / `tele-local <lệnh> -h`.

| Việc cần làm | Lệnh |
|---|---|
| Lấy 10 tin gần nhất | `tele get -u amacvn -l 10` |
| Kèm đủ thông tin (người gửi, reply, forward, media...) | `tele get -u amacvn -l 10 --meta` |
| Đọc group | `tele get -u -5539294370 -l 10` |
| Tải cả media | `tele get -u amacvn -l 10 --download-media` |
| Bắt buộc hỏi Telegram có tin mới | `tele get -u amacvn -l 10 --max-age 0` |
| Lấy lại cả cửa sổ (cập nhật tin sửa/xoá) | `tele get -u amacvn -l 10 --refresh` |
| Gửi tin nhắn | `tele send -u amacvn -m "Nội dung"` |
| Reply một tin | `tele send -u amacvn --reply-to 12345 -m "Nội dung"` |
| Chờ phản hồi | `tele wait -u amacvn --after 12345 --timeout 900` |
| Giữ kết nối và lưu mọi tin đến | `tele listen` |
| Tìm ID hội thoại | `tele dialogs -l 50` |
| Kiểm tra session và database | `tele status` |

Tin mới nhất được trả về trước; `-l` nhận từ 1 đến 5000. Dùng `--format=text` nếu muốn
đọc dạng chữ. JSON in mỗi tin trên một dòng, mặc định chỉ gồm `id`, `time`, `sender` (tên người
gửi), `type`, `text` và `path` khi file media đã có trên máy này (không có file vì bị bỏ qua,
lỗi hoặc đã xoá thì in `download_status`). `time` và các mốc thời gian khác in dạng
`yyyy-mm-dd HH:MM:SS` theo giờ UTC+7 (đổi bằng biến `TELE_TIMEZONE`, vd. `+9`, `UTC-05:30`,
`Asia/Tokyo`):

```json
{"id": 196639, "time": "2026-09-25 17:15:39", "sender": "Minh Chu", "type": "photo", "text": "Đơn cần ra 17h"}
{"id": 190585, "time": "2026-09-21 16:03:23", "sender": "C Linh", "type": "sticker", "text": "", "emoji": "👌"}
{"id": 193632, "time": "2026-09-23 15:55:01", "sender": "C Linh", "type": "service", "action": "phone_call", "text": ""}
```

`type` cho biết tin là gì:

| `type` | Nghĩa |
|---|---|
| `text` | tin chữ (kể cả tin có link preview) |
| `emoji` | tin chỉ gồm emoji, vd. `😂` |
| `photo`, `video`, `gif`, `video_note` (video tròn), `voice` (ghi âm), `audio`, `file` | tin có đính kèm |
| `sticker` | sticker, kèm `emoji` mà sticker đại diện |
| `location`, `contact`, `poll`, `dice`, ... | các loại đính kèm khác |
| `service` | tin hệ thống, kèm `action`: `pin_message`, `phone_call`, `chat_add_user`, ... |

`type` được lưu ở cột `messages.kind`, nên lọc thẳng được không cần JOIN `media`, vd.
`tele-local sql "SELECT chat_id, id, date FROM messages WHERE kind = 'photo'"`.

Mọi thông tin khác vẫn được lưu trong database. Thêm `--meta` (cả ở `tele-local messages` /
`search`) để lấy đủ: `sender` (id, username, name), `outgoing`, `edit_date`,
`reply_to_message_id`, `grouped_id`, `action`, `forward`, `reactions`, `views`, `links` (link ẩn
sau chữ), `link_preview`, `sticker_emoji`, chi tiết `media` (`download_status`, `local_path`,
`files`...).

### Đọc database trước

`tele get` trả thẳng từ database, **không kết nối Telegram**, khi đủ cả ba điều kiện:

1. chat đã được kiểm tra với Telegram (từ bất kỳ server nào cùng tài khoản) trong vòng
   `--max-age` giây (mặc định 300, đổi bằng biến `TELE_CACHE_MAX_AGE`);
2. từ đó chưa có tin nào mới hơn được lưu (ví dụ do `tele send` / `tele wait`);
3. database có đủ `-l` tin liên tục tính từ tin mới nhất (hoặc đã có toàn bộ lịch sử chat).

Nếu không, `tele` chỉ lấy phần thiếu: các tin mới hơn tin mới nhất đã biết, rồi các tin cũ
hơn nếu chưa đủ `-l`. Tin đã lấy không bao giờ bị tải lại, trừ khi dùng `--refresh`
(lấy lại cả cửa sổ để cập nhật nội dung đã sửa và xoá tin đã bị xoá trên Telegram).
Lần đầu chạy trên một máy, `tele` luôn kết nối Telegram một lần để biết máy đó đăng nhập
tài khoản nào.

Khối `cache` trong kết quả cho biết nguồn dữ liệu:

```json
"cache": {"source": "local", "synced_at": "2026-09-25T13:34:51+00:00", "fetched_from_telegram": 0}
```

### Media

Media mặc định được giữ tại `~/telegram-files/<target>/`. Dùng `-d <folder>` để chọn thư
mục khác hoặc đặt `TELE_MEDIA_DIR` để đổi thư mục gốc. Giới hạn mặc định 100 MB mỗi file,
đổi bằng `--max-media-mb N` (`0` = không giới hạn).

Mỗi file tải về được ghi lại **máy nào đang giữ** (hostname + user OS) và đường dẫn:

Với `--meta`:

```json
"media": {"type": "photo", "size": 24953, "download_status": "existing",
          "local_path": "/home/mihb/telegram-files/amacvn/196671_photo.jpg",
          "files": [{"host": "X1CB7", "user": "mihb", "path": "/home/mihb/telegram-files/amacvn/196671_photo.jpg"}]}
```

`files` liệt kê bản sao trên mọi máy; `local_path` chỉ có khi máy đang chạy lệnh giữ một
bản sao còn trên đĩa. Máy khác không đọc được đĩa của nhau, nên `--download-media` trên
máy B vẫn tải bản của B (một lần) và ghi thêm một dòng cho B.

Trước khi tải, `tele` tìm file sẵn có **trên máy hiện tại** theo thứ tự: file đã ghi nhận
cho tin đó → cùng file Telegram (ảnh/tài liệu được gửi lại, forward) đã tải ở tin khác →
file cùng tên từ lần chạy trước. Chỉ phần còn thiếu mới được tải. Với `-d`, file đã có ở
thư mục khác được hard link (khác ổ đĩa thì symlink) vào `-d`, không copy thêm bản nào.
`download_status` (đầy đủ trong `--meta`; bản rút gọn chỉ in khi thiếu file: `skipped_too_large`
kèm `max_size_bytes`, `error` kèm `download_error`, `file_missing`):
`downloaded`, `existing` (dùng được), `skipped_too_large`, `not_downloadable`, `error`,
`file_missing` (máy này đã ghi nhận file nhưng file bị xoá khỏi đĩa).

### Gửi và chờ phản hồi

`tele send` trả về `message.id`. Truyền ID này vào `tele wait --after` để không bỏ sót phản
hồi đến nhanh:

```text
tele send → lấy message.id → tele wait --after ID
          → tele get --download-media → tiếp tục xử lý
```

`tele wait` kết thúc khi nhận một tin mới (`event: "message"`) hoặc hết thời gian
(`event: "timeout"`). Timeout mặc định 900 giây, tối đa 86400 giây. Tin nhận được và tin đã
gửi đều được lưu, nên `tele get` ngay sau đó sẽ tự hỏi Telegram phần tin mới.

Với nội dung quan trọng, kiểm tra người nhận trước bằng `--dry-run`.

### Listener 24/7 cho coding agent

`tele listen` không kết thúc sau tin đầu tiên. Nó giữ một Telegram connection, chỉ lưu incoming
message từ các hội thoại trong `TELE_LISTEN_CHATS`, và tạo cursor tăng dần trong `inbox_events`:

```bash
# ~/.config/tele/.env — username hoặc marked ID, hỗ trợ cả chat riêng và group
TELE_LISTEN_CHATS=amacvn,123456789,-1001234567890
tele listen
tele-local inbox -l 20 --meta
tele-local wait --after 42 --timeout 900 --meta
```

Thiếu hoặc để trống whitelist thì listener từ chối chạy. Dùng `tele dialogs` để tìm ID; nên
dùng numeric ID vì ổn định hơn username. Listener resolve toàn bộ danh sách trước khi ready và
fail-closed nếu có một target không hợp lệ.

`tele-local wait` chỉ chờ database nên Claude, Antigravity hoặc Codex không mở thêm kết nối
Telegram. Nếu agent restart, dùng lại `inbox --after <cursor cuối>` để lấy đủ backlog. Không có
listener nào còn heartbeat thì `wait` báo ngay `listener_not_running` (exit 12, kèm cursor) thay
vì chờ hết timeout; event đã lưu sau cursor vẫn được trả về trước.
`wait -u <chat>` với chat không nằm trong `TELE_LISTEN_CHATS` báo ngay `chat_not_listened`
(exit 2). Username và ID được so chéo qua peer đã cache: whitelist ghi `amacvn` thì
`wait -u 123456789` vẫn hợp lệ nếu ID đó là của `amacvn`, và ngược lại. Máy không khai báo
`TELE_LISTEN_CHATS` (chỉ đọc DB dùng chung) thì bỏ qua bước kiểm tra này.

Người gửi hay tách một yêu cầu thành nhiều tin liên tiếp. Khi tin đầu tiên đến, `wait` gom tiếp
cho tới khi `--settle`/`-s` giây (mặc định 30) không có tin mới, tối đa 4 lần khoảng đó tính từ
tin đầu, hoặc khi đủ `-l`, rồi trả cả lô. Backlog đã im lặng từ lâu trả về ngay; `-s 0` trả về
ngay khi có tin như trước.

`inbox` và `wait` nhận thêm:

- `--from nam172`: chỉ tin của một người gửi, kể cả trong group (kết hợp được với `-u`).
- `--consumer TÊN`: cursor có tên lưu trong database. Không truyền `--after` thì đọc tiếp sau
  cursor đó và tự đẩy cursor qua những tin vừa trả về; tên mới bắt đầu từ event mới nhất.
  Agent không cần tự nhớ cursor, restart cũng không mất. `--after` vẫn thắng để đọc lại.

```bash
tele-local wait -u amacvn --consumer claude-amacvn --timeout 900 --meta
tele-local wait -u -1001234567890 --from nam172 --consumer claude-nam172 --meta
```

Khi có socket của listener, `tele send` tự chuyển request qua private Unix socket của daemon,
tránh lỗi khóa Telethon session và vẫn giữ nguyên JSON contract cũ. Lỗi connect socket fallback
sang kết nối trực tiếp; lỗi sau khi request đã ghi vào socket là `delivery_unknown`, phải kiểm tra
hội thoại trước khi gửi lại. `tele wait` vẫn là fallback one-shot cho máy chưa chạy listener.
Các lệnh `tele` khác làm việc trên bản sao session trong bộ nhớ, nên chạy song song với listener
cũng không tranh khoá file session hay ghi đè update state của nó.

Chạy listener bằng systemd user service:

```bash
make install
make start-listener
loginctl enable-linger "$USER"  # tiếp tục chạy sau khi logout và tự lên sau reboot
make listener-status
```

`make install` tự restart listener nếu nó đang chạy, để dùng binary
vừa build (service không tự nhận ra binary đã đổi). Binary không đọc `.env` của source mà đọc
`~/.config/tele/.env`: `make install` chỉ copy `.env` sang đó ở lần đầu, sau này sửa
`.env` của project thì phải sửa cả file này rồi `make restart-listener` (hoặc `systemctl --user restart tele-listener`).

Mặc định journal chỉ có trạng thái ready/error, không có nội dung chat. Dùng
`tele listen --print-events` khi thực sự muốn xem từng event dưới dạng NDJSON. Thiết kế và
vận hành chi tiết ở [listen-inbox.md](docs/features/listen-inbox.md).

## `tele-local`

Chỉ đọc dữ liệu đã được `tele` lưu (SQLite local hoặc PostgreSQL dùng chung). Chat chưa
từng `tele get` sẽ báo lỗi `not_cached`.

| Việc cần làm | Lệnh |
|---|---|
| Danh sách chat đã lưu | `tele-local chats` |
| 20 tin gần nhất của một chat | `tele-local messages -u amacvn` |
| Tin trong 2 giờ qua, theo thứ tự thời gian | `tele-local messages -u amacvn --since 2h --oldest-first` |
| Ngữ cảnh quanh một tin | `tele-local messages -u amacvn --around 196395 -l 10` |
| Đọc tiếp sau một tin | `tele-local messages -u amacvn --after 196395` |
| Chỉ tin của một người trong group | `tele-local messages -u -5539294370 --from amacvn` |
| Chỉ tin có media / chỉ tin đã gửi | `--with-media`, `--outgoing`, `--incoming` |
| Tìm kiếm (không phân biệt hoa thường, dấu) | `tele-local search "doi soat"` |
| Tìm trong một chat, trong 1 tuần | `tele-local search "cong no" -u amacvn --since 1w` |
| Media có / chưa có file trên máy này | `tele-local media -u amacvn --downloaded` / `--missing` |
| Xem inbox do listener lưu | `tele-local inbox -l 20 --meta` |
| Đọc inbox sau cursor | `tele-local inbox --after 42 --meta` |
| Chờ event mới mà không gọi Telegram | `tele-local wait --after 42 --timeout 900` |
| Kiểm tra listener và heartbeat | `tele-local listeners` |
| SQL chỉ-đọc tuỳ ý | `tele-local sql "SELECT sender_id, COUNT(*) FROM messages GROUP BY 1"` |
| Database, số bản ghi, các máy đang dùng | `tele-local info` |
| Đọc dữ liệu của tài khoản khác | `tele-local --account 5159078318 chats` |

`--since` / `--until` nhận `30m`, `2h`, `3d`, `1w`, `2026-09-25` hoặc ISO datetime (không ghi
múi giờ thì hiểu theo `TELE_TIMEZONE`, cùng múi với `time` in ra; `--until` với ngày trần là hết
ngày đó). Trong SQL, hàm
`fold(text)` bỏ dấu và chữ hoa (có ở cả SQLite và PostgreSQL):
`WHERE fold(text) LIKE '%doi soat%'`. Mặc định `tele-local` đọc tài khoản đang đăng nhập
trên máy này; máy không có session thì dùng tài khoản duy nhất trong database hoặc
`--account`.

## Dữ liệu

| Dữ liệu | Vị trí mặc định | Biến môi trường |
|---|---|---|
| API credentials | `~/.config/tele/config.json` | `TELE_API_ID`, `TELE_API_HASH` |
| Session Telegram | `~/.local/share/tele/telegram.session` | `TELE_SESSION` |
| Socket listener | `~/.local/share/tele/listener.sock` | `TELE_LISTENER_SOCKET` |
| Hội thoại listener nhận | bắt buộc, không có mặc định | `TELE_LISTEN_CHATS` |
| Database dùng chung | — | `TELE_DATABASE_URL` (`postgresql://...`) |
| SQLite (khi không có URL) | `~/.local/share/tele/messages.db` | `TELE_DB` |
| File media | `~/telegram-files/<target>/` | `TELE_MEDIA_DIR` |
| File `.env` thêm | — | `TELE_ENV_FILE` |

File local được tạo với quyền chỉ chủ sở hữu đọc được. Database chỉ là bản lưu của Telegram:
xoá SQLite là an toàn, lần `tele get` sau sẽ tự lấy lại (file media trên đĩa vẫn được nhận
lại, không tải lại).

### Schema

Định nghĩa trong `src/tele_cli/store/migrations.py`, cùng một SQL cho SQLite và PostgreSQL.
Mỗi bảng tương ứng một loại dữ liệu Telegram trả về; dữ liệu tin nhắn có `account_id`:

| Bảng | Nội dung | Khoá |
|---|---|---|
| `nodes` | các máy (hostname + user OS) dùng database và tài khoản Telegram của máy đó | `(host, os_user)` |
| `peers` | user, group, channel (cả chat lẫn người gửi), dùng chung mọi tài khoản | `id` |
| `dialogs` | danh sách hội thoại: số chưa đọc, ngày tin cuối | `(account_id, peer_id)` |
| `messages` | nội dung tin, người gửi, reply, album (`grouped_id`), service action; các trường còn lại (forward, reactions, views, links...) ở cột JSON `meta` | `(account_id, chat_id, id)` |
| `media` | metadata file đính kèm: `type` (class Telegram), `kind` (photo, video, sticker, voice, file...), `file_key` để nhận ra cùng một file | `(account_id, chat_id, message_id)` |
| `media_files` | bản sao đã tải: máy nào (host, user) giữ, đường dẫn, kích thước | `(account_id, chat_id, message_id, host, os_user)` |
| `sync_state` | tin mới nhất của chat và thời điểm hỏi Telegram gần nhất | `(account_id, chat_id)` |
| `sync_ranges` | các khoảng ID tin đã lưu liên tục, không thiếu tin nào | `(account_id, chat_id, start_id)` |
| `inbox_events` | cursor bền vững của incoming message do listener nhận | `(account_id, event_id)` |
| `inbox_sequence` | cursor lớn nhất đã cấp cho mỗi tài khoản, để cursor không bao giờ bị cấp lại | `account_id` |
| `inbox_consumers` | cursor có tên của từng agent (`--consumer`) | `(account_id, name)` |
| `listeners` | process listener, thời điểm bắt đầu và heartbeat | `(account_id, host, os_user, process_id)` |
| `schema_migrations` | các migration đã chạy | `version` |

Thay đổi schema: **thêm** một phần tử mới vào cuối `MIGRATIONS` (không sửa migration cũ),
chỉ dùng SQL mà cả hai dialect hiểu. Mọi máy tự nâng cấp ở lần chạy tiếp theo; nhiều máy
khởi động cùng lúc vẫn an toàn (có lock).

## Cấu trúc dự án

Bản đồ tính năng → file liên quan (đọc trước khi sửa code): [`docs/index.md`](docs/index.md).

```text
.env.example            mẫu cấu hình (copy thành .env)
compose.yaml            PostgreSQL dùng chung (Docker)
Makefile                make up / down / status / backup / build / install ...
bin/                    launcher chạy từ source (dùng .venv): bin/tele, bin/tele-local
scripts/
  build-standalone      build binary vào dist/
  entrypoints/          entry point cho PyInstaller
src/tele_cli/
  cli/                  argparse + dispatch: tele.py, local.py, args.py, runner.py
  telegram/             mọi thứ dùng Telethon
    client.py           kết nối, auth, resolve chat, ghi nhận máy/tài khoản
    history.py          tele get: đọc database trước, chỉ lấy phần thiếu
    media.py            tải media, chống tải trùng, ghi host/user của file
    listener.py         tele listen, inbox event, heartbeat và Unix socket cho tele send
    messaging.py        tele send, tele wait
    dialogs.py          tele dialogs
    records.py          object Telethon → bản ghi theo bảng
  store/                database, không import Telethon
    migrations.py       schema theo phiên bản
    database.py         SQLite / PostgreSQL, chạy migration, hàm fold()
    repository.py       class Store: mọi câu đọc/ghi
    payloads.py         bản ghi → JSON gọn để in ra
  config.py             đường dẫn, .env, credentials, định danh máy
  output.py             in JSON / text
  values.py             parse target và thời gian
systemd/                user service chạy tele listen 24/7
```

Quy ước: `store/` và `cli/local.py` không được import `telegram/` hay Telethon; psycopg chỉ
được import khi dùng PostgreSQL, nên SQLite chạy chỉ với thư viện chuẩn.

## Phát triển

```bash
uv sync                  # tạo .venv
bin/tele status          # chạy thẳng từ source
bin/tele-local info
make lint
uv run ruff format       # format
```

Nhắc lại: dự án **không dùng test tự động** — không viết file test. Với thay đổi logic,
chạy thử bằng lệnh thật; muốn tránh đụng dữ liệu thật thì trỏ `TELE_ENV_FILE`, `TELE_DB`,
`TELE_MEDIA_DIR` sang file/thư mục tạm, hoặc chạy một PostgreSQL riêng bằng
`make up COMPOSE="docker compose -p tele-test"`.

## Chia sẻ binary độc lập

```bash
make build        # hoặc ./scripts/build-standalone
```

Chia sẻ `dist/tele`, `dist/tele-local` và `dist/SHA256SUMS`. Người nhận chạy:

```bash
sha256sum -c SHA256SUMS
chmod +x tele tele-local
./tele configure
./tele auth
```

Binary không chứa credentials/session và không cần cài Python, Telethon hay psycopg. Muốn
dùng database chung, đặt `TELE_DATABASE_URL` vào `~/.config/tele/.env`.

Xem thêm: `tele --help`, `tele-local --help` hoặc `<lệnh> <subcommand> --help`.
