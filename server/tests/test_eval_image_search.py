from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import eval_image_search


class _RepoRaising:
    async def list_brands(self):
        raise RuntimeError("db unavailable")


@pytest.mark.asyncio
async def test_eval_image_search_loads_brands_from_dataset_when_repo_unavailable(tmp_path: Path):
    data_dir = tmp_path / "ecommerce_agent_dataset" / "1_美妆护肤" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "p_beauty_001.json").write_text(
        json.dumps({"brand": "雅诗兰黛"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "p_beauty_002.json").write_text(
        json.dumps({"brand": "Apple 苹果"}, ensure_ascii=False),
        encoding="utf-8",
    )

    brands = await eval_image_search._load_known_brands(
        tmp_path,
        repo_factory=lambda: _RepoRaising(),
    )

    assert brands == ["Apple 苹果", "雅诗兰黛"]
