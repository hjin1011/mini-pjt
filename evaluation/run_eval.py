"""자동 평가 실행기.

evaluation/test_queries.csv의 인-아웃 세트를 실제 REACT_AGENT(agent.py)에 자연어
그대로 입력해 실행하고 결과를 자동 채점한다. round1·round2까지는 사람이 직접 실행하고
직접 판단했지만(가장 최근 감사에서 "돌릴 때마다 임시 스크립트를 즉석에서 작성함"으로
지적됨), 이 스크립트는 재실행 가능한 고정 절차로 같은 일을 한다.

채점 방식 (하이브리드):
- expected_tools(도구 이름)는 실제 호출된 tool_call 이름과 코드로 비교한다 (결정론적, 비용 없음).
- expected_traits·forbidden(의미 판단)은 별도의 독립 LLM 호출(judge_answer)로 채점한다.
  cross_validate_problem이 문제 생성과 별개의 호출로 정오답을 검증하는 것과 같은 패턴이다.

반복 실행 (LLM 비결정성 대비): generate_worksheet·generate_english_worksheet·generate_korean_worksheet를
유발하는 케이스는 내부적으로 문제 생성(1회)+교차검증(문제당 1회, 최대 10회)까지
연쇄 호출돼 케이스당 비용이 훨씬 크다. 이 파이프라인 자체의 동작은 이미
round1·round2와 실제 스모크 테스트로 검증되어 있으므로 1회만 실행하고, 나머지
(가벼운) 케이스만 3회 반복해 통과율로 보고한다.

실행 (프로젝트 루트에서): python evaluation/run_eval.py
"""

from __future__ import annotations

import asyncio
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_aws import ChatBedrockConverse  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from src.agent import REACT_AGENT, _ainvoke_with_retry  # noqa: E402
from src.tools import MODEL_FALLBACK_CHAIN, invoke_with_retry  # noqa: E402

CSV_PATH = Path(__file__).resolve().parent / "test_queries.csv"
REPORT_DIR = Path(__file__).resolve().parent

# generate_worksheet를 유발하는 케이스는 케이스당 최대 ~12회 Bedrock 호출로 이어져
# 훨씬 비싸다 (파이프라인 자체 검증은 이미 끝났으므로 1회만). 나머지 가벼운 케이스는
# LLM 비결정성을 반복 실행으로 확인한다.
EXPENSIVE_TOOLS = {"generate_worksheet", "generate_english_worksheet", "generate_korean_worksheet"}
CHEAP_REPEATS = 3
EXPENSIVE_REPEATS = 1


class JudgeResult(BaseModel):
    """LLM 판정관이 채점한 expected_traits·forbidden 충족 여부."""

    satisfied_traits: list[str] = Field(
        description="expected_traits 중 답변이 실제로 만족한 항목 (입력받은 원문 그대로 적을 것)"
    )
    violated_forbidden: list[str] = Field(
        description="forbidden 중 답변에 실제로 나타난 항목 (입력받은 원문 그대로, 없으면 빈 리스트)"
    )
    reasoning: str = Field(description="판단 근거를 한두 문장으로")


def _judge_model(model_id: str | None = None) -> ChatBedrockConverse:
    return ChatBedrockConverse(model=model_id or MODEL_FALLBACK_CHAIN[0], temperature=0)


def judge_answer(question: str, answer: str, expected_traits: list[str], forbidden: list[str]) -> JudgeResult:
    """에이전트의 실제 답변이 expected_traits·forbidden 조건을 만족하는지 독립 LLM 호출로 판정한다.

    판정관 모델이 스로틀링되면 invoke_with_retry가 MODEL_FALLBACK_CHAIN의 다른 모델로
    바로 교체해 재시도한다 (src.tools와 동일 정책).
    """
    prompt = (
        "다음은 초등학생용 학습지 생성 Agent에게 던진 질문과 그 실제 답변이다. "
        "answer가 아래 expected_traits 각 항목을 실제로 만족하는지, forbidden 각 항목이 "
        "answer에 실제로 나타나는지 하나씩 판정해라. 표현이 정확히 같을 필요는 없고 "
        "의미가 통하면 만족한 것으로 본다.\n\n"
        f"질문: {question}\n답변: {answer}\n\n"
        f"확인할 expected_traits: {expected_traits}\n"
        f"확인할 forbidden: {forbidden}\n"
    )
    return invoke_with_retry(
        lambda model_id: _judge_model(model_id).with_structured_output(JudgeResult), prompt
    )


def parse_semicolon(value: str | None) -> list[str]:
    """CSV의 세미콜론 구분 컬럼(expected_traits/forbidden/expected_tools)을 리스트로 나눈다."""
    return [v.strip() for v in value.split(";") if v.strip()] if value else []


def extract_tool_names(trace: list[str]) -> set[str]:
    return {entry.split("(", 1)[0] for entry in trace}


async def run_case_once(question: str) -> tuple[list[str], str]:
    """REACT_AGENT를 한 번 호출해 (trace, 최종 답변)을 반환한다.

    agent.py의 POST /query 라우트와 동일한 방식(같은 재시도 래퍼, 같은 answer 추출
    방식)으로 실행해 실제 서비스 동작을 그대로 재현한다.
    """
    agent_result = await _ainvoke_with_retry(REACT_AGENT, {"messages": [{"role": "user", "content": question}]})
    messages = agent_result["messages"]
    trace: list[str] = []
    for message in messages:
        if isinstance(message, AIMessage):
            for tool_call in message.tool_calls:
                trace.append(f"{tool_call['name']}({tool_call['args']})")
    answer = messages[-1].content
    return trace, answer if isinstance(answer, str) else str(answer)


async def run_case(row: dict) -> dict:
    expected_tools = parse_semicolon(row.get("expected_tools"))
    expected_traits = parse_semicolon(row.get("expected_traits"))
    forbidden = parse_semicolon(row.get("forbidden"))
    is_expensive = any(t in EXPENSIVE_TOOLS for t in expected_tools)
    repeats = EXPENSIVE_REPEATS if is_expensive else CHEAP_REPEATS

    runs = []
    for _ in range(repeats):
        trace, answer = await run_case_once(row["input"])
        tool_ok = set(expected_tools).issubset(extract_tool_names(trace)) if expected_tools else True

        judge: JudgeResult | None = None
        judge_ok = True
        if expected_traits or forbidden:
            judge = judge_answer(row["input"], answer, expected_traits, forbidden)
            judge_ok = set(judge.satisfied_traits) >= set(expected_traits) and not judge.violated_forbidden

        runs.append(
            {
                "passed": tool_ok and judge_ok,
                "tool_ok": tool_ok,
                "judge_ok": judge_ok,
                "trace": trace,
                "answer": answer,
                "judge": judge,
            }
        )

    passes = sum(1 for r in runs if r["passed"])
    return {
        "id": row["id"],
        "category": row["category"],
        "input": row["input"],
        "expected_tools": expected_tools,
        "expected_traits": expected_traits,
        "forbidden": forbidden,
        "note": row.get("note", ""),
        "repeats": repeats,
        "passes": passes,
        "runs": runs,
    }


def _next_report_path() -> Path:
    pattern = re.compile(r"round(\d+)_report\.md$")
    nums = [int(m.group(1)) for p in REPORT_DIR.glob("round*_report.md") if (m := pattern.search(p.name))]
    next_round = max(nums, default=0) + 1
    return REPORT_DIR / f"round{next_round}_report.md"


def write_report(results: list[dict]) -> Path:
    total_cases = len(results)
    fully_passed = sum(1 for r in results if r["passes"] == r["repeats"])

    lines = [
        f"# 자동 평가 결과 (run_eval.py)",
        "",
        "`evaluation/run_eval.py`로 자동 실행했다. `expected_tools`는 코드로, "
        "`expected_traits`·`forbidden`은 독립 LLM 판정관 호출로 채점했다. "
        "`generate_worksheet`를 유발하는 케이스는 1회만, 나머지는 LLM 비결정성 확인을 "
        f"위해 {CHEAP_REPEATS}회 반복 실행해 통과율로 표시한다.",
        "",
        f"## 결과: {fully_passed} / {total_cases} 케이스 전 반복 통과",
        "",
        "| id | category | 반복 통과율 | 상태 | note |",
        "|----|----------|------------|------|------|",
    ]
    for r in results:
        status = "✅" if r["passes"] == r["repeats"] else ("⚠️" if r["passes"] > 0 else "❌")
        lines.append(
            f"| {r['id']} | {r['category']} | {r['passes']}/{r['repeats']} | {status} | {r['note']} |"
        )

    failures = [r for r in results if r["passes"] < r["repeats"]]
    if failures:
        lines += ["", "## 실패/부분 실패 상세", ""]
        for r in failures:
            lines.append(f"### id {r['id']} ({r['category']}) — {r['passes']}/{r['repeats']} 통과")
            lines.append(f"- 입력: {r['input']}")
            for i, run in enumerate(r["runs"], start=1):
                if run["passed"]:
                    continue
                lines.append(f"- {i}번째 실행 실패: tool_ok={run['tool_ok']}, judge_ok={run['judge_ok']}")
                lines.append(f"  - trace: {run['trace']}")
                lines.append(f"  - answer: {run['answer'][:200]}")
                if run["judge"]:
                    lines.append(
                        f"  - judge: satisfied={run['judge'].satisfied_traits}, "
                        f"violated_forbidden={run['judge'].violated_forbidden}, "
                        f"reasoning={run['judge'].reasoning}"
                    )
            lines.append("")
    else:
        lines += ["", "## 실패/부분 실패 상세", "", "없음 — 모든 케이스가 모든 반복에서 통과했다."]

    report_path = _next_report_path()
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


async def main() -> None:
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    results = []
    for row in rows:
        print(f"[{row['id']}] {row['category']}: {row['input'][:40]}", flush=True)
        result = await run_case(row)
        results.append(result)
        print(f"  -> {result['passes']}/{result['repeats']}", flush=True)

    report_path = write_report(results)
    print(f"\n리포트 작성 완료: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
