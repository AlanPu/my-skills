"""从 bash -x 轨迹里提取变量值。

单独成文件而不是塞进 `python3 -c` 里，是因为：
  - shell 单引号字符串里写不了单引号，正则 `\\$'(.*)'` 直接会把 bash 语法搞崩；
  - 嵌在字符串里的 python 代码没法单独测试、没法高亮、没法 lint。

用法：python3 trace.py <变量名> < trace.log
输出：该变量最后一次赋值的值（无则输出空行）。
"""
import re
import sys


def unquote(v):
    """还原 bash -x 打出来的值：去掉包裹引号，解 $'...' 转义。"""
    if len(v) >= 2 and v[0] == v[-1] == '\'':
        return v[1:-1]
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1]
    m = re.fullmatch(r"\$'(.*)'", v)
    if m:
        body = m.group(1)
        # 把 \xHH 还原成字节，再按 UTF-8 解码（bash 3.2 对非 ASCII 会打成 \xHH）
        body = re.sub(r'\\x([0-9A-Fa-f]{2})', lambda x: chr(int(x.group(1), 16)), body)
        return body.encode('latin-1', 'replace').decode('utf-8', 'replace')
    return v


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: trace.py <var-name> < trace.log")
    name = sys.argv[1]
    # 轨迹行的形式：`+$ NAME=value` / `++$ NAME=value`（PS4 是 '+$ '）。
    # 注意 `^\+* ` 是错的：PS4 里的 "$" 会顶在 "+" 后面，正确写法是 `^[+]+\$ `。
    pat = re.compile(r'^[+]+\$ ' + re.escape(name) + r'=(.*)$')
    last = None
    for line in sys.stdin.buffer:          # 字节流读入，避免 stdout 上的非 UTF-8 字节炸掉
        line = line.decode('utf-8', 'replace')
        m = pat.match(line.rstrip('\n'))
        if m:
            last = unquote(m.group(1).strip())
    print('' if last is None else last)


if __name__ == '__main__':
    main()
