# Phase 5 多模态图搜评测报告

> queries: `/Users/tongjiarui/Documents/基于RAG的多模态电商智能导购AI Agent/server/scripts/eval/image_queries.json`，dataset: `ecommerce_agent_dataset`，top_n_products=10


## 汇总
| 类型 | n | Top-1 | Top-3 | Top-5 |
| --- | --- | --- | --- | --- |
| same_item | 4 | 100.0% | 100.0% | 100.0% |
| similar | 5 | 100.0% | 100.0% | 100.0% |
| image_plus_price | 3 | 33.3% | 100.0% | 100.0% |
| image_plus_brand_exclude | 3 | 0.0% | 66.7% | 66.7% |


## 逐条详情

### same_item

- ✅ `same-1` — expected=['p_clothes_007'], top3=['p_clothes_007', 'p_clothes_008', 'p_clothes_009']
- ✅ `same-2` — expected=['p_beauty_002'], top3=['p_beauty_002', 'p_beauty_001', 'p_beauty_019']
- ✅ `same-3` — expected=['p_digital_001'], top3=['p_digital_001', 'p_digital_003', 'p_digital_014']
- ✅ `same-4` — expected=['p_food_001'], top3=['p_food_001', 'p_food_022', 'p_food_002']

### similar

- ✅ `similar-1` — expected=['p_clothes_007', 'p_clothes_008', 'p_clothes_009', 'p_clothes_010'], top3=['p_clothes_007', 'p_clothes_008', 'p_clothes_009']
- ✅ `similar-2` — expected=['p_beauty_002', 'p_beauty_001', 'p_beauty_004', 'p_beauty_005'], top3=['p_beauty_002', 'p_beauty_001', 'p_beauty_004']
- ✅ `similar-3` — expected=['p_digital_001', 'p_digital_008'], top3=['p_digital_001', 'p_digital_003', 'p_digital_014']
- ✅ `similar-4` — expected=['p_food_001', 'p_food_002'], top3=['p_food_002', 'p_food_023', 'p_food_011']
- ✅ `similar-5` — expected=['p_clothes_011', 'p_clothes_012'], top3=['p_clothes_011', 'p_clothes_013', 'p_clothes_012']

### image_plus_price

- ✅ `price-1` — expected=['p_clothes_007', 'p_clothes_010'], top3=['p_clothes_007', 'p_clothes_010', 'p_clothes_012']
- 🟡 `price-2` — expected=['p_beauty_010', 'p_beauty_006', 'p_beauty_011'], top3=['p_beauty_018', 'p_beauty_010', 'p_beauty_016']
- 🟡 `price-3` — expected=['p_beauty_004', 'p_beauty_005'], top3=['p_beauty_019', 'p_beauty_004', 'p_beauty_005']

### image_plus_brand_exclude

- 🟡 `brand-exclude-1` — expected=['p_clothes_008', 'p_clothes_009', 'p_clothes_010'], top3=['p_clothes_007', 'p_clothes_008', 'p_clothes_010']
- 🟡 `brand-exclude-2` — expected=['p_beauty_002', 'p_beauty_004', 'p_beauty_005'], top3=['p_beauty_001', 'p_beauty_002', 'p_beauty_004']
- ❌ `brand-exclude-3` — expected=['p_digital_008'], top3=['p_digital_001', 'p_digital_003', 'p_digital_009']
