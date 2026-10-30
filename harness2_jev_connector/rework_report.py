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
  時間とトークン: PBI ごとに、使えず・差し戻しも含めた全実行の time.seconds・steps・tokens を足す (分からない実行は
  足さない)。表は中央値。差し戻しのトークン割合は、最初に成果物を出した実行より後の実行と「使えず」の実行のトークンが
  全体に占める割合 (やり直しで失った分)。キャッシュ読みの割合は tokens.cache_read / (input + cache_read)。
  大きさ: runs.jsonl と同じフォルダに cases.jsonl (Atelier の export_cases.py) があれば、その size を PBI に付ける。
  Targets の数・Refs の数・実装担当に渡した文章の文字数 (worker.md・PBI・brief・Refs の要旨) と最初の tier で分ける。
  重い PBI: トークンの多い順。大きいのに差し戻されたものは PBI を分ける候補、Refs や文章が長いのに一発合格のものは
  渡す文章を削る候補として見る。
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


def load_sizes(paths: list[Path]) -> dict:
    """各 runs.jsonl の隣の cases.jsonl から (repo, work_key, pbi) -> size。"""
    sizes = {}
    for p in paths:
        cases = p.with_name("cases.jsonl")
        if not cases.is_file():
            continue
        for line in cases.read_text(encoding="utf-8").splitlines():
            try:
                c = json.loads(line)
            except ValueError:
                continue
            if isinstance(c, dict) and isinstance(c.get("size"), dict):
                sizes[(c.get("repo"), c.get("work_key"), c.get("pbi"))] = c["size"]
    return sizes


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


def total(values) -> float | None:
    known = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return sum(known) if known else None


def seconds_of(r: dict):
    return (r.get("time") or {}).get("seconds")


def tokens_of(r: dict):
    return (r.get("tokens") or {}).get("total")


def median(values) -> float | None:
    known = sorted(v for v in values if v is not None)
    if not known:
        return None
    mid = len(known) // 2
    return known[mid] if len(known) % 2 else (known[mid - 1] + known[mid]) / 2


def bucket(n, edges: tuple[int, ...]) -> str:
    """n を edges で区切った幅の名前。edges=(1, 3) なら 0・1・2-3・4+。"""
    if n is None:
        return UNSET
    low = 0
    for edge in edges:
        if n <= edge:
            return str(edge) if low == edge else f"{low}-{edge}"
        low = edge + 1
    return f"{low}+"


def text_chars(size: dict | None):
    chars = (size or {}).get("chars") or {}
    return total(chars.get(k) for k in ("worker", "pbi", "brief", "refs"))


def pbi_stats(pbi_rows: list[dict], size: dict | None = None) -> dict | None:
    delivered = [r for r in pbi_rows if outcome(r) != UNAVAILABLE]
    if not delivered:
        return None
    sent = [r for r in delivered if outcome(r) in SENT_BACK]
    causes = [c for r in sent for c in causes_of(r)]
    first = pbi_rows.index(delivered[0])
    wasted = [r for i, r in enumerate(pbi_rows) if i > first or outcome(r) == UNAVAILABLE]
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
        "key": (delivered[0].get("repo"), delivered[0].get("work_key"), delivered[0].get("pbi")),
        "attempts": len(pbi_rows),
        "seconds": total(seconds_of(r) for r in pbi_rows),
        "steps": total(r.get("steps") for r in pbi_rows),
        "tokens": total(tokens_of(r) for r in pbi_rows),
        "wasted_tokens": total(tokens_of(r) for r in wasted) or 0,
        "tier": (size or {}).get("tier") or delivered[0].get("tier"),
        "size": size,
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
        "median_seconds": median(s["seconds"] for s in stats),
        "median_steps": median(s["steps"] for s in stats),
        "median_tokens": median(s["tokens"] for s in stats),
        "rework_token_share": rate(
            sum(s["wasted_tokens"] for s in stats if s["tokens"]),
            sum(s["tokens"] for s in stats if s["tokens"]),
        ),
    }


def grouped(stats: list[dict], key, name: str) -> list[dict]:
    groups = {}
    for s in stats:
        groups.setdefault(key(s), []).append(s)
    return [
        {name: k, **summarize_group(v)}
        for k, v in sorted(groups.items(), key=lambda kv: sort_key(kv[0]))
    ]


def sort_key(name: str):
    head = name.split("-")[0].rstrip("+")
    return (0, int(head), name) if head.isdigit() else (1, 0, name)


def token_parts(rows: list[dict]) -> dict:
    parts = {
        k: total((r.get("tokens") or {}).get(k) for r in rows)
        for k in ("input", "output", "reasoning", "cache_read", "cache_write")
    }
    read = parts["cache_read"] or 0
    fresh = parts["input"] or 0
    return {**parts, "cache_read_share": rate(read, read + fresh)}


def summarize(rows: list[dict], candidates: int = 10, sizes: dict | None = None) -> dict:
    pbis = group_pbis(rows)
    sizes = sizes or {}
    stats = {k: s for k, v in pbis.items() if (s := pbi_stats(v, sizes.get(k))) is not None}
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
    sized = [s for s in stats.values() if s["size"]]
    heavy = sorted(
        (s for s in stats.values() if s["tokens"] is not None), key=lambda s: -s["tokens"]
    )
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
        "cost": {
            "token_parts": token_parts(rows),
            "pbis_with_size": len(sized),
            "by_tier": grouped(list(stats.values()), lambda s: s["tier"] or UNSET, "tier"),
            "by_targets": grouped(
                sized, lambda s: bucket(s["size"].get("targets"), (0, 1, 3)), "targets"
            ),
            "by_refs": grouped(sized, lambda s: bucket(s["size"].get("refs"), (0, 2)), "refs"),
            "by_text_chars": grouped(
                sized, lambda s: bucket(text_chars(s["size"]), (3999, 7999, 15999)), "chars"
            ),
            "heavy": [
                {
                    "repo": s["key"][0],
                    "work_key": s["key"][1],
                    "pbi": s["key"][2],
                    "tokens": s["tokens"],
                    "seconds": s["seconds"],
                    "steps": s["steps"],
                    "attempts": s["attempts"],
                    "first_pass": s["first_pass"],
                    "tier": s["tier"],
                    "targets": (s["size"] or {}).get("targets"),
                    "refs": (s["size"] or {}).get("refs"),
                    "chars": text_chars(s["size"]),
                }
                for s in heavy[:candidates]
            ],
        },
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


def num(v):
    return "—" if v is None else f"{v:,.0f}"


def group_table(title: str, key: str, name: str, groups: list[dict]) -> list[str]:
    lines = [
        "",
        title,
        "",
        f"| {key} | PBI | 一発合格率 | 差し戻し率 | 停止率 | 差し戻し行/PBI | 指示側の原因/PBI | "
        "秒 (中央値) | ステップ (中央値) | トークン (中央値) | 差し戻しのトークン割合 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for g in groups:
        lines.append(
            f"| {g[name]} | {g['pbis']} | {pct(g['first_pass_rate'])} | "
            f"{pct(g['rework_rate'])} | {pct(g['stop_rate'])} | {avg(g['sent_back_rows_per_pbi'])} | "
            f"{avg(g['prompt_causes_per_pbi'])} | {num(g['median_seconds'])} | {num(g['median_steps'])} | "
            f"{num(g['median_tokens'])} | {pct(g['rework_token_share'])} |"
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
    o_cost = report["overall"]
    parts = report["cost"]["token_parts"]
    lines += [
        "",
        f"時間とトークン (PBI ごとの全実行の合計の中央値): {num(o_cost['median_seconds'])} 秒、"
        f"{num(o_cost['median_steps'])} ステップ、{num(o_cost['median_tokens'])} トークン。"
        f"差し戻しと使えずで使ったトークンの割合 {pct(o_cost['rework_token_share'])}、"
        f"キャッシュ読みの割合 {pct(parts['cache_read_share'])}",
    ]
    lines += group_table(
        "指示の版 (worker.md) 別。古い順", "版", "worker", report["by_worker_version"]
    )
    lines += group_table(
        "最初に成果物を出したモデル別", "モデル", "model", report["by_first_model"]
    )
    cost = report["cost"]
    lines += group_table("最初の tier 別", "tier", "tier", cost["by_tier"])
    if cost["pbis_with_size"]:
        lines += group_table(
            f"Targets の数別 (cases.jsonl のある {cost['pbis_with_size']} 件)",
            "Targets",
            "targets",
            cost["by_targets"],
        )
        lines += group_table("Refs の数別", "Refs", "refs", cost["by_refs"])
        lines += group_table(
            "実装担当に渡した文章の文字数別 (worker.md・PBI・brief・Refs の要旨)",
            "文字数",
            "chars",
            cost["by_text_chars"],
        )
    if cost["heavy"]:
        lines += [
            "",
            "重い PBI (トークンの多い順。大きくて差し戻されたものは分ける候補、文章が長くて一発合格のものは削る候補)",
            "",
            "| PBI | トークン | 秒 | ステップ | 実行 | 一発合格 | tier | Targets | Refs | 文字数 |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for h in cost["heavy"]:
            lines.append(
                f"| {h['repo']} {h['pbi']} | {num(h['tokens'])} | {num(h['seconds'])} | {num(h['steps'])} | "
                f"{h['attempts']} | {'はい' if h['first_pass'] else 'いいえ'} | {h['tier'] or '—'} | "
                f"{num(h['targets'])} | {num(h['refs'])} | {num(h['chars'])} |"
            )
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
    report = summarize(load(paths), args.candidates, load_sizes(paths))
    print(json.dumps(report, ensure_ascii=False) if args.json else table(report))


if __name__ == "__main__":
    main()
