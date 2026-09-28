"""命令列介面：qrdrop send / receive / qr。"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import threading
import time
from typing import List, Optional

from . import __version__, render
from .qrcode import DataTooLong, encode
from .server import DEFAULT_MAX_SIZE, Session, collect_files, make_server
from .util import human_duration, human_size, lan_ip, parse_duration, parse_size

DEFAULT_TIMEOUT = "30m"


def _pin(text: str) -> str:
    if not re.fullmatch(r"\d{4}", text):
        raise argparse.ArgumentTypeError("PIN 必須是 4 位數字，例如 --pin 4821")
    return text


def _size(text: str) -> int:
    try:
        return parse_size(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _duration(text: str) -> Optional[float]:
    try:
        return parse_duration(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _port(text: str) -> int:
    if not text.isdigit() or not 0 <= int(text) <= 65535:
        raise argparse.ArgumentTypeError("埠號必須在 0–65535")
    return int(text)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qrdrop", description="掃 QR Code 就能在電腦和手機之間傳檔（同一個 Wi-Fi）。")
    p.add_argument("--version", action="version", version=f"qrdrop {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="{send,receive,qr}")

    def net_opts(sp):
        sp.add_argument("--port", type=_port, default=0, help="埠號（預設隨機）")
        sp.add_argument("--bind", metavar="ADDR", help="綁定的位址（預設只綁區網 IP；要綁 0.0.0.0 請明確指定）")
        sp.add_argument("--once", action="store_true", help="完成一次傳輸後自動關閉")
        sp.add_argument("--timeout", type=_duration, default=parse_duration(DEFAULT_TIMEOUT), metavar="時間",
                        help=f"逾時自動關閉，例如 90s、10m、1h；0 表示不逾時（預設 {DEFAULT_TIMEOUT}）")
        sp.add_argument("--pin", type=_pin, metavar="4位數", help="要求輸入 4 位數 PIN")
        sp.add_argument("--plain", action="store_true", help="終端機 QR 不使用 ANSI 顏色（預設在終端機內會用黑字白底）")
        sp.add_argument("--invert", action="store_true", help="搭配 --plain：給淺色背景的終端機用")

    s = sub.add_parser("send", help="分享檔案或資料夾給手機下載")
    s.add_argument("paths", nargs="+", metavar="檔案或資料夾")
    net_opts(s)

    r = sub.add_parser("receive", help="讓手機把檔案上傳到電腦")
    r.add_argument("--dir", default=".", metavar="目錄", help="存放位置（預設目前資料夾）")
    r.add_argument("--max-size", type=_size, default=DEFAULT_MAX_SIZE, metavar="大小", help="單一檔案上限，例如 500M、2G（預設 2G）")
    net_opts(r)

    q = sub.add_parser("qr", help="把任意文字編成 QR Code")
    q.add_argument("text", help='要編碼的文字；用 "-" 從標準輸入讀')
    q.add_argument("--ecc", choices=list("LMQH"), default="M", help="錯誤修正等級（預設 M）")
    q.add_argument("--min-version", type=int, default=1, metavar="N", help="最小版本 1–40")
    q.add_argument("--max-version", type=int, default=40, metavar="N", help="最大版本 1–40")
    q.add_argument("--mask", type=int, choices=range(8), metavar="0-7", help="指定遮罩（預設自動挑分數最低的）")
    q.add_argument("--png", metavar="檔案", help="輸出 PNG")
    q.add_argument("--svg", metavar="檔案", help="輸出 SVG")
    q.add_argument("--scale", type=int, default=8, help="PNG 每個模組的像素數（預設 8）")
    q.add_argument("--border", type=int, default=4, help="quiet zone 寬度（模組數，預設 4）")
    q.add_argument("--show", action="store_true", help="同時在終端機顯示（有輸出檔時預設不顯示）")
    q.add_argument("--info", action="store_true", help="印出版本、等級、遮罩等資訊")
    q.add_argument("--plain", action="store_true", help="不使用 ANSI 顏色")
    q.add_argument("--invert", action="store_true", help="搭配 --plain：給淺色背景的終端機用")
    return p


def _use_color(args) -> bool:
    return (not args.plain) and sys.stdout.isatty()


def cmd_qr(args, out=None) -> int:
    out = out or sys.stdout
    text = sys.stdin.read().rstrip("\n") if args.text == "-" else args.text
    try:
        qr = encode(text, args.ecc, args.min_version, args.max_version, args.mask)
    except (DataTooLong, ValueError) as e:
        print(f"qrdrop: {e}", file=sys.stderr)
        return 2
    wrote = False
    try:
        if args.png:
            with open(args.png, "wb") as fh:
                fh.write(render.to_png(qr, args.scale, args.border))
            print(f"已寫入 {args.png}", file=sys.stderr)
            wrote = True
        if args.svg:
            with open(args.svg, "w", encoding="utf-8") as fh:
                fh.write(render.to_svg(qr, args.border))
            print(f"已寫入 {args.svg}", file=sys.stderr)
            wrote = True
    except (OSError, ValueError) as e:
        print(f"qrdrop: {e}", file=sys.stderr)
        return 1
    if args.info:
        print(f"版本 {qr.version}（{qr.size}x{qr.size}）  等級 {qr.ecc}  模式 {qr.mode}  遮罩 {qr.mask}", file=sys.stderr)
    if not wrote or args.show:
        out.write(render.to_terminal(qr, args.border, color=_use_color(args), invert=args.invert))
    return 0


def _make_logger():
    lock = threading.Lock()

    def log(ip: str, action: str, detail: str = "") -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {ip:<15} {action}" + (f"  {detail}" if detail else "")
        with lock:
            print(line, flush=True)

    return log


def _serve(args, session: Session) -> int:
    bind = args.bind
    ip = lan_ip()
    if bind is None:
        if ip is None:
            print("qrdrop: 找不到區網 IP，請確認已連上 Wi-Fi／網路（或用 --bind 指定位址）。", file=sys.stderr)
            return 1
        bind = ip
    show_ip = ip if bind in ("0.0.0.0", "") and ip else bind
    if bind == "0.0.0.0":
        print("注意：已綁定 0.0.0.0，這台電腦的所有網卡都能連進來。", file=sys.stderr)
    try:
        server = make_server(session, bind, args.port)
    except OSError as e:
        print(f"qrdrop: 無法在 {bind}:{args.port} 開伺服器：{e.strerror or e}", file=sys.stderr)
        return 1
    port = server.server_address[1]
    url = f"http://{show_ip}:{port}/{session.token}/"
    qr = encode(url, "M")
    print()
    print(render.to_terminal(qr, 4, color=_use_color(args), invert=args.invert), end="")
    print()
    if session.mode == "send":
        total = sum(f.size for f in session.files)
        print(f"分享 {len(session.files)} 個檔案（{human_size(total)}）")
    else:
        print(f"存到：{os.path.abspath(session.dest)}（單檔上限 {human_size(session.max_size)}）")
    print(f"網址：{url}")
    if session.pin:
        print(f"PIN：{session.pin}（手機打開網址後要輸入）")
    extra = []
    if session.once:
        extra.append("傳完一次就自動關閉")
    if args.timeout:
        extra.append(f"{human_duration(args.timeout)}後自動關閉")
    print("用手機掃描上面的 QR Code（要在同一個 Wi-Fi）。" + ("（" + "、".join(extra) + "）" if extra else ""))
    print("提醒：這是 HTTP，沒有加密，只適合信任的區網。按 Ctrl+C 結束。\n", flush=True)
    timer = None
    if args.timeout:
        timer = threading.Timer(args.timeout, server.request_stop, args=("timeout",))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        server.block_on_close = False
        print("\n已中止。")
    finally:
        if timer:
            timer.cancel()
        server.server_close()
    reason = {"once": "已完成一次傳輸，伺服器關閉。", "timeout": "逾時，伺服器已關閉。"}.get(server.stop_reason or "")
    if reason:
        print(reason)
    if session.mode == "send":
        print(f"共完成 {session.downloads} 次下載。")
    else:
        print(f"共收到 {len(session.uploads)} 個檔案。")
    return 0


def cmd_send(args) -> int:
    try:
        files = collect_files(args.paths)
    except (OSError, ValueError) as e:
        print(f"qrdrop: {e}", file=sys.stderr)
        return 2
    tops = {f.arcname.split("/")[0] for f in files if "/" in f.arcname}
    zip_name = (next(iter(tops)) if len(args.paths) == 1 and len(tops) == 1 else "qrdrop") + ".zip"
    session = Session("send", files=files, pin=args.pin, once=args.once, log=_make_logger(), zip_name=zip_name)
    return _serve(args, session)


def cmd_receive(args) -> int:
    dest = os.path.abspath(args.dir)
    try:
        os.makedirs(dest, exist_ok=True)
        if not os.access(dest, os.W_OK):
            raise PermissionError(f"沒有寫入權限：{dest}")
        free = shutil.disk_usage(dest).free
    except OSError as e:
        print(f"qrdrop: {e}", file=sys.stderr)
        return 2
    print(f"目前剩餘空間：{human_size(free)}")
    session = Session("receive", dest=dest, max_size=args.max_size, pin=args.pin, once=args.once, log=_make_logger())
    return _serve(args, session)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "qr":
        return cmd_qr(args)
    if args.cmd == "send":
        return cmd_send(args)
    if args.cmd == "receive":
        return cmd_receive(args)
    parser.print_help()
    return 0
