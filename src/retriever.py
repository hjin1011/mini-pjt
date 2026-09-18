"""RAG 파이프라인.

SERVICE.md 3번 정책: 수학은 무료 MCP/API 제공처가 없으면 AI가 학교 교과과정
기준으로 자체 생성한다. 1차 범위(수학)는 검색 없이 자체 생성으로 충분해
아직 실제 검색 로직은 없다. 한자 급수표(한국어문회)·영어 확장 단계에서
이 파일에 실제 검색기를 추가한다.
"""

from __future__ import annotations


def retrieve_reference(query: str) -> list[str]:
    """참고자료를 검색한다. 1차(수학)에서는 항상 빈 리스트를 반환한다."""
    return []
