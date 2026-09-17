#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 FCE 词汇实战学习页导出成「适合打印的 PDF」。

设计要点
--------
本脚本**不做任何 HTML 改写**。适合打印的版式（浅色主题、隐藏右侧交互面板、
把 12 个词条全部铺开、用实线/虚线区分两类词）全部由页面自带的
`@page` / `@media print` / `#printAppendix` 在打印时自动生效。
脚本只负责「驱动一个 Chromium 内核的浏览器去打印」，因此：

  * 页面上直接按 Cmd/Ctrl + P 或点工具栏「打印 / 导出 PDF」按钮，效果与脚本一致；
  * 脚本的价值在于**批量**和**可重复**（以后新增页面一条命令重出全部 PDF）。

关键陷阱（踩过）
----------------
1. Chromium 在**已有实例运行**时，再加 `--headless --print-to-pdf` 会被转交给现有实例，
   结果什么也不生成、命令却退出 0。所以必须用 `--user-data-dir` 指定一个**独立的临时 profile**，
   让 headless 进程自成一个实例。同理要带上 `--no-first-run` 等开关，避免首次运行引导流程干扰。

2. 浏览器会往**沙箱外**写东西（Crashpad 崩溃转储、登录钥匙串），在受限沙箱里运行会被拦。
   本脚本已经带上 `--disable-breakpad` / `--crash-dumps-dir` / `--use-mock-keychain` 等一系列
   「自动化友好」开关把这些外部写入降到最低。**如果在沙箱环境下仍被拦，请用非沙箱方式运行本脚本**
   （或让 Agent 以提权方式执行）—— 这是浏览器本身的限制，不是脚本能绕过的。

用法
----
    python3 export_pdf.py page.html                 # 单文件 → 同目录同名 .pdf
    python3 export_pdf.py a.html b.html             # 多文件
    python3 export_pdf.py ./outputs                 # 目录（默认只取 *.html，不递归）
    python3 export_pdf.py ./outputs --recursive     # 目录 + 递归子目录
    python3 export_pdf.py page.html --out-dir /tmp  # 指定输出目录
    python3 export_pdf.py page.html --header-footer # 保留 Chrome 的页眉页脚（含页码）
    python3 export_pdf.py --check ./outputs         # 只体检：哪些页面缺打印支持
    python3 export_pdf.py page.html --open          # 导出后用系统默认程序打开

退出码
------
    0  全部成功
    1  有文件失败，或找不到可用的浏览器
    2  参数用法错误
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# --------------------------------------------------------------------------
# 浏览器探测
# --------------------------------------------------------------------------
#: 需要 Chromium 内核（`--print-to-pdf` 是 Chromium 私有开关，Firefox / Safari 不支持）。
#: 按顺序探测，第一个存在的就用它。
BROWSER_CANDIDATES: tuple[str, ...] = (
    # macOS
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Arc.app/Contents/MacOS/Arc",
    # Linux
    "/usr/bin/microsoft-edge",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
    # Windows
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)

#: 环境变量覆盖，优先级最高。想用非标准路径的浏览器时设它。
ENV_BROWSER_OVERRIDE = "CHROME_PATH"

#: 判定「页面已内建打印支持」的特征串。缺了它 PDF 会是深色主题、且只有 1 个词条详情。
PRINT_SUPPORT_MARKERS: tuple[str, ...] = (
    "@page{ size:A4",
    'id="printAppendix"',
    "buildPrintAppendix",
)


def find_browser(explicit: str | None = None) -> str | None:
    """定位一个可用的 Chromium 内核浏览器。

    参数:
        explicit: 用户通过 `--browser` 显式指定的可执行文件路径；给了就直接校验它。

    返回:
        可执行文件的绝对路径；找不到返回 None。
    """
    if explicit:
        return explicit if Path(explicit).is_file() else None
    env_path = os.environ.get(ENV_BROWSER_OVERRIDE)
    if env_path and Path(env_path).is_file():
        return env_path
    for candidate in BROWSER_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    # 最后再试一次 PATH
    for name in ("microsoft-edge", "google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


# --------------------------------------------------------------------------
# 页面体检
# --------------------------------------------------------------------------
def inspect_html(html_path: Path) -> dict:
    """检查一个 HTML 是否具备打印支持。

    参数:
        html_path: 待检查的 HTML 文件路径。

    返回:
        字典，字段：
            missing (list[str]): 缺失的打印支持特征；空列表表示完备。
            title   (str)      : 从 <title> 取到的页面标题，取不到则为文件名。
            words   (int|None) : 正文词数（尽力而为，取 id="en" 里的英文 token 数）。
    """
    text = html_path.read_text(encoding="utf-8", errors="replace")
    missing = [marker for marker in PRINT_SUPPORT_MARKERS if marker not in text]

    title_match = re.search(r"<title>(.*?)</title>", text, re.S)
    title = title_match.group(1).strip() if title_match else html_path.stem

    words: int | None = None
    en_match = re.search(r'<p class="passage" id="en">(.*?)</p>', text, re.S)
    if en_match:
        plain = re.sub(r"<[^>]+>", "", en_match.group(1))
        plain = plain.replace("&mdash;", " ").replace("&nbsp;", " ")
        words = len([t for t in plain.split() if re.search(r"[A-Za-z0-9]", t)])

    return {"missing": missing, "title": title, "words": words}


# --------------------------------------------------------------------------
# PDF 校验
# --------------------------------------------------------------------------
def count_pdf_pages(pdf_path: Path) -> int | None:
    """数 PDF 的页数。

    参数:
        pdf_path: PDF 文件路径。

    返回:
        页数；数不出来返回 None。

    说明:
        macOS 上优先用 Spotlight 的元数据（最准）；失败则退回统计未压缩的
        `/Type /Page` 计数（对线性化/压缩对象流可能少算，仅作兜底）。
    """
    if sys.platform == "darwin" and shutil.which("mdls"):
        try:
            result = subprocess.run(
                ["mdls", "-name", "kMDItemNumberOfPages", "-raw", str(pdf_path)],
                capture_output=True, text=True, timeout=15,
            )
            raw = result.stdout.strip()
            if raw.isdigit():
                return int(raw)
        except (subprocess.SubprocessError, OSError):
            pass
    try:
        data = pdf_path.read_bytes()
    except OSError:
        return None
    # /Type /Page 后面不跟 s 的，才是页对象（/Pages 是页树节点）
    return len(re.findall(rb"/Type\s*/Page(?![s])", data)) or None


def validate_pdf(pdf_path: Path) -> tuple[bool, str]:
    """校验生成的 PDF 是否真的有效。

    参数:
        pdf_path: 待校验的 PDF 路径。

    返回:
        (是否有效, 人类可读的说明) —— 说明里包含字节数与页数。
    """
    if not pdf_path.exists():
        return False, "文件未生成"
    size = pdf_path.stat().st_size
    if size == 0:
        return False, "文件为空"
    with pdf_path.open("rb") as fh:
        if fh.read(5) != b"%PDF-":
            return False, "文件头不是 %PDF-（可能是错误页而非 PDF）"
    pages = count_pdf_pages(pdf_path)
    human = f"{size / 1024:.0f} KB" + (f" · {pages} 页" if pages else "")
    return True, human


# --------------------------------------------------------------------------
# 核心：打印一个页面
# --------------------------------------------------------------------------
def print_to_pdf(
    browser: str,
    html_path: Path,
    pdf_path: Path,
    *,
    header_footer: bool = False,
    virtual_time_budget_ms: int = 8000,
    timeout_s: int = 120,
) -> tuple[bool, str]:
    """用一个临时的 headless 浏览器实例把 HTML 打成 PDF。

    参数:
        browser:               浏览器可执行文件绝对路径。
        html_path:             源 HTML 文件路径。
        pdf_path:              目标 PDF 文件路径（父目录会自动创建）。
        header_footer:         True 则保留 Chrome 默认页眉页脚（含日期/标题/页码）。
        virtual_time_budget_ms: 允许页面脚本运行多久后再出 PDF（毫秒）。附录由 JS 在
                               加载时同步生成，取值范围宽松些更保险。
        timeout_s:             子进程硬超时（秒），防止浏览器卡死。

    返回:
        (是否成功, 说明文本)。
    """
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        pdf_path.unlink()

    # 关键：独立临时 profile，避免被已在运行的浏览器实例接管而静默不产出
    with tempfile.TemporaryDirectory(prefix="fce-pdf-profile-") as profile_dir:
        crash_dir = Path(profile_dir) / "crashdumps"
        crash_dir.mkdir(exist_ok=True)
        cmd: list[str] = [
            browser,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            # --- 自动化友好：尽量不往 profile 之外写任何东西 ---
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-sync",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-client-side-phishing-detection",
            "--metrics-recording-only",
            "--disable-breakpad",                 # 不生成崩溃转储
            "--disable-crash-reporter",
            f"--crash-dumps-dir={crash_dir}",     # 万一要写，也写进临时目录
            "--use-mock-keychain",                # macOS：不碰真实登录钥匙串
            "--disable-features=Translate,MediaRouter,OptimizationHints,InterestFeedContentSuggestions",
            "--password-store=basic",
            f"--user-data-dir={profile_dir}",
            f"--virtual-time-budget={virtual_time_budget_ms}",
            f"--print-to-pdf={pdf_path}",
        ]
        if not header_footer:
            # 去掉 Chrome 自带的页眉页脚（日期 + 文件路径 + 页码那一行）
            cmd.append("--no-pdf-header-footer")
        cmd.append(html_path.resolve().as_uri())

        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return False, f"浏览器超时（>{timeout_s}s）"
        except OSError as exc:
            return False, f"无法启动浏览器：{exc}"

    ok, detail = validate_pdf(pdf_path)
    return ok, detail


# --------------------------------------------------------------------------
# 输入解析
# --------------------------------------------------------------------------
def collect_html(inputs: list[str], recursive: bool) -> list[Path]:
    """把命令行参数展开成一份去重后的 HTML 文件清单。

    参数:
        inputs:    原始参数列表，元素可以是文件路径或目录路径。
        recursive: 遇到目录时是否递归子目录。

    返回:
        排好序的绝对路径列表。
    """
    found: list[Path] = []
    for raw in inputs:
        path = Path(raw).expanduser()
        if path.is_dir():
            pattern = "**/*.html" if recursive else "*.html"
            found.extend(sorted(path.glob(pattern)))
        elif path.is_file():
            found.append(path)
        else:
            print(f"  ! 跳过不存在的路径：{raw}", file=sys.stderr)
    # 去重（同一文件被两种方式指到）并保持稳定顺序
    deduped: dict[Path, None] = {}
    for item in found:
        deduped[item.resolve()] = None
    return sorted(deduped)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    参数:
        argv: 参数列表；None 表示取 sys.argv[1:]。

    返回:
        进程退出码（0 全部成功 / 1 有失败 / 2 用法错误）。
    """
    parser = argparse.ArgumentParser(
        prog="export_pdf.py",
        description="把 FCE 词汇实战学习页导出成适合打印的 PDF（A4 / 浅色 / 含全部词条详解）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("inputs", nargs="*", help="HTML 文件或目录；给目录则批量处理其中的 *.html")
    parser.add_argument("--out-dir", metavar="DIR", help="输出目录（默认与源文件同目录）")
    parser.add_argument("--out", metavar="FILE", help="单文件模式下的输出路径")
    parser.add_argument("--browser", metavar="PATH", help=f"指定浏览器可执行文件（也可用环境变量 {ENV_BROWSER_OVERRIDE}）")
    parser.add_argument("--recursive", action="store_true", help="目录输入时递归子目录")
    parser.add_argument("--header-footer", action="store_true", help="保留浏览器默认页眉页脚（含页码，但也会带日期和文件路径）")
    parser.add_argument("--virtual-time-budget", type=int, default=8000, metavar="MS", help="页面脚本运行预算，毫秒（默认 8000）")
    parser.add_argument("--timeout", type=int, default=120, metavar="SEC", help="单个文件的硬超时，秒（默认 120）")
    parser.add_argument("--check", action="store_true", help="只体检页面是否内建打印支持，不生成 PDF")
    parser.add_argument("--open", dest="open_after", action="store_true", help="导出后用系统默认程序打开 PDF")
    args = parser.parse_args(argv)

    if not args.inputs:
        parser.print_help()
        return 2

    targets = collect_html(args.inputs, args.recursive)
    if not targets:
        print("没有找到任何 HTML 文件。", file=sys.stderr)
        return 2

    # ---------------- 体检模式 ----------------
    if args.check:
        print(f"体检 {len(targets)} 个页面：\n")
        bad = 0
        for html_path in targets:
            info = inspect_html(html_path)
            words = f"{info['words']} 词" if info["words"] else "词数未知"
            if info["missing"]:
                bad += 1
                print(f"  ✗ {html_path.name}  （{words}）缺少打印支持：{'、'.join(info['missing'])}")
                print(f"      → 该页面比较老，PDF 会是深色主题且只有 1 个词条详情。"
                      f"用当前 assets/template.html 重做即可。")
            else:
                print(f"  ✓ {html_path.name}  （{words}）打印支持完备")
        print(f"\n结论：{len(targets) - bad} 个完备，{bad} 个待更新。")
        return 0 if bad == 0 else 1

    # ---------------- 找浏览器 ----------------
    browser = find_browser(args.browser)
    if not browser:
        print("找不到 Chromium 内核浏览器（Edge / Chrome / Chromium / Brave / Arc 均可）。", file=sys.stderr)
        print(f"可以用 --browser 指定路径，或设置环境变量 {ENV_BROWSER_OVERRIDE}。", file=sys.stderr)
        return 1
    if len(targets) > 1 and args.out:
        print("--out 只能配合单个输入文件使用。", file=sys.stderr)
        return 2
    print(f"浏览器：{browser}\n")

    # ---------------- 逐个导出 ----------------
    started = time.time()
    failures = 0
    produced: list[Path] = []

    for index, html_path in enumerate(targets, start=1):
        info = inspect_html(html_path)
        if args.out:
            pdf_path = Path(args.out).expanduser()
        elif args.out_dir:
            pdf_path = Path(args.out_dir).expanduser() / (html_path.stem + ".pdf")
        else:
            pdf_path = html_path.with_suffix(".pdf")

        print(f"[{index}/{len(targets)}] {html_path.name}  →  {pdf_path.name}")
        if info["words"]:
            print(f"          正文 {info['words']} 词", end="")
        if info["missing"]:
            print(f"　⚠ 未内建打印支持，版式可能不适合打印", end="")
        print()

        ok, detail = print_to_pdf(
            browser, html_path, pdf_path,
            header_footer=args.header_footer,
            virtual_time_budget_ms=args.virtual_time_budget,
            timeout_s=args.timeout,
        )
        if ok:
            print(f"          ✓ 完成 · {detail}")
            produced.append(pdf_path)
        else:
            print(f"          ✗ 失败 · {detail}", file=sys.stderr)
            failures += 1

    # ---------------- 汇总 ----------------
    elapsed = time.time() - started
    print(f"\n{'=' * 58}")
    print(f"成功 {len(produced)} 个 / 失败 {failures} 个 · 耗时 {elapsed:.1f}s")
    for pdf_path in produced:
        ok, detail = validate_pdf(pdf_path)
        print(f"  {pdf_path}   ({detail})")

    if produced and args.open_after:
        opener = "open" if sys.platform == "darwin" else ("start" if os.name == "nt" else "xdg-open")
        try:
            subprocess.run([opener, str(produced[0])], check=False)
        except OSError:
            pass

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
