#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""钉钉「群签到」自动化 —— 手机 USB 常连电脑，电脑侧定时触发。

实际流程（按你的要求）：
    消息列表 → 建筑学院群 → 点「群签到」→ 点黄色大按钮「签到」

- 位置用的是手机自己的真实 GPS，脚本不伪造定位。
- 群签到页是 H5 轻应用，界面读不到文字，所以按钮用屏幕比例坐标点击。
- 打卡成功后会回到群聊并出现一张签到卡片；脚本用「卡片日期 + 本地日期记录」
  双重判断今天是否已经签过，避免重复发卡片。

用法：
    python dingtalk_checkin.py --dry-run                 # 只走到签到页，不点按钮
    python dingtalk_checkin.py --window 06:30-21:30      # 定时窗口内执行
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ADB_CANDIDATES = (
    r"D:\Program Files\Essentials\NetEase\MuMuPlayer-12.0\shell\adb.exe",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "Android", "Sdk",
                 "platform-tools", "adb.exe"),
)

CHAT_NAME = "建筑学院"          # 要进入的群名, 可以修改为自己学院对应的群聊
CHECKIN_ENTRY = "群签到"        # 群工具栏里的入口
SIGN_BTN_FRACTION = (0.5, 0.781)  # 黄色签到按钮在屏幕上的相对位置

def resolve_adb() -> str:
    env = os.environ.get("ADB", "").strip()
    if env:
        return env
    which = shutil.which("adb")
    if which:
        return which
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "tools", "platform-tools", "adb.exe")
    for cand in (local, *ADB_CANDIDATES):
        if os.path.isfile(cand):
            return cand
    return "adb"


ADB = resolve_adb()
PKG = "com.alibaba.android.rimet"

SERIAL: str | None = None

CLOSE_WORDS = ("暂不更新", "以后再说", "稍后再说", "下次再说", "跳过",
               "我知道了", "知道了", "取消", "关闭", "忽略")


class Log:
    def __init__(self, path: str | None = None) -> None:
        self.path = path
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def __call__(self, msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        print(line, flush=True)
        if self.path:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# adb
# --------------------------------------------------------------------------- #
def adb(*args, timeout: int = 60):
    cmd = [ADB] + (["-s", SERIAL] if SERIAL else []) + [str(a) for a in args]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "", f"找不到 adb：{ADB}"
    except subprocess.TimeoutExpired:
        return 124, "", "adb 命令超时"
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def shell(*args, timeout: int = 60):
    return adb("shell", *args, timeout=timeout)


def pick_device(log: Log) -> str | None:
    rc, out, err = adb("devices")
    if rc != 0:
        log(f"adb 不可用：{err.strip()[:200]}")
        return None
    ready, other = [], []
    for line in out.splitlines()[1:]:
        if "\t" not in line:
            continue
        dev, state = line.split("\t", 1)
        (ready if state.strip() == "device" else other).append(f"{dev}({state.strip()})")
    usb = [d.split("(")[0] for d in ready
           if ":" not in d.split("(")[0] and not d.split("(")[0].startswith("emulator-")]
    if usb:
        if len(usb) > 1:
            log(f"检测到多台手机，使用第一台：{usb[0]}（可用 --serial 指定）")
        return usb[0]
    if ready:
        log("adb 只看到通过 TCP 连接的设备（一般是模拟器），没有 USB 手机：" + ", ".join(ready))
        log("插上手机并开启 USB 调试；若确实用无线调试，加 --serial <序列号>。")
        return None
    log("没有可用的手机连接。" + (f"当前状态：{', '.join(other)}" if other else "请检查 USB 与 USB 调试授权。"))
    return None


# --------------------------------------------------------------------------- #
# 屏幕状态
# --------------------------------------------------------------------------- #
_SIZE: tuple[int, int] | None = None


def screen_size() -> tuple[int, int]:
    global _SIZE
    if _SIZE is None:
        rc, out, _ = shell("wm", "size")
        m = re.search(r"(\d+)x(\d+)", out)
        _SIZE = (int(m.group(1)), int(m.group(2))) if m else (1080, 2400)
    return _SIZE


def keyguard_showing() -> bool:
    rc, out, _ = shell("dumpsys", "window")
    pats = (r"mDreamingLockscreen=true", r"mShowingLockscreen=true",
            r"isKeyguardShowing=true", r"mKeyguardShowing=true")
    return any(re.search(p, out) for p in pats)


def current_focus() -> str:
    rc, out, _ = shell("dumpsys", "window")
    for line in out.splitlines():
        if "mCurrentFocus" in line:
            return line.strip()
    return ""


def ensure_ready(log: Log, quiet: bool = True) -> bool:
    """唤醒 + 上划解锁。返回最终是否已解锁（无锁屏）。"""
    for attempt in (1, 2):
        rc, out, _ = shell("dumpsys", "power")
        if "mWakefulness=Awake" not in out:
            shell("input", "keyevent", "224")          # KEYCODE_WAKEUP
            time.sleep(0.8)
        if not keyguard_showing():
            shell("svc", "power", "stayon", "true")    # 插电时保持常亮
            return True
        w, h = screen_size()
        shell("input", "swipe", str(w // 2), str(int(h * 0.82)),
              str(w // 2), str(int(h * 0.25)), "200")
        time.sleep(1.2)
        if not keyguard_showing():
            shell("svc", "power", "stayon", "true")
            return True
        # 某些机型上滑后仍停在锁屏，按 MENU 兜一下
        shell("input", "keyevent", "82")
        time.sleep(0.8)
        if not keyguard_showing():
            shell("svc", "power", "stayon", "true")
            return True
        if not quiet:
            log(f"第 {attempt} 次解锁没成功")
    shell("svc", "power", "stayon", "true")
    return not keyguard_showing()


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
class Node:
    __slots__ = ("text", "desc", "bounds", "clickable", "cls")

    def __init__(self, a: dict) -> None:
        self.text = a.get("text", "")
        self.desc = a.get("content-desc", "")
        self.bounds = a.get("bounds", "")
        self.clickable = a.get("clickable") == "true"
        self.cls = a.get("class", "")

    @property
    def label(self) -> str:
        return (self.text + " " + self.desc).strip()

    def center(self) -> tuple[int, int] | None:
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", self.bounds)
        if not m:
            return None
        x1, y1, x2, y2 = (int(v) for v in m.groups())
        return (x1 + x2) // 2, (y1 + y2) // 2

    def area(self) -> int:
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", self.bounds)
        if not m:
            return 0
        x1, y1, x2, y2 = (int(v) for v in m.groups())
        return max(0, x2 - x1) * max(0, y2 - y1)


def dump_xml(log: Log, retries: int = 3) -> str:
    for _ in range(retries):
        rc, out, err = shell("uiautomator", "dump", "/sdcard/dsh_ui.xml")
        if rc == 0 and "dumped to" in (out + err):
            rc2, data, _ = adb("exec-out", "cat", "/sdcard/dsh_ui.xml")
            if rc2 == 0 and data.lstrip().startswith("<"):
                return data
        time.sleep(1.2)
    log("UI 抓取失败")
    return ""


def parse_nodes(xml: str) -> list[Node]:
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    return [Node(el.attrib) for el in root.iter("node")]


def pick_best(nodes: list[Node], words: tuple[str, ...],
              exclude: tuple[str, ...] = (), min_y: int = -1) -> Node | None:
    for w in words:
        cands = [n for n in nodes
                 if w in n.label and n.area() > 0
                 and not any(x in n.label for x in exclude)]
        if min_y >= 0:
            cands = [n for n in cands if (n.center() or (0, 0))[1] >= min_y]
        if cands:
            return max(cands, key=lambda n: (n.clickable, n.area()))
    return None


def tap(node: Node, log: Log) -> bool:
    c = node.center()
    if not c:
        return False
    shell("input", "tap", str(c[0]), str(c[1]))
    return True


def dismiss_popups(log: Log) -> bool:
    nodes = parse_nodes(dump_xml(log))
    for n in nodes:
        if n.text in CLOSE_WORDS and n.area() > 0:
            log(f"关闭弹窗：{n.text}")
            tap(n, log)
            time.sleep(1.5)
            return True
    return False


# --------------------------------------------------------------------------- #
# 业务步骤
# --------------------------------------------------------------------------- #
def open_dingtalk(log: Log, wait: int = 30) -> bool:
    # 干净启动：避免停在上次的群签到页/别的群聊里，起始页面不可控
    shell("am", "force-stop", PKG)
    time.sleep(1.0)
    rc, out, err = shell("monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")
    if "No activities found" in (out + err):
        log("找不到钉钉，请确认已安装")
        return False
    end = time.time() + wait
    while time.time() < end:
        if PKG in current_focus():
            time.sleep(2.5)          # 等首页渲染
            return True
        time.sleep(1)
    log("等待钉钉到前台超时")
    return False


def goto_message_list(log: Log) -> bool:
    """回到「消息」标签页。"""
    w, h = screen_size()
    for _ in range(4):
        nodes = parse_nodes(dump_xml(log))
        tab = pick_best(nodes, ("消息",), exclude=("未读",), min_y=int(h * 0.78))
        if tab:
            tap(tab, log)
            time.sleep(2)
            return True
        if dismiss_popups(log):
            continue
        shell("input", "keyevent", "4")      # 返回，从群聊/子页面退出
        time.sleep(1.5)
    log("没能回到消息列表")
    return False


def open_chat(log: Log) -> bool:
    """在消息列表里点开目标群，并确认真的进了群聊。"""
    w, h = screen_size()
    for attempt in range(3):
        nodes = parse_nodes(dump_xml(log))
        chat = pick_best(nodes, (CHAT_NAME,), min_y=int(h * 0.18))
        if not chat:
            log(f"消息列表里没看到「{CHAT_NAME}」，翻一屏再找")
            shell("input", "swipe", str(w // 2), str(int(h * 0.72)),
                  str(w // 2), str(int(h * 0.32)), "300")
            time.sleep(1.5)
            continue
        log(f"打开群聊：{chat.text} @ {chat.bounds}")
        tap(chat, log)
        time.sleep(3)
        if "ChatMsgActivity" in current_focus():
            return True
        log("没进到群聊，返回重试")
        shell("input", "keyevent", "4")
        time.sleep(1.5)
    log(f"进不去「{CHAT_NAME}」群聊")
    return False


def sign_card_today(nodes: list[Node]) -> tuple[bool, str]:
    """群里是否已有今天的签到卡片，返回 (是否已签, 地址)。"""
    today = datetime.now()
    for n in nodes:
        m = re.search(r"签到时间：\s*(\d{1,2})月(\d{1,2})日", n.text)
        if m and int(m.group(1)) == today.month and int(m.group(2)) == today.day:
            addr = ""
            for other in nodes:
                if "签到" in other.text and "在" in other.text and "签到时间" not in other.text:
                    addr = other.text
                    break
            return True, addr
    return False, ""


def open_checkin_page(log: Log) -> bool:
    nodes = parse_nodes(dump_xml(log))
    entry = pick_best(nodes, (CHECKIN_ENTRY,))
    if not entry:
        log(f"群聊里没找到「{CHECKIN_ENTRY}」入口")
        return False
    log(f"点击 {CHECKIN_ENTRY} @ {entry.bounds}")
    tap(entry, log)
    end = time.time() + 15
    while time.time() < end:
        if "lightapp" in current_focus() or "DragDrop" in current_focus():
            time.sleep(2.0)          # 等页面渲染
            return True
        time.sleep(1)
    log("群签到页没打开（可能还在加载）")
    return False


def press_sign_button(log: Log) -> None:
    w, h = screen_size()
    x, y = int(w * SIGN_BTN_FRACTION[0]), int(h * SIGN_BTN_FRACTION[1])
    log(f"点击签到按钮 @ ({x},{y})")
    shell("input", "tap", str(x), str(y))


def back_to_chat(log: Log) -> bool:
    for _ in range(3):
        if "ChatMsgActivity" in current_focus():
            return True
        shell("input", "keyevent", "4")
        time.sleep(1.5)
    return "ChatMsgActivity" in current_focus()


def wait_auto_close(log: Log, timeout: float = 10.0) -> bool:
    """签到成功后群签到页会自己关掉、回到群聊；没关上说明没签成。"""
    end = time.time() + timeout
    while time.time() < end:
        if "ChatMsgActivity" in current_focus():
            return True
        time.sleep(0.8)
    return False


# --------------------------------------------------------------------------- #
# 单次流程
# --------------------------------------------------------------------------- #
def run_once(log: Log, args) -> str:
    """返回 'done'（今天已签）、'ok'（本次签到成功）或 'fail'。"""
    if not (open_dingtalk(log) and goto_message_list(log)):
        return "fail"
    if not open_chat(log):
        return "fail"

    # 群里是否已经有今天的签到卡片（钉钉的消息列表时有时无，多抓两次）
    time.sleep(1.5)
    already, addr = False, ""
    for _ in range(2):
        nodes = parse_nodes(dump_xml(log))
        already, addr = sign_card_today(nodes)
        if already:
            break
        time.sleep(2)
    if already and not args.force:
        log(f"群里已有今天的签到卡片（{addr[:30]}），今天不再签")
        mark_state(args)
        return "done"

    if not open_checkin_page(log):
        return "fail"
    if args.dry_run:
        log("dry-run：已到签到页，不点按钮")
        back_to_chat(log)
        return "ok"

    press_sign_button(log)
    if wait_auto_close(log, 10):
        time.sleep(1.5)
        nodes = parse_nodes(dump_xml(log))
        ok, addr = sign_card_today(nodes)
        log(f"签到页已自动关闭，判定签到成功 ✅ {('地址：' + addr[:40]) if ok else ''}")
        mark_state(args)
        return "ok"
    log("签到页没自动关闭，可能没签上")
    back_to_chat(log)
    return "fail"


# --------------------------------------------------------------------------- #
# 本地日期记录（防止重复签到）
# --------------------------------------------------------------------------- #
def state_path(args) -> str:
    return args.state or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "state", "last_checkin.txt")


def done_today(args) -> bool:
    try:
        with open(state_path(args), encoding="utf-8-sig") as f:
            txt = f.read().strip()
    except OSError:
        return False
    return txt == datetime.now().strftime("%Y-%m-%d")


def mark_state(args) -> None:
    p = state_path(args)
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(datetime.now().strftime("%Y-%m-%d"))


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def today_at(hhmm: str) -> datetime:
    h, m = hhmm.strip().split(":")
    return datetime.now().replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def parse_args():
    ap = argparse.ArgumentParser(description="钉钉群签到自动化（adb）")
    ap.add_argument("--chat", default=CHAT_NAME, help=f"群名，默认 {CHAT_NAME}")
    ap.add_argument("--window", help="允许执行的时间窗，如 06:30-21:30")
    ap.add_argument("--interval", type=int, default=30, help="失败重试间隔秒")
    ap.add_argument("--max-minutes", type=int, default=15, help="单次运行最长重试分钟数")
    ap.add_argument("--serial", help="多台设备时指定序列号")
    ap.add_argument("--dry-run", action="store_true", help="走到签到页但不点按钮")
    ap.add_argument("--force", action="store_true", help="忽略本地记录，强制再签一次")
    ap.add_argument("--no-sleep", action="store_true", help="结束后不自动息屏")
    ap.add_argument("--state", help="本地日期记录文件路径")
    ap.add_argument("--log", help="日志文件路径")
    return ap.parse_args()


def run(args, log: Log) -> int:
    global CHAT_NAME, SERIAL
    CHAT_NAME = args.chat
    log(f"adb: {ADB}")

    SERIAL_ = args.serial or pick_device(log)
    if not SERIAL_:
        return 2
    SERIAL = SERIAL_
    log(f"===== 开始 设备={SERIAL} 群={CHAT_NAME} dry_run={args.dry_run} =====")

    if done_today(args) and not args.force:
        log("本地记录显示今天已签过，跳过（--force 可强制）")
        return 0

    deadline = datetime.now() + timedelta(minutes=args.max_minutes)
    if args.window:
        try:
            s, e = args.window.split("-")
            start, end = today_at(s), today_at(e)
        except ValueError:
            log("--window 格式应为 HH:MM-HH:MM")
            return 4
        if end <= start:
            end += timedelta(days=1)
        if datetime.now() > end:
            log(f"已过时间窗 {end:%H:%M}，今天不再处理")
            return 3
        while datetime.now() < start:
            log(f"未到时间窗（{start:%H:%M} 开始），等待中…")
            time.sleep(min(60, max(5, (start - datetime.now()).total_seconds())))
        deadline = min(end, datetime.now() + timedelta(minutes=args.max_minutes))

    if not ensure_ready(log, quiet=False):
        log("⚠ 唤醒/上划解锁失败：请确认锁屏是「上滑解锁」而不是密码/图案")
        return 2

    while datetime.now() < deadline:
        if not ensure_ready(log):
            log("本轮没解开锁屏，稍后重试")
        else:
            result = run_once(log, args)
            if result in ("ok", "done"):
                log("===== 结束：成功 =====")
                return 0
        log(f"{args.interval} 秒后重试")
        time.sleep(args.interval)

    log("===== 结束：超时未成功 =====")
    return 2


def main() -> int:
    args = parse_args()
    here = os.path.dirname(os.path.abspath(__file__))
    log = Log(args.log or os.path.join(here, "logs", f"checkin-{datetime.now():%Y%m%d}.log"))
    code = 2
    try:
        code = run(args, log)
    except KeyboardInterrupt:
        code = 130
    finally:
        # 用完熄屏：省电也更像正常使用，下次由 ensure_ready 唤醒+上划
        if SERIAL and not args.no_sleep:
            time.sleep(0.8)
            log("收尾：自动息屏")
            shell("input", "keyevent", "223")      # KEYCODE_SLEEP
    return code


if __name__ == "__main__":
    sys.exit(main())
