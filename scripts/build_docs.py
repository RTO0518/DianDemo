# -*- coding: utf-8 -*-
"""
交付文档生成脚本。

1. 把 docs 下的 Markdown 转成带格式的 .docx（宋体小四、1.5 倍行距、首行缩进，
   支持标题、表格、项目符号、行内加粗）
2. 现跑 8 道推理题，生成 docs/推理验证报告.md，避免手抄数字出错
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.reasoner.verify import run_all  # noqa: E402

DOCS = ROOT / "docs"

# (markdown 源, docx 目标)
DOC_TARGETS = [
    ("设计文档.md", "配电网本体模型设计说明.docx"),
    ("架构方案评估与采纳说明.md", "架构方案评估与采纳说明.docx"),
]

_BOLD = re.compile(r"(\*\*[^*]+\*\*)")
_CODE = re.compile(r"(`[^`]+`)")

# 遇到这些开头就不再并入当前段落
_BLOCK_START = re.compile(r"^(#{1,6}\s|\||```|\s*[-*+]\s|\s*\d+\.\s|>|---|\*\*\*)")


def _set_font(run, name: str = "宋体", size: float = 12, bold: bool = False) -> None:
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)


def _join(prev: str, nxt: str) -> str:
    """合并 markdown 的软换行：中文直接相接，英文之间补一个空格。"""
    if not prev:
        return nxt
    if prev[-1].isascii() and prev[-1].isalnum() and nxt[:1].isascii() and nxt[:1].isalnum():
        return prev + " " + nxt
    return prev + nxt


def _rich(paragraph, text: str, size: float = 12) -> None:
    """处理行内标记：**加粗** 与 `等宽`。"""
    for part in _BOLD.split(text):
        if not part:
            continue
        bold = part.startswith("**") and part.endswith("**") and len(part) > 4
        body = part[2:-2] if bold else part
        for seg in _CODE.split(body):
            if not seg:
                continue
            if seg.startswith("`") and seg.endswith("`") and len(seg) > 2:
                _set_font(paragraph.add_run(seg[1:-1]), "Consolas", size - 1, bold)
            else:
                _set_font(paragraph.add_run(seg), "宋体", size, bold)


def _split_row(line: str) -> list:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def _is_separator(line: str) -> bool:
    s = line.strip().strip("|")
    return bool(s) and set(s.replace("|", "").replace(" ", "")) <= set("-: ")


def _add_table(doc, block: list) -> None:
    rows = [_split_row(l) for l in block if not _is_separator(l)]
    if not rows:
        return
    cols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=cols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for r_idx, row in enumerate(rows):
        for c_idx in range(cols):
            cell = table.cell(r_idx, c_idx)
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(2)
            text = row[c_idx] if c_idx < len(row) else ""
            if r_idx == 0:
                _rich(p, text, 10)
                for run in p.runs:
                    run.font.bold = True
            else:
                _rich(p, text, 10)
    doc.add_paragraph()


def build_docx(md_name: str, docx_name: str) -> None:
    md_path = DOCS / md_name
    if not md_path.exists():
        print(f"跳过（源文件不存在）：{md_path}")
        return
    text = md_path.read_text(encoding="utf-8")
    doc = Document()

    section = doc.sections[0]
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(3.17)
    section.right_margin = Cm(3.17)

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
            continue

        # 表格块
        if line.strip().startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            _add_table(doc, block)
            continue

        # 分隔线
        if line.strip() in ("---", "***"):
            i += 1
            continue

        if line.startswith("## "):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(12)
            p.paragraph_format.space_after = Pt(6)
            _set_font(p.add_run(line[3:].strip()), "微软雅黑", 14, True)
        elif line.startswith("### "):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(8)
            p.paragraph_format.space_after = Pt(4)
            _set_font(p.add_run(line[4:].strip()), "微软雅黑", 12.5, True)
        elif line.startswith("# "):
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(18)
            _set_font(p.add_run(line[2:].strip()), "微软雅黑", 18, True)
        elif re.match(r"^\s*[-*]\s+", line):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.85)
            p.paragraph_format.first_line_indent = Cm(-0.4)
            p.paragraph_format.line_spacing = 1.5
            p.paragraph_format.space_after = Pt(3)
            body = re.sub(r"^\s*[-*]\s+", "", line)
            _set_font(p.add_run("· "), "宋体", 12)
            _rich(p, body, 12)
        elif re.match(r"^\s*\d+\.\s+", line):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.85)
            p.paragraph_format.first_line_indent = Cm(-0.4)
            p.paragraph_format.line_spacing = 1.5
            p.paragraph_format.space_after = Pt(3)
            m = re.match(r"^\s*(\d+)\.\s+(.*)$", line)
            _set_font(p.add_run(f"{m.group(1)}. "), "宋体", 12)
            _rich(p, m.group(2), 12)
        elif line.startswith("```"):
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Cm(0.6)
                p.paragraph_format.line_spacing = 1.2
                p.paragraph_format.space_after = Pt(1)
                _set_font(p.add_run(lines[i]), "Consolas", 10)
                i += 1
        else:
            # 普通段落：把紧随其后的软换行并进来，否则一个段落会被拆成多个，
            # 导致 `**` 这类跨行的行内标记无法成对匹配
            buf = [line.strip()]
            while i + 1 < len(lines):
                nxt = lines[i + 1].rstrip()
                if not nxt.strip() or _BLOCK_START.match(nxt):
                    break
                buf.append(nxt.strip())
                i += 1
            body = buf[0]
            for extra in buf[1:]:
                body = _join(body, extra)

            p = doc.add_paragraph()
            p.paragraph_format.first_line_indent = Cm(0.85)
            p.paragraph_format.line_spacing = 1.5
            p.paragraph_format.space_after = Pt(6)
            _rich(p, body, 12)
        i += 1

    out = DOCS / docx_name
    doc.save(str(out))
    print(f"已生成 {out}")


def build_report() -> None:
    report = run_all()
    lines = [
        "# 推理验证报告",
        "",
        f"生成时间：由脚本现跑，共 {report['total']} 题，通过 {report['passed']} 题。",
        "",
        "## 一、验证方式",
        "",
        "每道题都写成可执行的断言：预期答案写在用例里，实际答案由推理引擎在运行时计算，"
        "两者比对一致才算通过。因此报告中的数字不是抄来的，是跑出来的。",
        "",
        "所有题目固定按**基线阈值**计算（过载 100%、重载 80%、转供上限 100%），"
        "不受界面上的阈值调节影响——阈值是演示用的变量，验证题的预期答案是基准。",
        "",
        "## 二、逐题结果",
        "",
        "| 编号 | 问题 | 依据规则 | 预期答案 | 引擎实际输出 | 结论 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in report["results"]:
        lines.append(
            f"| {r['id']} | {r['question']} | {r['rule']} | {r['expected']} | {r['actual']} | "
            f"{'通过' if r['passed'] else '不一致'} |"
        )

    lines += ["", "## 三、关键推理链", ""]
    for r in report["results"]:
        if not r["trace"]:
            continue
        lines.append(f"### {r['id']} {r['question']}")
        lines.append("")
        for step in r["trace"][:8]:
            lines.append(f"- **{step['rule']} {step['rule_name']}** · {step['action']}：{step['detail']}")
        lines.append("")

    lines += [
        "## 四、覆盖面说明",
        "",
        "前 5 题对应需求文档指定的推理性问题，后 3 题覆盖评审最可能追问的三类边界：",
        "",
        "1. **可行转供**：容量足够时给出方案、转移负荷、转供后负载率与复核结论；",
        "2. **不可行转供**：容量不足时给出缺口数值与替代动作，而不是含糊其辞；",
        "3. **无解场景**：配变级过载无法靠 10kV 联络解决，靠基数公理直接推出「无备用配变」。",
        "",
        "另有两项独立自检：",
        "",
        "- 把规则层结论断言回 OWL，运行推理闭包后检查不相交冲突，"
        "确认同一设备不会既被判为带电又被判为失电；",
        "- 用数据接入模块从台账 CSV 重新生成三元组，再跑一遍本题集，"
        "确认「换一批数据进来，推理结论依然对得上」（`tests/test_ingest.py` 中的闭环用例）。",
    ]
    out = DOCS / "推理验证报告.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"已生成 {out}（{report['summary']}）")


if __name__ == "__main__":
    for md_name, docx_name in DOC_TARGETS:
        build_docx(md_name, docx_name)
    build_report()
