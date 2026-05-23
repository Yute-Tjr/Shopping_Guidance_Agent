# Phase 1 检索召回评测

- Embedding 模型：`ep-20260523004654-hx4sm`（dim=2048）
- 黄金集：25 条 query
- 检索引擎：Milvus Lite (FLAT / IP)，每条 query 取 Top-50 chunk 再按 product_id 去重
- 总耗时：3.7s

## 总体 Top-K Recall

| 指标 | Top-1 | Top-3 | Top-5 | Top-10 |
| --- | --- | --- | --- | --- |
| Recall | 80.00% | 100.00% | 100.00% | 100.00% |

## 按意图分组

| 意图 | 条数 | Top-1 | Top-3 | Top-5 | Top-10 |
| --- | --- | --- | --- | --- | --- |
| brand_exclude | 4 | 0.00% | 100.00% | 100.00% | 100.00% |
| category_recommend | 8 | 87.50% | 100.00% | 100.00% | 100.00% |
| price_filter | 5 | 100.00% | 100.00% | 100.00% | 100.00% |
| scenario | 8 | 100.00% | 100.00% | 100.00% | 100.00% |

## 逐条明细

| ID | 意图 | Query | 黄金 | Top-5 命中 | Top-1 商品 |
| --- | --- | --- | --- | --- | --- |
| q01 | category_recommend | 推荐一款抗初老精华 | p_beauty_001, p_beauty_009, p_beauty_024 | ✅ | p_beauty_001 |
| q02 | category_recommend | 高倍防晒霜推荐 | p_beauty_006, p_beauty_010, p_beauty_023 | ✅ | p_beauty_023 |
| q03 | category_recommend | 敏感肌可以用的修护面霜 | p_beauty_007, p_beauty_012 | ✅ | p_beauty_012 |
| q04 | category_recommend | 适合早上喝的速溶咖啡 | p_food_001, p_food_002, p_food_022 | ✅ | p_food_023 |
| q05 | category_recommend | 想买 256G 的苹果手机 | p_digital_001, p_digital_003 | ✅ | p_digital_001 |
| q06 | category_recommend | 推荐一双跑步鞋 | p_clothes_007, p_clothes_008, p_clothes_009 | ✅ | p_clothes_008 |
| q07 | category_recommend | 无糖饮料有什么推荐 | p_food_003, p_food_004, p_food_015 | ✅ | p_food_015 |
| q08 | category_recommend | 便宜实惠的洁面乳 | p_beauty_011 | ✅ | p_beauty_011 |
| q09 | price_filter | 300元以下的防晒霜 | p_beauty_006, p_beauty_023, p_beauty_010 | ✅ | p_beauty_023 |
| q10 | price_filter | 2000元以下的真无线耳机 | p_digital_007, p_digital_018 | ✅ | p_digital_007 |
| q11 | price_filter | 7000元以下的轻薄笔记本 | p_digital_004, p_digital_023 | ✅ | p_digital_023 |
| q12 | price_filter | 200元以内的运动T恤 | p_clothes_001, p_clothes_003, p_clothes_020 | ✅ | p_clothes_003 |
| q13 | price_filter | 100元以内的休闲零食 | p_food_009, p_food_010 | ✅ | p_food_010 |
| q14 | brand_exclude | 非 Apple 品牌的轻薄笔记本 | p_digital_004, p_digital_022, p_digital_023 | ✅ | p_digital_020 |
| q15 | brand_exclude | 不是耐克的专业跑鞋 | p_clothes_008, p_clothes_009, p_clothes_010 | ✅ | p_clothes_007 |
| q16 | brand_exclude | 国产旗舰手机推荐 | p_digital_002, p_digital_008, p_digital_014 | ✅ | p_digital_017 |
| q17 | brand_exclude | 不要可口可乐，有别的碳酸饮料吗 | p_food_004, p_food_024 | ✅ | p_food_015 |
| q18 | scenario | 夜跑装备，要轻便透气 | p_clothes_020, p_clothes_021, p_clothes_007 | ✅ | p_clothes_020 |
| q19 | scenario | 适合户外徒步的鞋 | p_clothes_014, p_clothes_015 | ✅ | p_clothes_015 |
| q20 | scenario | 打篮球穿的实战篮球鞋 | p_clothes_011, p_clothes_012, p_clothes_013 | ✅ | p_clothes_013 |
| q21 | scenario | 熬夜后修护肌肤的夜间精华 | p_beauty_001, p_beauty_004 | ✅ | p_beauty_001 |
| q22 | scenario | 夏天补充能量的运动饮料 | p_food_005, p_food_006 | ✅ | p_food_005 |
| q23 | scenario | 适合日常通勤的双肩背包 | p_clothes_018, p_clothes_025 | ✅ | p_clothes_025 |
| q24 | scenario | 送女朋友的彩妆礼物 | p_beauty_014, p_beauty_015, p_beauty_025 | ✅ | p_beauty_015 |
| q25 | scenario | 瑜伽穿的高腰紧身裤 | p_clothes_016 | ✅ | p_clothes_016 |
