"""FastAPI 라우트 ("POST /query" 제출 규약).

HTTP 요청/응답만 다루는 얇은 계층이다. 에이전트 판단(그래프·ReAct·가드레일)은
src/agent.py, 데이터 저장·집계는 src/db.py에 있고 여기서는 그 둘을 조합만 한다
(CLAUDE.md 코드 규칙: 파일 하나에 한 가지 역할만).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel

from src.agent import REACT_AGENT, _ainvoke_with_retry, _pending_approvals, _pending_reasons
from src.db import get_stars_summary, get_wrong_answers, init_db, log_grade, weekly_report
from src.retriever import retrieve_reference
from src.tools import (
    EnglishProblem,
    KoreanProblem,
    MathProblem,
    generate_english_hint,
    generate_hint,
    generate_korean_hint,
    grade_answer,
    grade_english_answer,
    grade_korean_answer,
)

app = FastAPI()


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str
    contexts: list[str]
    trace: list[str]
    used_fallback: bool
    approval_mode: str | None = None
    problems: list[dict] = []
    attempt_id: str | None = None


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    contexts = retrieve_reference(req.question)
    agent_result = await _ainvoke_with_retry(REACT_AGENT, {"messages": [{"role": "user", "content": req.question}]})

    messages = agent_result["messages"]
    trace: list[str] = []
    used_fallback = False
    approval_mode: str | None = None
    problems: list[dict] = []
    attempt_id: str | None = None
    for message in messages:
        if isinstance(message, AIMessage):
            for tool_call in message.tool_calls:
                trace.append(f"{tool_call['name']}({tool_call['args']})")
        if isinstance(message, ToolMessage) and message.name in (
            "generate_worksheet",
            "generate_english_worksheet",
            "generate_korean_worksheet",
        ):
            try:
                payload = json.loads(message.content)
            except (json.JSONDecodeError, TypeError):
                continue
            used_fallback = payload.get("used_fallback", used_fallback)
            approval_mode = payload.get("approval_mode", approval_mode)
            problems = payload.get("problems", problems)
            attempt_id = payload.get("attempt_id", attempt_id)

    answer = messages[-1].content
    return QueryResponse(
        answer=answer,
        contexts=contexts,
        trace=trace,
        used_fallback=used_fallback,
        approval_mode=approval_mode,
        problems=problems,
        attempt_id=attempt_id,
    )


@app.get("/pending-approvals")
def pending_approvals() -> dict:
    """부모용 화면에서 지금 승인 대기 중인 요청을 보여줄 때 쓴다."""
    return _pending_reasons


@app.get("/report/weekly")
def report_weekly(weeks_ago: int = 0) -> dict:
    """부모 화면의 주간 리포트 (최근 7일 요약). weeks_ago로 지난 주들을 선택할 수 있다."""
    return weekly_report(weeks_ago=weeks_ago)


@app.get("/stars")
def stars() -> dict:
    """아이 화면 상단바(오늘 딴 별 수·누적 별 수) 초기 로딩용 (SERVICE.md 4번)."""
    return get_stars_summary()


class ApprovalRequest(BaseModel):
    thread_id: str


@app.post("/approve")
def approve(req: ApprovalRequest) -> dict:
    """부모가 문제은행 대체를 명시적으로 승인한다 (SERVICE.md 4번 HITL)."""
    event = _pending_approvals.get(req.thread_id)
    if event is None:
        return {"ok": False, "reason": "승인 대기 중인 요청이 없습니다 (이미 처리됐거나 잘못된 thread_id)."}
    event.set()
    return {"ok": True}


class GradeRequest(BaseModel):
    question: str
    correct_answer: str
    child_answer: str
    used_hint: bool = False
    attempt_id: str | None = None
    problem_index: int | None = None
    topic: str = ""
    subject: str = "math"
    choices: list[str] = []


@app.post("/grade")
def grade(req: GradeRequest) -> dict:
    """아이가 제출한 답을 채점한다 (핵심 루프의 마지막 단계, 과목별로 분기)."""
    if req.subject == "english":
        problem = EnglishProblem(question=req.question, choices=req.choices, answer=req.correct_answer)
        result = grade_english_answer(problem, req.child_answer, req.used_hint)
    elif req.subject == "korean":
        problem = KoreanProblem(question=req.question, choices=req.choices, answer=req.correct_answer)
        result = grade_korean_answer(problem, req.child_answer, req.used_hint)
    else:
        problem = MathProblem(question=req.question, answer=req.correct_answer)
        result = grade_answer(problem, req.child_answer, req.used_hint)
    result["stars_earned"] = log_grade(
        req.attempt_id,
        req.problem_index,
        req.question,
        req.correct_answer,
        req.child_answer,
        result["is_correct"],
        result["score"],
        req.used_hint,
        req.topic,
        req.subject,
        req.choices,
    )
    result.update(get_stars_summary())
    return result


@app.get("/wrong-answers")
def wrong_answers(subject: str = "math") -> dict:
    """부모 화면의 오답노트 (SERVICE.md 3·4번, 과목별 최신순 최대 50개)."""
    return {"items": get_wrong_answers(subject)}


class HintRequest(BaseModel):
    question: str
    correct_answer: str
    grade: int = 3
    subject: str = "math"
    choices: list[str] = []


@app.post("/hint")
def hint(req: HintRequest) -> dict:
    """정답은 알려주지 않고 풀이 방향을 알려준다 (SERVICE.md 4번 힌트 정책, 과목별로 분기)."""
    if req.subject == "english":
        problem = EnglishProblem(question=req.question, choices=req.choices, answer=req.correct_answer)
        return {"hint": generate_english_hint(problem, req.grade)}
    if req.subject == "korean":
        problem = KoreanProblem(question=req.question, choices=req.choices, answer=req.correct_answer)
        return {"hint": generate_korean_hint(problem, req.grade)}
    problem = MathProblem(question=req.question, answer=req.correct_answer)
    return {"hint": generate_hint(problem, req.grade)}


# 프론트엔드 정적 파일 (web/) — API 라우트보다 뒤에 등록해야 /query 등과 겹치지 않는다
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if _WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
