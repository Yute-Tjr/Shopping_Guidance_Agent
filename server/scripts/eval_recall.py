"""Top-K Recall 评测：把黄金集逐条 embed → Milvus search → 计算召回率。

依据 docs/02 §9 指标：黄金集每条标 1-3 个 product_id，Top-K 命中视为该条召回成功。
Phase 1 阶段不带 metadata 过滤（价格 / 品牌排除），纯语义向量召回——price_filter /
brand_exclude 意图同样按"是否在 Top-K 出现金 ID"判定，过滤逻辑要等 Phase 2 query
rewriter 落地后再单独评。

用法：
    cd server
    python -m scripts.eval_recall                          # 跑全部 query
    python -m scripts.eval_recall --top-k 5                # 只看 Top-5
    python -m scripts.eval_recall --queries path/to.json   # 自定义黄金集
    python -m scripts.eval_recall --output report.md       # 输出 Markdown 报告
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from app.config import settings
from app.rag.embedder import build_embedder_from_settings
from app.rag.milvus_store import ProductTextStore
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_GOLDSET = Path(__file__).resolve().parent / "eval" / "queries.json"
DEFAULT_TOP_KS = (1, 3, 5, 10)
# 搜出更多 chunk，因为同一商品多条 chunk 会进 Top-K；做完去重后再按 K 截断
SEARCH_LIMIT = 50


def load_goldset(path: Path) -> list[dict]:
    """加载黄金集。每条记录必须含 id / query / gold / intent。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    for q in data:
        assert {"id", "query", "gold", "intent"} <= q.keys(), f"黄金集条目缺字段：{q}"
        assert isinstance(q["gold"], list) and q["gold"], f"gold 必须非空列表：{q['id']}"
    return data


def dedupe_to_product_ids(hits: Iterable[dict]) -> list[str]:
    """把 Milvus 搜出来的 chunk 列表按 product_id 去重，保留首次出现顺序（即得分最高的）。"""
    seen: list[str] = []
    seen_set: set[str] = set()
    for h in hits:
        pid = h.get("entity", {}).get("product_id")
        if pid and pid not in seen_set:
            seen.append(pid)
            seen_set.add(pid)
    return seen


def hit_at_k(ranked_pids: list[str], gold: list[str], k: int) -> bool:
    """gold 中只要有一个 product_id 出现在 ranked_pids[:k]，记为命中。"""
    top = set(ranked_pids[:k])
    return any(g in top for g in gold)


def render_markdown_report(
    overall: dict[int, float],
    by_intent: dict[str, dict[int, float]],
    per_query: list[dict],
    top_ks: tuple[int, ...],
    embedding_model: str,
    embedding_dim: int,
    elapsed_sec: float,
    n_queries: int,
) -> str:
    """把评测结果格式化成 Markdown，便于直接贴 README。"""
    lines: list[str] = []
    lines.append("# Phase 1 检索召回评测")
    lines.append("")
    lines.append(f"- Embedding 模型：`{embedding_model}`（dim={embedding_dim}）")
    lines.append(f"- 黄金集：{n_queries} 条 query")
    lines.append(f"- 检索引擎：Milvus Lite (FLAT / IP)，每条 query 取 Top-{SEARCH_LIMIT} chunk 再按 product_id 去重")
    lines.append(f"- 总耗时：{elapsed_sec:.1f}s")
    lines.append("")
    lines.append("## 总体 Top-K Recall")
    lines.append("")
    lines.append("| 指标 | " + " | ".join(f"Top-{k}" for k in top_ks) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in top_ks) + " |")
    lines.append("| Recall | " + " | ".join(f"{overall[k]:.2%}" for k in top_ks) + " |")
    lines.append("")
    lines.append("## 按意图分组")
    lines.append("")
    lines.append("| 意图 | 条数 | " + " | ".join(f"Top-{k}" for k in top_ks) + " |")
    lines.append("| --- | --- | " + " | ".join("---" for _ in top_ks) + " |")
    for intent in sorted(by_intent):
        scores = by_intent[intent]
        n = sum(1 for q in per_query if q["intent"] == intent)
        lines.append(
            f"| {intent} | {n} | " + " | ".join(f"{scores[k]:.2%}" for k in top_ks) + " |"
        )
    lines.append("")
    lines.append("## 逐条明细")
    lines.append("")
    lines.append("| ID | 意图 | Query | 黄金 | Top-5 命中 | Top-1 商品 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for q in per_query:
        gold_str = ", ".join(q["gold"])
        hit_mark = "✅" if q["hits"][5] else "❌"
        top1 = q["ranked"][0] if q["ranked"] else "-"
        lines.append(
            f"| {q['id']} | {q['intent']} | {q['query']} | {gold_str} | {hit_mark} | {top1} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1 检索召回评测")
    parser.add_argument("--queries", type=Path, default=DEFAULT_GOLDSET)
    parser.add_argument("--top-k", type=int, action="append", default=None,
                        help="可重复指定，如 --top-k 1 --top-k 5；缺省 (1,3,5,10)")
    parser.add_argument("--output", type=Path, default=None,
                        help="把 Markdown 报告写到该文件；缺省只打印汇总")
    parser.add_argument("--verbose", action="store_true", help="打印每条 query 的 Top-3 命中详情")
    args = parser.parse_args()

    top_ks = tuple(sorted(args.top_k)) if args.top_k else DEFAULT_TOP_KS

    goldset = load_goldset(args.queries)
    logger.info("加载 %d 条 query（来自 %s）", len(goldset), args.queries)

    embedder = build_embedder_from_settings()
    dim = embedder.dim
    store = ProductTextStore(db_path=settings.milvus_db_path, dim=dim)
    store.ensure_collection()
    logger.info("Milvus collection 已加载（products_text, dim=%d）", dim)

    per_query: list[dict] = []
    hits_count: dict[int, int] = {k: 0 for k in top_ks}
    by_intent_hits: dict[str, dict[int, int]] = defaultdict(lambda: {k: 0 for k in top_ks})
    by_intent_total: dict[str, int] = defaultdict(int)

    started = time.time()
    for entry in goldset:
        qvec = embedder.embed_one(entry["query"])
        hits = store.search(qvec, top_k=SEARCH_LIMIT)
        ranked = dedupe_to_product_ids(hits)
        query_hits: dict[int, bool] = {}
        for k in top_ks:
            ok = hit_at_k(ranked, entry["gold"], k)
            query_hits[k] = ok
            if ok:
                hits_count[k] += 1
                by_intent_hits[entry["intent"]][k] += 1
        by_intent_total[entry["intent"]] += 1
        per_query.append({**entry, "ranked": ranked, "hits": query_hits})
        if args.verbose:
            print(f"[{entry['id']}] {entry['query']}")
            print(f"  gold: {entry['gold']}")
            print(f"  top5: {ranked[:5]}")
            print(f"  hit@5: {query_hits[5]}")

    elapsed = time.time() - started

    n = len(goldset)
    overall = {k: hits_count[k] / n for k in top_ks}
    by_intent = {
        intent: {k: by_intent_hits[intent][k] / by_intent_total[intent] for k in top_ks}
        for intent in by_intent_total
    }

    print()
    print("=" * 60)
    print(f"Phase 1 检索召回评测（{n} 条 query，{elapsed:.1f}s）")
    print("=" * 60)
    print(f"Embedding: {embedder.model} (dim={dim})")
    print()
    print("Top-K Recall 总体：")
    for k in top_ks:
        print(f"  Top-{k:>2}: {overall[k]:.2%}  ({hits_count[k]}/{n})")
    print()
    print("按意图：")
    for intent in sorted(by_intent_total):
        scores = " | ".join(f"Top-{k}={by_intent[intent][k]:.0%}" for k in top_ks)
        print(f"  [{intent:18s}] n={by_intent_total[intent]:2d}  {scores}")

    if args.output:
        report = render_markdown_report(
            overall=overall,
            by_intent=by_intent,
            per_query=per_query,
            top_ks=top_ks,
            embedding_model=embedder.model,
            embedding_dim=dim,
            elapsed_sec=elapsed,
            n_queries=n,
        )
        args.output.write_text(report, encoding="utf-8")
        logger.info("Markdown 报告已写出：%s", args.output)


if __name__ == "__main__":
    main()
