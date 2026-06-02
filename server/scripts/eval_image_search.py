"""Phase 5 多模态图搜评测：跑 image_queries.json 黄金集，按四类输出 Top-1/3/5。

用法：

    cd server
    python -m scripts.eval_image_search                    # 输出到 stdout
    python -m scripts.eval_image_search --output ../docs/phase5_eval_report.md

依赖前置：
- 已跑过 build_image_index.py（让 collection 含 chunk_type=image 行）；
- .env 配好 ARK_API_KEY 等可让 embedder 真实调 vision API；
- 数据集目录 ecommerce_agent_dataset 在仓库根。
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.config import settings
from app.agent.query_rewriter import build_query_rewriter
from app.rag.embedder import build_embedder_from_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

QUERIES_PATH = Path(__file__).parent / "eval" / "image_queries.json"


def _topk_hit(predicted: list[str], expected: list[str], k: int) -> bool:
    """前 k 个预测中只要命中 expected 任意一个即算成功。"""
    return any(p in expected for p in predicted[:k])


async def _build_branch():
    """脱离 FastAPI 容器手动装一个 MultimodalBranch 跑测试。"""
    from app.agent.multimodal_branch import build_multimodal_branch
    from app.api.deps import get_structured_retriever
    from app.rag.image_embed_cache import ImageEmbedCache
    from app.rag.milvus_store import COLLECTION_NAME, ProductTextStore
    from app.rag.retriever import RagRetriever

    project_root = Path(__file__).resolve().parents[2]
    brands = await _load_known_brands(project_root)
    logger.info("图搜评测加载品牌词表 %d 个", len(brands))

    embedder = build_embedder_from_settings()
    dim = embedder.dim  # 探测一次
    store = ProductTextStore(db_path=settings.milvus_db_path, dim=dim)
    store.client.load_collection(COLLECTION_NAME)
    retriever = RagRetriever(embedder=embedder, store=store)
    branch = build_multimodal_branch(
        embedder=embedder,
        retriever=retriever,
        cache=ImageEmbedCache(),
        query_rewriter=build_query_rewriter(known_brands=brands),
        structured_retriever=get_structured_retriever(),
    )
    return branch, embedder, store


async def _load_known_brands(
    project_root: Path,
    *,
    repo_factory: Any | None = None,
) -> list[str]:
    """加载评测用品牌白名单，优先 MySQL，失败时从本地数据集 JSON 回退。

    FastAPI 启动时会把品牌词表注入全局 QueryRewriter；评测脚本脱离 lifespan
    手动装配，必须显式做同样的事，否则「不要苹果/耐克」不会生成 brand filter。
    """
    if repo_factory is None:
        from app.db.product_repo import ProductRepository

        repo_factory = ProductRepository

    try:
        repo = repo_factory()
        brands = await repo.list_brands()
    except Exception as exc:  # noqa: BLE001
        logger.warning("MySQL 拉品牌失败，从本地数据集回退：%s", exc)
        brands = []

    if not brands:
        brands = _load_brands_from_dataset(project_root)

    return sorted({b.strip() for b in brands if isinstance(b, str) and b.strip()})


def _load_brands_from_dataset(project_root: Path) -> list[str]:
    dataset_root = project_root / "ecommerce_agent_dataset"
    brands: list[str] = []
    for path in dataset_root.glob("*/data/*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("跳过无法读取的数据文件 %s：%s", path, exc)
            continue
        brand = payload.get("brand")
        if isinstance(brand, str) and brand.strip():
            brands.append(brand.strip())
    return brands


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--queries", type=Path, default=QUERIES_PATH)
    parser.add_argument("--top-n-products", type=int, default=10)
    args = parser.parse_args()

    with args.queries.open("r", encoding="utf-8") as f:
        cases = json.load(f)
    logger.info("载入 %d 条评测 case", len(cases))

    branch, embedder, store = await _build_branch()

    # 跑每个 case：复用 branch.query_rewriter 抽 filter，但绕过 cache 直接 embed_multimodal
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    project_root = Path(__file__).resolve().parents[2]  # 项目根

    for c in cases:
        img_abs = project_root / c["image_path"]
        if not img_abs.exists():
            logger.warning("跳过 %s：图片缺失 %s", c["case_id"], img_abs)
            continue

        try:
            vec = embedder.embed_multimodal(
                text=c.get("text") or None,
                image_path=str(img_abs),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("%s embed 失败：%s", c["case_id"], exc)
            continue

        # 构造 filter_expr（结构化 + chunk_type）
        if branch.query_rewriter is not None:
            parsed = await branch.query_rewriter.parse(c.get("text", ""))
        else:
            from app.agent.query_rewriter import ParsedQuery
            parsed = ParsedQuery(search_query=c.get("text", ""))
        structural = parsed.to_filter_expr()
        if structural:
            filter_expr = f'({structural}) and chunk_type in ["image", "title"]'
        else:
            filter_expr = 'chunk_type in ["image", "title"]'

        search_top_k = 80 if parsed.brands_exclude and parsed.sub_categories else 30
        hits = store.search(query_vector=vec, top_k=search_top_k, filter_expr=filter_expr)
        from app.rag.retriever import _aggregate
        aggregate_top_n = max(args.top_n_products, 20) if parsed.brands_exclude else args.top_n_products
        products = _aggregate(hits, top_n_products=aggregate_top_n)
        if parsed.brands_exclude and parsed.sub_categories:
            from app.agent.multimodal_branch import (
                CHUNK_TYPE_FILTER,
                rerank_multimodal_products,
                select_source_hint_from_hits,
            )

            source_hits = store.search(
                query_vector=vec,
                top_k=10,
                filter_expr=CHUNK_TYPE_FILTER,
            )
            source_hint = select_source_hint_from_hits(source_hits, parsed)
            products = rerank_multimodal_products(
                products,
                parsed=parsed,
                source_hint=source_hint,
            )
        products = products[:args.top_n_products]
        predicted_pids = [p.product_id for p in products]
        record = {
            "case_id": c["case_id"],
            "predicted_top10": predicted_pids,
            "expected": c["expected_pids"],
            "top1": _topk_hit(predicted_pids, c["expected_pids"], 1),
            "top3": _topk_hit(predicted_pids, c["expected_pids"], 3),
            "top5": _topk_hit(predicted_pids, c["expected_pids"], 5),
        }
        by_type[c["type"]].append(record)
        logger.info(
            "  [%s][%s] top3=%s expected=%s",
            c["type"], c["case_id"], predicted_pids[:3], c["expected_pids"],
        )

    # 汇总
    report_lines: list[str] = ["# Phase 5 多模态图搜评测报告\n"]
    report_lines.append(
        f"> queries: `{args.queries}`，dataset: `ecommerce_agent_dataset`，"
        f"top_n_products={args.top_n_products}\n"
    )
    summary_rows: list[str] = ["| 类型 | n | Top-1 | Top-3 | Top-5 |", "| --- | --- | --- | --- | --- |"]
    for typ in ["same_item", "similar", "image_plus_price", "image_plus_brand_exclude"]:
        records = by_type.get(typ, [])
        n = len(records)
        if n == 0:
            summary_rows.append(f"| {typ} | 0 | - | - | - |")
            continue
        t1 = sum(r["top1"] for r in records) / n * 100
        t3 = sum(r["top3"] for r in records) / n * 100
        t5 = sum(r["top5"] for r in records) / n * 100
        summary_rows.append(f"| {typ} | {n} | {t1:.1f}% | {t3:.1f}% | {t5:.1f}% |")
    report_lines.append("\n## 汇总\n" + "\n".join(summary_rows) + "\n")

    # 详细
    report_lines.append("\n## 逐条详情\n")
    for typ in ["same_item", "similar", "image_plus_price", "image_plus_brand_exclude"]:
        records = by_type.get(typ, [])
        if not records:
            continue
        report_lines.append(f"### {typ}\n")
        for r in records:
            mark = "✅" if r["top1"] else ("🟡" if r["top3"] else "❌")
            report_lines.append(
                f"- {mark} `{r['case_id']}` — expected={r['expected']}, "
                f"top3={r['predicted_top10'][:3]}"
            )
        report_lines.append("")

    out = "\n".join(report_lines)
    if args.output:
        args.output.write_text(out, encoding="utf-8")
        logger.info("报告写入 %s", args.output)
    else:
        print(out)


if __name__ == "__main__":
    asyncio.run(main())
