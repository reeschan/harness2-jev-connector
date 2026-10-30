# Copyright 2026 Ryuki Tobita
# SPDX-License-Identifier: Apache-2.0
"""Atelier の runs.jsonl (record_run.py が書く) から差し戻し率とその原因、規約・メモリ・範囲の守り具合を出す。読むだけで何も書き換えない。

使い方:
  atelier-rework-report [docs/pbi/runs.jsonl ...]     # 省略時は docs/pbi/runs.jsonl
  atelier-rework-report --archive [DIR]               # 保全先の全 runs.jsonl (リポジトリをまたぐ)
  atelier-rework-report ... --json                    # 表の代わりに JSON
  atelier-rework-report ... --candidates 20           # 指示を直す候補の行数 (既定 10)

数え方 (規則はこのファイルにだけある。runs.jsonl には書かない):
  PBI は (repo, work_key, pbi) で 1 件。「使えず」だけの PBI は判定していないので数えない。
  一発合格: 最初に「使えず」以外の結果を出した実行が合格。
  差し戻し率: 差し戻し・昇格・保留の行が 1 つでもある PBI の割合。
  原因: その 3 つの結果の行の judged.cause (record_run.py --cause)。spec・convention・scope・memory は指示の側
  (worker.md・PBI の書き方を直せば減らせる)、model はモデルの側、env は環境。原因の無い行は「未記入」に数える。
  守り具合: memory.violation (許されない shared-memory の使い方)、memory.missed_refs (Refs の読み漏れ。agent だけ)、
  unchanged.violation (Do not touch の変更) を、確かめられた行のうちの割合で出す。
  指示の版: prompt.worker (docs/pbi/worker.md の sha256 の先頭 12 文字)。PBI の版は最初の実行の版。指示を直した前後を
  この表で比べる。atelier-cases で作り直した結果とあわせて、指示を直すかを決める。
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Atelier の record_run.py の CAUSES と PROMPT_CAUSES と同じ値
CAUSES = ("spec", "convention", "scope", "memory", "model", "env")
PROMPT_CAUSES = ("spec", "convention", "scope", "memory")

UNAVAILABLE = "使えず"


def archive_root() -> Path:
    """Atelier の保全先 (record_run.archive_root と同じ): PBI_RUNS_ARCHIVE、無ければ ~/.claude/pbi-runs。"""
    env = os.environ.get("PBI_RUNS_ARCHIVE")
    return Path(env) if env else Path.home() / ".claude" / "pbi-runs"


def label(row: dict) -> str:
    effort = row.get("effort")
    return f"{row.get('runner')} {row.get('model')}" + (f" {effort}" if effort else "")


def load(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("judged"), dict):
                rows.append(row)
    return rows


PASS = "合格"
SENT_BACK = ("差し戻し", "昇格", "保留")
UNSET = "未記入"


def order(r: dict):
    return (r.get("rework", 0), r.get("attempt", 0))


def group_pbis(rows: list[dict]) -> dict:
    by_pbi = {}
    for r in rows:
        by_pbi.setdefault((r.get("repo"), r.get("work_key"), r.get("pbi")), []).append(r)
    return {k: sorted(v, key=order) for k, v in by_pbi.items()}


def outcome(r: dict) -> str | None:
    return (r.get("judged") or {}).get("outcome")


def causes_of(r: dict) -> list[str]:
    return (r.get("judged") or {}).get("cause") or [UNSET]


def rate(n: int, d: int) -> float | None:
    return n / d if d else None


def pbi_stats(pbi_rows: list[dict]) -> dict | None:
    delivered = [r for r in pbi_rows if outcome(r) != UNAVAILABLE]
    if not delivered:
        return None
    sent = [r for r in delivered if outcome(r) in SENT_BACK]
    causes = [c for r in sent for c in causes_of(r)]
    return {
        "first_pass": outcome(delivered[0]) == PASS,
        "sent_back": bool(sent),
        "sent_rows": len(sent),
        "passed": any(outcome(r) == PASS for r in delivered),
        "stopped": any(outcome(r) in ("昇格", "保留") for r in delivered),
        "prompt_causes": sum(1 for c in causes if c in PROMPT_CAUSES),
        "worker": (delivered[0].get("prompt") or {}).get("worker"),
        "first_model": label(delivered[0]),
        "first_at": delivered[0].get("recorded_at") or "",
    }


def checked_rate(rows: list[dict], get) -> dict:
    values = [get(r) for r in rows]
    known = [v for v in values if v is not None]
    hits = sum(1 for v in known if v)
    return {"hits": hits, "checked": len(known), "rate": rate(hits, len(known))}


def summarize_group(stats: list[dict]) -> dict:
    n = len(stats)
    return {
        "pbis": n,
        "first_pass_rate": rate(sum(s["first_pass"] for s in stats), n),
        "rework_rate": rate(sum(s["sent_back"] for s in stats), n),
        "stop_rate": rate(sum(s["stopped"] for s in stats), n),
        "sent_back_rows_per_pbi": rate(sum(s["sent_rows"] for s in stats), n),
        "prompt_causes_per_pbi": rate(sum(s["prompt_causes"] for s in stats), n),
    }


def summarize(rows: list[dict], candidates: int = 10) -> dict:
    pbis = group_pbis(rows)
    stats = {k: s for k, v in pbis.items() if (s := pbi_stats(v)) is not None}
    sent = [r for r in rows if outcome(r) in SENT_BACK]
    cause_counts = {c: 0 for c in (*CAUSES, UNSET)}
    for r in sent:
        for c in causes_of(r):
            cause_counts[c if c in cause_counts else UNSET] += 1
    attributed = sum(v for c, v in cause_counts.items() if c != UNSET)
    prompt_side = sum(cause_counts[c] for c in PROMPT_CAUSES)

    versions = {}
    for s in sorted(stats.values(), key=lambda s: s["first_at"]):
        versions.setdefault(s["worker"] or UNSET, []).append(s)
    models = {}
    for s in stats.values():
        models.setdefault(s["first_model"], []).append(s)

    judged = [r for r in rows if outcome(r) != UNAVAILABLE]
    compliance = {
        "memory_violation": checked_rate(
            judged, lambda r: (r.get("memory") or {}).get("violation")
        ),
        "missed_refs": checked_rate(
            judged,
            lambda r: (
                None if (m := (r.get("memory") or {}).get("missed_refs")) is None else bool(m)
            ),
        ),
        "do_not_touch": checked_rate(judged, lambda r: (r.get("unchanged") or {}).get("violation")),
        "convention_cause": {
            "hits": cause_counts["convention"],
            "checked": len(sent),
            "rate": rate(cause_counts["convention"], len(sent)),
        },
    }
    picks = [
        r
        for r in sorted(sent, key=lambda r: r.get("recorded_at") or "", reverse=True)
        if set(causes_of(r)) & set(PROMPT_CAUSES)
    ][:candidates]
    return {
        "rows": len(rows),
        "pbis": len(stats),
        "overall": summarize_group(list(stats.values())),
        "causes": cause_counts,
        "sent_back_rows": len(sent),
        "prompt_side_share": rate(prompt_side, attributed),
        "unattributed": cause_counts[UNSET],
        "compliance": compliance,
        "by_worker_version": [{"worker": k, **summarize_group(v)} for k, v in versions.items()],
        "by_first_model": [{"model": k, **summarize_group(v)} for k, v in sorted(models.items())],
        "candidates": [
            {
                "repo": r.get("repo"),
                "work_key": r.get("work_key"),
                "report": r.get("report"),
                "attempt": r.get("attempt"),
                "outcome": outcome(r),
                "cause": causes_of(r),
                "model": label(r),
                "archive": (r.get("archive") or {}).get("dir"),
            }
            for r in picks
        ],
    }


def pct(v):
    return "—" if v is None else f"{v:.0%}"


def avg(v):
    return "—" if v is None else f"{v:.2f}"


def group_table(title: str, key: str, groups: list[dict]) -> list[str]:
    lines = [
        "",
        title,
        "",
        f"| {key} | PBI | 一発合格率 | 差し戻し率 | 停止率 | 差し戻し行/PBI | 指示側の原因/PBI |",
        "|---|---|---|---|---|---|---|",
    ]
    for g in groups:
        lines.append(
            f"| {g.get('worker') or g.get('model')} | {g['pbis']} | {pct(g['first_pass_rate'])} | "
            f"{pct(g['rework_rate'])} | {pct(g['stop_rate'])} | {avg(g['sent_back_rows_per_pbi'])} | "
            f"{avg(g['prompt_causes_per_pbi'])} |"
        )
    return lines


def table(report: dict) -> str:
    o = report["overall"]
    lines = [
        f"実行 {report['rows']} 件 / 判定した PBI {report['pbis']} 件: 一発合格率 {pct(o['first_pass_rate'])}、"
        f"差し戻し率 {pct(o['rework_rate'])}、停止率 {pct(o['stop_rate'])}",
        "",
        f"差し戻し・昇格・保留の原因 ({report['sent_back_rows']} 行。1 行に複数あり。"
        f"指示の側 {'/'.join(PROMPT_CAUSES)} の割合 {pct(report['prompt_side_share'])}、未記入 {report['unattributed']})",
        "",
        "| " + " | ".join(report["causes"]) + " |",
        "|" + "---|" * len(report["causes"]),
        "| " + " | ".join(str(v) for v in report["causes"].values()) + " |",
        "",
        "守り具合 (確かめられた行のうち)",
        "",
        "| 項目 | 件数 | 確かめた行 | 割合 |",
        "|---|---|---|---|",
    ]
    names = {
        "memory_violation": "shared-memory の違反",
        "missed_refs": "Refs の読み漏れ (agent)",
        "do_not_touch": "Do not touch の変更",
        "convention_cause": "規約が原因の差し戻し (差し戻し行のうち)",
    }
    for k, v in report["compliance"].items():
        lines.append(f"| {names[k]} | {v['hits']} | {v['checked']} | {pct(v['rate'])} |")
    lines += group_table("指示の版 (worker.md) 別。古い順", "版", report["by_worker_version"])
    lines += group_table("最初に成果物を出したモデル別", "モデル", report["by_first_model"])
    if report["candidates"]:
        lines += [
            "",
            "指示を直す候補 (指示の側の原因の行。新しい順)",
            "",
            "| 報告 | attempt | 結果 | 原因 | モデル | 保全先 |",
            "|---|---|---|---|---|---|",
        ]
        for c in report["candidates"]:
            lines.append(
                f"| {c['repo']} {c['report']} | {c['attempt']} | {c['outcome']} | {','.join(c['cause'])} | "
                f"{c['model']} | {c['archive'] or '—'} |"
            )
    return "\n".join(lines)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--archive", nargs="?", const="", default=None, metavar="DIR")
    ap.add_argument("--candidates", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.archive is not None:
        root = Path(args.archive) if args.archive else archive_root()
        paths = sorted(root.glob("*/*/runs.jsonl"))
    else:
        paths = [Path(p) for p in args.paths] or [Path("docs/pbi/runs.jsonl")]
    missing = [p for p in paths if not p.is_file()]
    if missing or not paths:
        print(
            f"runs.jsonl が無い: {', '.join(map(str, missing)) or '(保全先に 0 件)'}",
            file=sys.stderr,
        )
        sys.exit(2)
    report = summarize(load(paths), args.candidates)
    print(json.dumps(report, ensure_ascii=False) if args.json else table(report))


if __name__ == "__main__":
    main()
