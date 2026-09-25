# tele

Hai CLI dùng tài khoản Telegram cá nhân, kết quả mặc định là JSON để agent dễ xử lý:

- **`tele`**: đọc tin, tải media, gửi tin, chờ phản hồi. Mọi thứ đã lấy về được lưu vào
  database, lần sau **đọc database trước** và chỉ gọi Telegram cho phần còn thiếu.
- **`tele-local`**: truy vấn dữ liệu đã lưu (lọc, tìm kiếm, SQL). **Không bao giờ gọi
  Telegram**, không cần Telethon.

Database là SQLite trên máy (mặc định) hoặc **PostgreSQL dùng chung** cho nhiều server
(khai báo `TELE_DATABASE_URL` trong `.env`, bật/tắt bằng `make up` / `make down`).

> **Dự án không có test tự động.** Không viết file test, không thêm pytest hay thư mục
> `tests/`. Kiểm tra thay đổi bằng cách chạy lệnh thật (`bin/tele ...`, `bin/tele-local ...`)
> và `make lint`.

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
| `make client-env` | In `TELE_DATABASE_URL` cho server khác |
| `make build`, `make install` | Build binary; build rồi cài vào `~/bin` |
| `make lint` | `ruff check` + kiểm tra format |

Bảo mật: PostgreSQL chỉ có mật khẩu, không mã hoá kết nối. Khi `TELE_DB_BIND=0.0.0.0`, chỉ mở
port trong mạng nội bộ/VPN (firewall), không để lộ ra internet. `.env` chứa mật khẩu và đã
được `.gitignore`.

Các server đăng nhập **tài khoản Telegram khác nhau** vẫn dùng chung được: dữ liệu được tách
theo tài khoản (ID tin nhắn chỉ duy nhất trong một tài khoản). Bảng `nodes` ghi mỗi máy
(hostname + user OS) đang dùng tài khoản nào; xem bằng `tele-local info`.

## `tele`

`-u` nhận username, `@username`, `me` hoặc ID user/group/channel như `-5539294370`.

| Việc cần làm | Lệnh |
|---|---|
| Lấy 10 tin gần nhất | `tele get -u amacvn -l 10` |
| Đọc group | `tele get -u -5539294370 -l 10` |
| Tải cả media | `tele get -u amacvn -l 10 --download-media` |
| Bắt buộc hỏi Telegram có tin mới | `tele get -u amacvn -l 10 --max-age 0` |
| Lấy lại cả cửa sổ (cập nhật tin sửa/xoá) | `tele get -u amacvn -l 10 --refresh` |
| Gửi tin nhắn | `tele send -u amacvn -m "Nội dung"` |
| Reply một tin | `tele send -u amacvn --reply-to 12345 -m "Nội dung"` |
| Chờ phản hồi | `tele wait -u amacvn --after 12345 --timeout 900` |
| Tìm ID hội thoại | `tele dialogs -l 50` |
| Kiểm tra session và database | `tele status` |

Tin mới nhất được trả về trước; `-l` nhận từ 1 đến 100. Dùng `--format=text` nếu muốn
đọc dạng chữ. JSON bỏ các trường rỗng và in mỗi tin trên một dòng cho gọn.

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

```json
"media": {"type": "Photo", "size": 24953, "download_status": "existing",
          "local_path": "/home/mihb/telegram-files/amacvn/196671_photo.jpg",
          "files": [{"host": "X1CB7", "user": "mihb", "path": "/home/mihb/telegram-files/amacvn/196671_photo.jpg"}]}
```

`files` liệt kê bản sao trên mọi máy; `local_path` chỉ có khi máy đang chạy lệnh giữ một
bản sao còn trên đĩa. Máy khác không đọc được đĩa của nhau, nên `--download-media` trên
máy B vẫn tải bản của B (một lần) và ghi thêm một dòng cho B.

Trước khi tải, `tele` tìm file sẵn có **trên máy hiện tại** theo thứ tự: file đã ghi nhận
cho tin đó → cùng file Telegram (ảnh/tài liệu được gửi lại, forward) đã tải ở tin khác →
file cùng tên từ lần chạy trước. Chỉ phần còn thiếu mới được tải. `download_status`:
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
| SQL chỉ-đọc tuỳ ý | `tele-local sql "SELECT sender_id, COUNT(*) FROM messages GROUP BY 1"` |
| Database, số bản ghi, các máy đang dùng | `tele-local info` |
| Đọc dữ liệu của tài khoản khác | `tele-local --account 5159078318 chats` |

`--since` / `--until` nhận `30m`, `2h`, `3d`, `1w`, `2026-09-25` hoặc ISO datetime (giờ địa
phương nếu không ghi múi giờ; `--until` với ngày trần là hết ngày đó). Trong SQL, hàm
`fold(text)` bỏ dấu và chữ hoa (có ở cả SQLite và PostgreSQL):
`WHERE fold(text) LIKE '%doi soat%'`. Mặc định `tele-local` đọc tài khoản đang đăng nhập
trên máy này; máy không có session thì dùng tài khoản duy nhất trong database hoặc
`--account`.

## Dữ liệu

| Dữ liệu | Vị trí mặc định | Biến môi trường |
|---|---|---|
| API credentials | `~/.config/tele/config.json` | `TELE_API_ID`, `TELE_API_HASH` |
| Session Telegram | `~/.local/share/tele/telegram.session` | `TELE_SESSION` |
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
| `messages` | nội dung tin, người gửi, reply, album (`grouped_id`), service action | `(account_id, chat_id, id)` |
| `media` | metadata file đính kèm, `file_key` để nhận ra cùng một file | `(account_id, chat_id, message_id)` |
| `media_files` | bản sao đã tải: máy nào (host, user) giữ, đường dẫn, kích thước | `(account_id, chat_id, message_id, host, os_user)` |
| `sync_state` | tin mới nhất của chat và thời điểm hỏi Telegram gần nhất | `(account_id, chat_id)` |
| `sync_ranges` | các khoảng ID tin đã lưu liên tục, không thiếu tin nào | `(account_id, chat_id, start_id)` |
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
