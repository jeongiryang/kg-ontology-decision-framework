"""Verified bundle에서 PDF 출처 metadata만 읽어 캐시한다.

파싱은 프로세스당 한 번만 수행하고, 파일을 읽을 수 없으면 예외를 던지지 않고 빈
값을 돌려준다. 화면은 근거 원문과 페이지 번호만으로도 동작해야 하기 때문이다.

여기서 읽는 값은 모두 파생값이다. 원본은 `data/verified/<연도>/*_kg_data.json`이며
이 모듈은 그 값을 코드에 복사하지 않는다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BUNDLE_PATH = ROOT / "data/verified/2026/2026_curriculum_kg_data.json"


@lru_cache(maxsize=1)
def load_bundle(path: str | None = None) -> dict[str, Any]:
    """Verified bundle을 파싱해 캐시한다. 실패하면 빈 사전을 돌려준다."""
    target = Path(path) if path else BUNDLE_PATH
    try:
        result = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return result if isinstance(result, dict) else {}


def source_document(path: str | None = None) -> dict[str, Any]:
    """`metadata.source_document`를 돌려준다. 원본 PDF의 해시와 페이지 수가 여기 있다."""
    metadata = load_bundle(path).get("metadata")
    if not isinstance(metadata, dict):
        return {}
    document = metadata.get("source_document")
    return document if isinstance(document, dict) else {}
