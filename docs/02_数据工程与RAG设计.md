# 02 · 数据工程与 RAG 设计

> 配套主文档 `01_项目开发文档.md`。本篇专门讲：原始数据怎么变成可检索的向量、检索链路怎么搭、怎么防幻觉。

---

## 1. 原始数据分析

### 1.1 数据集结构

```
ecommerce_agent_dataset/
├── 1_美妆护肤/   (25 件)   data/p_beauty_*.json    images/p_beauty_*_live.jpg
├── 2_数码电子/   (25 件)   data/p_digital_*.json   images/p_digital_*_live.jpg
├── 3_服饰运动/   (25 件)   data/p_fashion_*.json   images/p_fashion_*_live.jpg
└── 4_食品生活/   (25 件)   data/p_food_*.json      images/p_food_*_live.jpg
```

共 **100 件商品**，每件 1 张直播现场图。

### 1.2 单个商品 JSON 字段

```jsonc
{
  "product_id": "p_beauty_001",          // 主键
  "title": "雅诗兰黛特润修护肌活精华露...",
  "brand": "雅诗兰黛",
  "category": "美妆护肤",
  "sub_category": "精华",
  "base_price": 720.0,                    // 主价（用于范围过滤）
  "image_path": "1_美妆护肤/images/p_beauty_001_live.jpg",
  "skus": [
    { "sku_id": "s_p_beauty_001_1", "properties": {"容量": "30ml 经典装"}, "price": 720.0 },
    ...
  ],
  "rag_knowledge": {
    "marketing_description": "...一段约 200 字的卖点描述...",
    "official_faq": [
      { "question": "...", "answer": "..." },
      ...     // 通常 3 条
    ],
    "user_reviews": [
      { "nickname": "...", "rating": 1-5, "content": "..." },
      ...     // 通常 5 条
    ]
  }
}
```

### 1.3 关键观察（直接影响 Chunking 策略）

- `marketing_description` 信息密度高，是**推荐理由**的主要来源。
- `official_faq` 每条 Q/A 都是独立语义单元，**特别适合**回答"这款怎么用？""敏感肌能用吗？"类追问。
- `user_reviews` 评分分布 1–5 都有，**正反兼具**，可用于"真实口碑"维度回答；但要注意低分评论不能被误用作推荐理由。
- 图片只有 1 张直播图，不是干净的产品白底图——多模态检索时需注意。

---

## 2. Chunking 策略

### 2.1 策略选型

**不采用**固定 token 切分（会把 FAQ 切碎），**采用按语义字段切分**：

| chunk_type | 来源字段 | 一条 chunk 内容 | 估计条数 |
| --- | --- | --- | --- |
| `description` | `marketing_description` | 全文（约 150–250 字） | 100 |
| `faq` | `official_faq[i]` | `"Q: ... A: ..."` 拼接 | ≈ 300 |
| `review` | `user_reviews[i]` | `"评分 X/5：..."` 拼接 | ≈ 500 |
| `title` | `title + brand + sub_category` | 拼成一行 | 100 |
| `image` | `image_path`（仅加分项 5B 启用） | 图像向量 | 100 |

**合计文本 chunk ≈ 1000 条**，对 100 件商品足够稠密。

### 2.2 为什么这么切

- 答辩话术："如果按 marketing_description 整段建一个向量，用户问'敏感肌能用吗'时召回的还是整段卖点，FAQ 的细节被稀释了。所以我们按字段单独切，FAQ 一条一向量，召回精度更高。"
- 副作用：同一商品可能在 Top-K 中出现多条 chunk → 后处理时**按 product_id 聚合去重**，每个商品最多保留 score 最高的 2 条片段塞给 LLM。

### 2.3 元数据 (metadata) 设计

每条 chunk 写入 Milvus 时**必带**以下 metadata（用于过滤）：

```python
{
  "product_id": "p_beauty_001",
  "chunk_type": "faq",            # description / faq / review / title / image
  "category": "美妆护肤",
  "sub_category": "精华",
  "brand": "雅诗兰黛",
  "base_price": 720.0,
  "min_sku_price": 720.0,
  "max_sku_price": 1260.0,
  "rating": 5,                    # 仅 review 类型有
  "source_id": "p_beauty_001#faq#1"  # 可追溯
}
```

> 重要：`base_price / min_sku_price / max_sku_price` 必须是数值类型，Milvus 才能做范围过滤。

---

## 3. Embedding 方案

### 3.1 模型选择

| 模态 | 模型 | 调用方式 |
| --- | --- | --- |
| 文本 | `doubao-embedding-text-240715`（或当前最新版本） | 火山方舟 OpenAI 兼容协议 |
| 图像 | `doubao-embedding-vision-241215` | 同一家方舟 SDK |

> **注意**：课题原文写明 Embedding **不提供 API Key**，需自行申请方舟账号。如果无法及时拿到 key，可临时降级到本地 `bge-large-zh-v1.5`（768 维），所有 chunk 用同一模型，否则向量空间不对齐。

### 3.2 调用代码骨架（仅示意，正式实现见 `server/app/rag/embedder.py`）

```python
# server/app/rag/embedder.py
from typing import List
from volcenginesdkarkruntime import Ark
from tenacity import retry, stop_after_attempt, wait_exponential

class DoubaoEmbedder:
    def __init__(self, api_key: str, model: str = "doubao-embedding-text-240715"):
        self.client = Ark(api_key=api_key)
        self.model = model
        self.dim = 2048  # 以官方文档为准，建表前确认

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        resp = self.client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]
```

### 3.3 维度与归一化

- 写入 Milvus 前**强制 L2 归一化**（`vec / np.linalg.norm(vec)`），用 `IP`（内积）等价于余弦相似度，省一次 normalize。
- 文本和图像向量**维度不同**，建两个 collection 或一个 collection + 分 partition：本项目用**两个 collection** 更简单：
  - `products_text` (dim = 文本维度)
  - `products_image` (dim = 图像维度)

---

## 4. Milvus Lite 表设计

### 4.1 安装与启动

```bash
pip install pymilvus  # 自带 Milvus Lite
# 嵌入式模式：传 db 文件路径即可，不需要独立服务
```

### 4.2 `products_text` Collection schema

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INT64, primary, auto_id=True | 自增 |
| `vector` | FLOAT_VECTOR(dim=2048) | 文本向量 |
| `product_id` | VARCHAR(64) | 商品 ID |
| `chunk_type` | VARCHAR(16) | description/faq/review/title |
| `text` | VARCHAR(2000) | 原文片段 |
| `category` | VARCHAR(32) | 一级类目 |
| `sub_category` | VARCHAR(32) | 二级类目 |
| `brand` | VARCHAR(64) | 品牌 |
| `base_price` | FLOAT | 主价 |
| `min_sku_price` | FLOAT | SKU 最低价 |
| `max_sku_price` | FLOAT | SKU 最高价 |
| `rating` | INT8 | 评论评分（其它 chunk_type 填 0） |
| `source_id` | VARCHAR(128) | 来源追溯 ID |

索引：`IVF_FLAT` (nlist=128) + metric `IP`；100 条数据级用 `FLAT` 直接暴搜也行。

### 4.3 写入流程（`scripts/build_index.py`）

```
读取 ecommerce_agent_dataset/*/data/*.json
  → 提取 chunk（按 §2.1 规则）
  → 批量调用 DoubaoEmbedder.embed_batch
  → 拼装 metadata
  → collection.insert(...)
  → collection.flush() + create_index() + load()
商品主表（product_id / title / brand / category / sub_category / base_price / skus / image_path）由 **独立的 `scripts/seed_mysql.py` 写入 MySQL**（`products` + `skus` 两张表，详见 03 篇 §6.1）；build_index.py 只关心向量索引，不重复写主表，两侧通过 `product_id` 关联。
```

> **幂等性**：脚本支持 `--rebuild` 参数，会先 drop collection 再重建，避免重复写入。

---

## 5. 检索链路

### 5.1 Query 处理三步走

```
用户原始 message
    │
    ▼
[1] Query Rewriter (LLM small call OR 规则)
    - 抽取过滤条件：price_max / price_min / category / brand_include / brand_exclude / scenario
    - 改写为"检索友好"的中文短句
    │
    ▼
[2] Hybrid Retrieve
    - Dense: vector search on products_text (filter by metadata)
    - Sparse (可选, 加分项): BM25 over chunk.text
    - 合并：Reciprocal Rank Fusion（RRF, k=60）
    │
    ▼
[3] Post-process
    - 按 product_id 聚合：每个商品保留 score 最高的 ≤ 2 条 chunk
    - 按 final score 取 Top-N (推荐 N=3~5)
    - 装配给 LLM 的"商品候选块"
```

### 5.2 Query Rewriter 提示词（核心）

```
你是电商查询解析器，把用户的中文消息解析为结构化字段，并改写为更检索友好的查询。
输出严格 JSON：
{
  "search_query": "...",       // 用于向量检索的短句
  "filters": {
    "category": "美妆护肤|数码电子|服饰运动|食品生活|null",
    "brand_include": ["..."],
    "brand_exclude": ["..."],
    "price_min": null|number,
    "price_max": null|number,
    "scenario": "..."           // 如 "三亚度假" "夜跑" "敏感肌"
  },
  "intent": "recommend|compare|cart_op|clarify_needed|chitchat"
}
只输出 JSON，不要解释。
```

> 这一步用 Doubao-lite + `response_format={"type":"json_object"}` 强制 JSON 输出，约 200 ms。

### 5.3 元数据过滤示例

```python
# 用户说"200 元以下的蓝牙耳机"
filter_expr = "category == '数码电子' && sub_category == '蓝牙耳机' && min_sku_price <= 200.0"
results = collection.search(
    data=[query_vec],
    anns_field="vector",
    param={"metric_type": "IP", "params": {"nprobe": 16}},
    limit=20,
    expr=filter_expr,
    output_fields=["product_id", "chunk_type", "text", "base_price", "brand"]
)
```

### 5.4 反选 / 排除语义

Query Rewriter 把"不要含酒精""排除日系品牌"解析为：
```json
{"brand_exclude": ["资生堂", "SK-II", "DHC"], "must_not_keywords": ["酒精", "乙醇"]}
```
在检索后**用 Python 后过滤**：剔除 chunk.text 命中关键词、剔除 brand 在 brand_exclude 列表中的商品。

> **不要**直接把 must_not 关键词扔进向量 query，向量模型不擅长否定语义，必须用规则后过滤。

---

## 6. 给 LLM 的上下文格式

### 6.1 检索结果 → Prompt 模板

```
你是「派派购」电商导购助手。严格遵守：
1. 只能基于下方 <retrieved_products> 中出现的商品作答。
2. 任何 product_id、价格、SKU 名称、品牌必须原样引用，禁止编造。
3. 推荐 1~3 件最匹配的商品，给出推荐理由（结合用户需求 + 检索片段事实）。
4. 商品卡片信息必须放在最后，用 JSON code block 包裹，格式：
   ```product_cards
   [{"product_id":"...", "reason":"30 字内推荐理由"}]
   ```
5. 当用户问及未检索到的商品时，必须诚实回答"抱歉，库内暂未找到匹配的商品，您可以换个关键词或上传图片找同款"。

<user_intent>
{intent_json}
</user_intent>

<retrieved_products>
[
  {
    "product_id": "p_beauty_001",
    "title": "雅诗兰黛特润修护肌活精华露 30ml",
    "brand": "雅诗兰黛",
    "category": "美妆护肤 / 精华",
    "price_range": "720~1260 元 (3 个 SKU)",
    "highlights": [
       {"type":"description","text":"...一段卖点..."},
       {"type":"faq","text":"Q: 敏感肌可以用吗？ A: ..."}
    ]
  },
  ...（最多 5 件）
]
</retrieved_products>

历史对话（最近 6 轮）：
{conversation_history}

当前用户消息：
{user_message}
```

### 6.2 输出后处理

后端在 SSE 流式输出时**实时解析**：
- 检测到 `\`\`\`product_cards` 之后的 JSON → 解析每个 `product_id`
- 用 MySQL `products` 表（异步 AsyncSession + 单条 SELECT）校验存在性 → **不存在的直接丢弃**
- 命中的商品查出完整信息（标题、主图、价格区间、SKU 列表）→ 包装成 `event: product_card` 推给客户端
- 文本流中**剥离** `product_cards` 这段 JSON，不要让用户看到原始代码块

---

## 7. 防幻觉的多层保险

| 层 | 做法 | 失效后的兜底 |
| --- | --- | --- |
| 1. 系统 Prompt | 强约束 + 示例（few-shot） | — |
| 2. 检索覆盖 | 命中阈值不足时回固定话术 | "抱歉，没找到合适商品…" |
| 3. JSON 强校验 | 后端校验 product_id 真实存在 | 丢弃伪造卡片 |
| 4. 价格字段 | 卡片价格**只从 MySQL `products` + `skus` 取**，不从 LLM 输出取 | — |
| 5. 评测脚本 | 每次发版前跑 30 条 query 回归 | 抓出新引入的幻觉回退 |

---

## 8. 多模态检索（加分项 5B）

### 8.1 图像入库

```python
# scripts/build_index.py 末尾追加
for product in all_products:
    img = Image.open(product["image_path"]).convert("RGB")
    vec = vlm_embedder.embed_image(img)   # Doubao-embedding-vision
    products_image_collection.insert([{
        "vector": vec / np.linalg.norm(vec),
        "product_id": product["product_id"],
        "category": product["category"],
    }])
```

### 8.2 用户拍照流程

```
iOS 拍照/选图 → 压缩到 ≤1MB → multipart 上传 /upload/image
    → 后端用 doubao-embedding-vision 编码
    → products_image 向量检索 Top-K
    → 拿到 product_id 列表 → 回到正常文本 RAG：把这些 product_id 作为"必检索集"
       塞进 Prompt 的 <retrieved_products>
    → LLM 生成"同款推荐"话术 + 商品卡片
```

> 用户上传图**不持久化**，处理完即释放。

---

## 9. 评测

### 9.1 准备评测集（`server/scripts/eval_recall.py`）

手工写 **20–30 条 query**，标注预期 `product_id`（黄金答案 1–3 件），覆盖：

- 单类目模糊推荐（"推荐一款适合油皮的洗面奶"→ `p_beauty_***`）
- 价格范围（"200 元以下的蓝牙耳机"）
- 品牌排除（"不要含酒精的防晒"）
- 场景化（"夜跑装备"）
- 同款找货（图片输入）

### 9.2 指标

- **Top-K Recall**：黄金 product_id 命中前 K 的比例。目标 Top-5 ≥ 0.8。
- **首 token 延迟**：从 `/chat/stream` 收到请求到第一个 `data:` 的时间，目标 < 1.5 s。
- **整体响应时长**：到 `event: done` 的时间，目标 < 6 s。

---

## 10. 常见坑（开发期一定会踩）

1. **Milvus VARCHAR 长度不够**：marketing_description 可能 > 256 字，定义时给到 2000。
2. **embedding 维度搞错**：建 collection 前一定先 `len(embedder.embed_batch(["test"])[0])` 打印确认。
3. **批量 embedding 限流**：方舟 RPM 700，批量调用建议每批 ≤ 16 条，加 0.05 s 间隔。
4. **filter expr 语法**：Milvus 用 `&&` `||` 不是 `and / or`；字符串值要用单引号包。
5. **中文向量召回头部偏向标题**：可在 chunker 里给 description chunk 加权（前缀拼一遍 title），提升类目召回。
6. **图片直接 ndarray 入 embed**：Doubao-vision 需要 base64 编码后传 url 字段，注意 SDK 文档。

---

至此数据 + RAG 链路设计完成。下一篇 `03_后端API与Agent编排.md` 讲后端怎么把这套检索能力包装成 FastAPI + SSE + Agent。
