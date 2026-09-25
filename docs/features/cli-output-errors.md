# 10. Khung CLI: parse, in JSON/text, mã lỗi

[← Bản đồ](../index.md)

Phần dùng chung của `tele` và `tele-local`: vòng chạy chính, kiểu tham số, định dạng đầu ra
và cách báo lỗi. Đầu ra mặc định là JSON để agent xử lý.

## Vòng chạy (`cli/runner.py:run`)

```text
umask 077 → load_env_files() → build_parser().parse_args()
  → dispatch(args) → payload dict
  → --format text ? render_text(args, payload) : dump_json(payload)       exit 0
  → TeleError      : dump_json(error, stderr)                             exit error.exit_code
  → Ctrl+C         : {"code": "interrupted"}                              exit 130
```

Mỗi CLI chỉ cần cung cấp `build_parser`, `dispatch`, `_render_text` rồi gọi `run(...)`.

## Flag viết tắt (`cli/args.py:add_short_flags`)

`build_parser()` của cả hai CLI kết thúc bằng `add_short_flags`: mỗi flag chỉ có dạng dài được
gán short là chữ cái đầu (`--timeout` → `-t`), trùng thì lấy 2 chữ cái đầu (`--format` → `-fo`
khi `-f` đã là `--from`), vẫn trùng thì giữ dạng dài (`tele get --max-age`). Short viết tay
(`-u`, `-l`, `-s`, `-d`, `-m`) và `-h` được ưu tiên, flag khai báo trước thắng khi tranh nhau,
mỗi subcommand tính riêng. Vì vậy cùng một flag có thể có short khác nhau giữa các lệnh; xem
`<lệnh> -h`.

## Mã lỗi (`TeleError.exit_code`)

| Exit | Mã lỗi tiêu biểu | Nơi raise |
|---|---|---|
| 1 | `connection_error`, `telegram_error` | `telegram/client.py` |
| 1 | `listener_unavailable`, `listener_socket_error`, `invalid_listener_request` | `telegram/listener.py` (IPC giữa `tele send` và `tele listen`) |
| 2 | `invalid_target`, `invalid_message`, `invalid_query`, `invalid_sql`, `invalid_command`, `ambiguous_account`, `message_file_error`, `chat_not_listened` | `values.py`, `cli/*.py`, `store/*` |
| 3 | `configuration_error`, `listener_whitelist_empty` | `cli/tele.py`, `store/database.py:open_database` |
| 4 | `not_authorized` | `telegram/client.py:authorized_client` |
| 5 | `peer_not_found`, `not_cached`, `no_account` | `client.py:resolve_chat`, `cli/local.py:_peer`, `repository.py:require_account` |
| 6 | `interactive_terminal_required` | `cli/tele.py` (`auth`) |
| 7 | `delivery_unknown` | `telegram/messaging.py:send_text_with_client`, `telegram/listener.py` |
| 8 | `media_directory_error` | `telegram/media.py:prepare_media_directory` |
| 9 | `store_error` | `store/database.py:store_error` |
| 10 | `listener_error` | `telegram/listener.py:_listener_error` |
| 11 | `listener_already_running`, `listener_lease_lost` | `store/repository.py` |
| 12 | `listener_not_running` (kèm `cursor`) | `cli/local.py:_wait` |
| 75 | `flood_wait` (kèm `retry_after_seconds`) | `telegram/client.py:_flood_wait_error` |
| 130 | `interrupted` | `cli/runner.py` |

Lỗi của argparse (sai option) do argparse tự in và thoát với mã 2.

## File liên quan

| File | Vai trò |
|---|---|
| `src/tele_cli/cli/runner.py` | Vòng chạy chung, map lỗi sang exit code |
| `src/tele_cli/cli/args.py` | Kiểu tham số: `bounded_int`, `positive_message_id`, `non_negative_megabytes`, `time_bound`; option chung `add_target` (`-u/--user/--chat`), `add_format` |
| `src/tele_cli/cli/tele.py` | Parser và `dispatch` của `tele` |
| `src/tele_cli/cli/local.py` | Parser và `dispatch` của `tele-local` |
| `src/tele_cli/cli/__init__.py` | Để trống có chủ đích (import `tele-local` không kéo Telethon) |
| `src/tele_cli/errors.py` | Dataclass `TeleError` (`code`, `message`, `exit_code`, `hint`, `details`) và `as_dict()` |
| `src/tele_cli/output.py` | `dump_json` (mỗi bản ghi trong list nằm trên một dòng), `render_messages`, `render_table` (tab-separated, ô rỗng là `-`) |
| `src/tele_cli/store/payloads.py` | `compact()` bỏ trường rỗng; hình dạng JSON của peer/message/media |
| `src/tele_cli/values.py` | `parse_target` (`@user` → `user`, số → int, `me`), `utc_now`, `to_iso`, `from_iso`, `parse_time_bound` |
| `src/tele_cli/__init__.py` | `__version__` cho `--version` |

## Lưu ý khi sửa

- Thêm subcommand: khai báo trong `build_parser()`, xử lý trong `dispatch()`, và nếu có
  `--format text` thì thêm nhánh trong `_render_text()`.
- Mọi lỗi người dùng nhìn thấy phải là `TeleError` có `hint` hướng dẫn cách xử lý; không để
  exception thô lọt ra.
- Nâng phiên bản: sửa `__version__` trong `src/tele_cli/__init__.py` và `version` trong `pyproject.toml`.
