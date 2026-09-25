# 3. Tải media không trùng lặp

[← Bản đồ](../index.md)

Tải ảnh/tài liệu đính kèm của các tin trả về bởi `tele get`, không bao giờ tải lại file đã có
trên máy, và ghi lại **máy nào** (hostname + user OS) giữ bản sao ở đâu.

## Lệnh

`tele get -u <target> --download-media [-d DIR] [--max-media-mb N]` (`-d` tự bật
`--download-media`; `--max-media-mb 0` = không giới hạn).

Tra cứu media đã lưu: `tele-local media` (xem [local-query.md](local-query.md)).

## Luồng xử lý (`telegram/media.py:MediaDownloader`)

1. `prepare_media_directory()`: thư mục là `-d` hoặc `<TELE_MEDIA_DIR>/<target>`, tạo với
   quyền 700.
2. `resolve_locally(ids)`: với mỗi media, `_resolve()` thử lần lượt:
   1. bản sao đã ghi cho tin này hoặc cùng `file_key` trên **máy này** (`store.node_copies`)
      và còn nguyên kích thước → `existing`;
   2. file cùng tên đích đã có trên đĩa → `existing`;
   3. vượt giới hạn dung lượng → `skipped_too_large`;
   4. loại không có file (`FILELESS_MEDIA_TYPES`) hoặc đã biết `not_downloadable`.
3. Phần còn lại nằm trong `_pending`. `download()` lấy các tin chưa có trong lần fetch này
   bằng `client.get_messages(ids=...)`, thử `_resolve()` lại (album/forward có thể vừa tải
   xong), rồi `_download_one()` với `progress_callback` cắt ngang khi vượt giới hạn.
4. `_finish()` ghi kết quả: có file → `store.record_file()` (bảng `media_files`); không có
   file → `store.set_media_status()` (cột `media.download_status`).
5. Với `-d`, bản sao cũ ở thư mục khác được hard link vào `-d` (khác filesystem thì symlink),
   không bao giờ copy thành file thứ hai (`_place`).

## File liên quan

| File | Vai trò |
|---|---|
| `src/tele_cli/telegram/media.py` | `prepare_media_directory`, `_destination` (tên `<message_id>_<tên file>`, tránh trùng tên), `_is_complete`, class `MediaDownloader` (`resolve_locally`, `download`, `_download_one`, `summary`), `FILELESS_MEDIA_TYPES` |
| `src/tele_cli/telegram/history.py` | Tạo `MediaDownloader` (`downloader_for`), quyết định có phải kết nối Telegram chỉ để tải media không, gắn `download_status` của lần chạy vào JSON (`_report_download`; bản rút gọn chỉ khi thiếu file) |
| `src/tele_cli/telegram/records.py` | `media_record()`: loại media, `file_key` (`photo:<id>`/`document:<id>`), tên, đuôi, mime, kích thước |
| `src/tele_cli/store/repository.py` | `media_rows`, `node_copies`, `record_file`, `set_media_status`, `files_for`, `media_list`; trong `save_messages` xoá bản sao cũ khi `file_key` đổi |
| `src/tele_cli/store/migrations.py` | Bảng `media`, `media_files`, index `media_file_key` |
| `src/tele_cli/store/payloads.py` | `media_payload`: `local_path` (bản trên máy này nếu còn), `files` (mọi máy), `file_missing` |
| `src/tele_cli/config.py` | `default_media_root()` (`TELE_MEDIA_DIR` → `media_dir` trong config.json → `~/telegram-files`), `Node`, `current_node` |
| `src/tele_cli/cli/args.py` | `non_negative_megabytes` parse `--max-media-mb` |

## Giá trị `download_status`

`downloaded`, `existing`, `skipped_too_large`, `not_downloadable`, `error`, và
`file_missing` (chỉ tính khi in ra: máy này đã ghi nhận file nhưng file đã bị xoá khỏi đĩa).

## Lưu ý khi sửa

- Chỉ bản sao của **máy hiện tại** mới dùng được; máy khác không đọc được đĩa của nhau.
- File tải về được đặt quyền 600. Lỗi giữa chừng phải xoá file dở (`destination.unlink`).
- `FloodWaitError` được ném lại để dừng cả lệnh, không nuốt thành `error`.
