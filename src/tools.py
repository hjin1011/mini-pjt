"""도메인 도구: 문제 생성, 오류 교차검증, 채점.

SERVICE.md 3~4번 정책을 코드로 옮긴다.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable, Literal

from botocore.exceptions import ClientError
from langchain_aws import ChatBedrockConverse
from pydantic import BaseModel, Field

Difficulty = Literal["쉬움", "표준", "어려움"]

# Bedrock 쪽 일시적 과부하/스로틀링만 재시도 대상으로 삼는다 (SERVICE.md엔 없지만
# 실서비스 안정성을 위한 인프라 처리 — 잘못된 입력 등 영구적 오류는 재시도하지 않는다).
_RETRYABLE_BEDROCK_CODES = {
    "ThrottlingException",
    "ServiceUnavailableException",
    "ModelTimeoutException",
    "ModelNotReadyException",
}

# 모델 하나가 스로틀링(계정 일일 토큰 한도 포함)에 걸려도 서비스가 멈추지 않도록,
# 실제 계정에서 쓸 수 있는 모델을 순서대로 나열한다 (2026-09-17 확인). 같은 모델의
# us./global. 리전 프로파일은 별도 한도로 취급되는 것으로 보여 둘 다 넣었고,
# Anthropic 계열을 우선하고 Nova 계열은 구조화 출력 검증 이력이 적어 마지막 안전망으로 둔다.
MODEL_FALLBACK_CHAIN = [
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.anthropic.claude-sonnet-4-6",
    "global.anthropic.claude-sonnet-4-6",
    "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.amazon.nova-pro-v1:0",
    "us.amazon.nova-2-lite-v1:0",
    "global.amazon.nova-2-lite-v1:0",
    "us.amazon.nova-lite-v1:0",
]


def is_retryable_bedrock_error(exc: Exception) -> bool:
    """Bedrock 호출 오류 중 재시도해볼 만한 일시적 오류인지 판정한다."""
    if not isinstance(exc, ClientError):
        return False
    return exc.response.get("Error", {}).get("Code") in _RETRYABLE_BEDROCK_CODES


def invoke_with_retry(make_chain: Callable[[str], Any], prompt: str) -> Any:
    """모델 하나가 스로틀링 등으로 막히면 기다리지 않고 바로 다음 모델로 교체해가며
    MODEL_FALLBACK_CHAIN을 순서대로 시도한다.

    make_chain(model_id)는 그 model_id로 바인딩된 체인(구조화 출력 포함)을 반환해야
    한다 — 모델마다 새로 체인을 만들어야 하므로 완성된 chain 객체 대신 팩토리 함수를
    받는다. 재시도 불가능한 오류(예: ValidationException)는 그 자리에서 바로 올리고,
    체인의 모델을 전부 시도해도 실패하면(계정 전체 일일 토큰 한도 등) 마지막 오류를 올린다.
    """
    last_exc: Exception | None = None
    for model_id in MODEL_FALLBACK_CHAIN:
        try:
            return make_chain(model_id).invoke(prompt)
        except Exception as exc:
            if not is_retryable_bedrock_error(exc):
                raise
            last_exc = exc
    raise last_exc


class MathProblem(BaseModel):
    """수학 문제 하나."""

    question: str = Field(description="문제 본문")
    answer: str = Field(description="정답 (숫자 또는 짧은 단답)")
    topic: str = Field(
        default="",
        description="이 문제가 다루는 아주 짧은 주제 (예: '10 이하의 덧셈', '두 자리 수 뺄셈(받아내림)', '직사각형의 넓이'). 부모 주간 리포트의 오답 주제 표시에 쓰인다.",
    )


class ProblemSet(BaseModel):
    """하루 분량 문제 묶음."""

    problems: list[MathProblem] = Field(description="생성된 문제 목록")


class ValidationResult(BaseModel):
    """오류 교차검증 결과 (SERVICE.md 4번: 정오답 오류·오타/표기 오류 판정)."""

    is_valid: bool = Field(description="문제·정답에 오류가 없으면 True")
    reason: str = Field(description="오류가 있다면 그 이유, 없으면 빈 문자열")


def _model(temperature: float = 0.7, model_id: str | None = None) -> ChatBedrockConverse:
    return ChatBedrockConverse(
        model=model_id or MODEL_FALLBACK_CHAIN[0],
        temperature=temperature,
    )


def generate_math_problems(grade: int, difficulty: Difficulty, count: int) -> list[MathProblem]:
    """학년·난이도에 맞는 수학 문제를 생성한다 (SERVICE.md 3번: 학교 교과과정 기준 자체 생성)."""
    prompt = (
        f"초등학교 {grade}학년 수준의 수학 문제를 {count}개 만들어줘. "
        f"난이도는 '{difficulty}'로 맞추고, 객관식이나 단답형으로만 낸다 "
        f"(서술형 제외). 각 문제는 정답이 명확한 숫자나 짧은 단어여야 한다. "
        f"문제마다 topic 필드에 그 문제가 다루는 아주 짧은 주제를 붙여라 "
        f"(예: '10 이하의 덧셈', '두 자리 수 뺄셈(받아내림)', '직사각형의 넓이', '시계 읽기')."
    )
    result: ProblemSet = invoke_with_retry(
        lambda model_id: _model(temperature=0.7, model_id=model_id).with_structured_output(ProblemSet), prompt
    )
    return result.problems


class Hint(BaseModel):
    """정답을 직접 알려주지 않는 풀이 힌트."""

    hint: str = Field(description="정답 숫자를 포함하지 않는, 풀이 방향을 알려주는 짧은 힌트 (1~2문장)")


def generate_hint(problem: MathProblem, grade: int) -> str:
    """정답을 바로 알려주지 않고, 어떻게 풀면 되는지 방향을 알려준다 (SERVICE.md 4번 힌트 정책).

    모델이 실수로 정답 문자열을 그대로 포함하면 한 번 더 요청해 걸러낸다.
    """
    prompt = (
        f"초등학교 {grade}학년 아이가 다음 수학 문제를 풀다가 막혔다. "
        "정답은 절대 알려주지 말고, 어떤 순서로 계산하면 되는지 또는 어떤 개념을 "
        "떠올리면 되는지 짧고 다정한 힌트를 1~2문장으로 줘라.\n\n"
        f"문제: {problem.question}\n(참고용, 아이에게 보이면 안 되는 정답: {problem.answer})"
    )
    for _ in range(2):
        result: Hint = invoke_with_retry(
            lambda model_id: _model(temperature=0.3, model_id=model_id).with_structured_output(Hint), prompt
        )
        if _normalize_answer(problem.answer) not in _normalize_answer(result.hint):
            return result.hint
    return "차근차근 순서대로 계산해보자. 문제를 다시 한 번 천천히 읽어볼까?"


def cross_validate_problem(problem: MathProblem, grade: int) -> ValidationResult:
    """생성된 문제·정답을 배포 전에 다른 호출로 전수 검증한다 (SERVICE.md 4번).

    정오답 오류와 오타/표기 오류를 판정 대상으로 한다.
    """
    prompt = (
        f"다음은 초등학교 {grade}학년용으로 생성된 수학 문제와 정답이다. "
        "정답이 실제로 맞는지, 오탈자나 표기 오류가 없는지 검증해라.\n\n"
        f"문제: {problem.question}\n제시된 정답: {problem.answer}"
    )
    return invoke_with_retry(
        lambda model_id: _model(temperature=0.0, model_id=model_id).with_structured_output(ValidationResult), prompt
    )


_UNIT_SUFFIXES = ["개", "명", "살", "권", "장", "마리", "원"]


def _normalize_answer(raw: str) -> str:
    """정답 인정 범위 정규화 (SERVICE.md 4번).

    단위 표기, 띄어쓰기, 전각/반각, 대소문자 차이를 흡수한다.
    """
    text = unicodedata.normalize("NFKC", raw).strip().lower()
    text = re.sub(r"\s+", "", text)
    for suffix in _UNIT_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    if re.fullmatch(r"\d+/\d+", text):
        num, denom = text.split("/")
        text = str(float(num) / float(denom))
    return text


def grade_answer(problem: MathProblem, child_answer: str, used_hint: bool) -> dict:
    """아이가 제출한 답을 채점한다.

    힌트를 사용했다면 정답이어도 50%만 인정한다 (SERVICE.md 4번).
    """
    is_correct = _normalize_answer(child_answer) == _normalize_answer(problem.answer)
    score = 0.0
    if is_correct:
        score = 0.5 if used_hint else 1.0
    return {"is_correct": is_correct, "score": score, "used_hint": used_hint}


# ── 영어 (4지선다형 빈칸 채우기, SERVICE.md 3~4번 영어 확장 정책) ──────────


class EnglishProblem(BaseModel):
    """영어 문장 빈칸 채우기 문제 하나 (4지선다형)."""

    question: str = Field(description="빈칸이 포함된 영어 문장 (빈칸은 ___로 표시)")
    choices: list[str] = Field(description="보기 4개 (이 중 하나가 answer와 정확히 일치해야 한다)")
    answer: str = Field(description="정답 보기 (choices 중 하나와 정확히 같은 문자열)")
    topic: str = Field(
        default="",
        description="이 문제가 다루는 아주 짧은 주제 (예: '과거시제', '비교급', '전치사'). 부모 주간 리포트의 오답 주제 표시에 쓰인다.",
    )


class EnglishProblemSet(BaseModel):
    """하루 분량 영어 문제 묶음."""

    problems: list[EnglishProblem] = Field(description="생성된 문제 목록")


def generate_english_problems(grade: int, difficulty: Difficulty, count: int) -> list[EnglishProblem]:
    """학년·난이도에 맞는 영어 빈칸 채우기 문제를 생성한다 (SERVICE.md 3번: 3~6학년, 어휘·문법만).

    오답 3개는 헷갈리기 쉬운 유사 단어·문법으로 구성하고, 난이도에 따라 헷갈리는
    정도를 조절한다(쉬움=뻔하게 틀림, 어려움=헷갈림). 철자 문제는 내지 않는다.
    """
    difficulty_hint = {
        "쉬움": "오답 3개는 정답과 확연히 달라서 뻔하게 틀린 것처럼 보이게 만들어라",
        "표준": "오답 3개는 적당히 헷갈리되 문법을 아는 아이라면 구별할 수 있게 만들어라",
        "어려움": "오답 3개는 정답과 아주 비슷한 형태·의미로 헷갈리게 만들어라",
    }[difficulty]
    prompt = (
        f"초등학교 {grade}학년 수준의 영어 문장 빈칸 채우기 문제를 4지선다형으로 {count}개 만들어줘. "
        "빈칸은 어휘 뜻이나 문법(시제·조동사·비교급 등)만 묻고, 철자를 묻는 문제는 내지 마라. "
        f"난이도는 '{difficulty}'로 맞추고, {difficulty_hint}. "
        "문장은 영어로 쓰고, choices는 정확히 4개이며 그중 하나는 answer 필드와 토씨 하나 "
        "틀리지 않고 똑같아야 한다. "
        "문제마다 topic 필드에 다루는 주제를 한국어로 아주 짧게 붙여라 "
        "(예: '과거시제', '비교급', '전치사', '기본 어휘')."
    )
    result: EnglishProblemSet = invoke_with_retry(
        lambda model_id: _model(temperature=0.7, model_id=model_id).with_structured_output(EnglishProblemSet), prompt
    )
    return result.problems


def generate_english_hint(problem: EnglishProblem, grade: int) -> str:
    """정답 보기를 알려주지 않고 문법 규칙만 살짝 알려준다 (SERVICE.md 4번 영어 힌트 정책)."""
    prompt = (
        f"초등학교 {grade}학년 아이가 다음 영어 빈칸 채우기 문제를 풀다가 막혔다. "
        "어떤 보기가 정답인지는 절대 알려주지 말고, 관련된 문법 규칙이나 어휘 힌트만 "
        "짧고 다정하게 1~2문장으로 줘라.\n\n"
        f"문장: {problem.question}\n보기: {problem.choices}\n"
        f"(참고용, 아이에게 보이면 안 되는 정답: {problem.answer})"
    )
    for _ in range(2):
        result: Hint = invoke_with_retry(
            lambda model_id: _model(temperature=0.3, model_id=model_id).with_structured_output(Hint), prompt
        )
        if problem.answer.strip().lower() not in result.hint.lower():
            return result.hint
    return "문장을 다시 천천히 읽고, 어떤 문법 규칙이 어울릴지 생각해볼까?"


def cross_validate_english_problem(problem: EnglishProblem, grade: int) -> ValidationResult:
    """생성된 영어 문제를 배포 전에 다른 호출로 전수 검증한다 (SERVICE.md 4번).

    정답의 유일성뿐 아니라 나머지 오답 3개가 확실히 틀렸는지도 함께 판정한다.
    """
    prompt = (
        f"다음은 초등학교 {grade}학년용으로 생성된 영어 빈칸 채우기 문제다. "
        "answer가 문장에 들어갔을 때 문법적으로·의미적으로 유일하게 맞는 정답인지, "
        "그리고 choices 중 answer를 제외한 나머지 보기 3개가 문장에 들어갔을 때 "
        "확실히 틀린 것이 맞는지 모두 검증해라. 정답이 둘 이상 가능하거나, 오답인데 "
        "사실 답이 될 수도 있는 보기가 있다면 오류로 판정해라.\n\n"
        f"문장: {problem.question}\n보기: {problem.choices}\n제시된 정답: {problem.answer}"
    )
    return invoke_with_retry(
        lambda model_id: _model(temperature=0.0, model_id=model_id).with_structured_output(ValidationResult), prompt
    )


def grade_english_answer(problem: EnglishProblem, selected_choice: str, used_hint: bool) -> dict:
    """아이가 고른 영어 객관식 답을 채점한다.

    4지선다 선택이라 표기 정규화가 필요 없다 (SERVICE.md 4번) — 선택한 보기 문자열을
    정답과 그대로 비교한다. 힌트를 사용했다면 정답이어도 50%만 인정한다(수학과 동일).
    """
    is_correct = selected_choice.strip() == problem.answer.strip()
    score = 0.0
    if is_correct:
        score = 0.5 if used_hint else 1.0
    return {"is_correct": is_correct, "score": score, "used_hint": used_hint}


# ── 국어 (4지선다 객관식 + 단답형 1문제 혼합, SERVICE.md 3~4번 국어 확장 정책) ──


class KoreanProblem(BaseModel):
    """국어 문제 하나 (맞춤법·어휘·문법 등 4지선다 객관식, 또는 단답형 1문제).

    choices가 비어 있으면 단답형, 채워져 있으면 4지선다 객관식이다.
    """

    question: str = Field(description="문제 본문")
    choices: list[str] = Field(
        default_factory=list,
        description="4지선다 보기 4개 (단답형이면 빈 리스트로 둔다)",
    )
    answer: str = Field(description="정답 (객관식이면 choices 중 하나와 정확히 같은 문자열, 단답형이면 짧은 낱말)")
    topic: str = Field(
        default="",
        description="이 문제가 다루는 아주 짧은 주제 (예: '맞춤법', '띄어쓰기', '반대말', '높임말'). 부모 주간 리포트의 오답 주제 표시에 쓰인다.",
    )


class KoreanProblemSet(BaseModel):
    """하루 분량 국어 문제 묶음."""

    problems: list[KoreanProblem] = Field(description="생성된 문제 목록")


def generate_korean_problems(
    grade: int, difficulty: Difficulty, count: int, include_short_answer: bool = True
) -> list[KoreanProblem]:
    """학년·난이도에 맞는 국어 문제를 생성한다 (SERVICE.md 3번: 1~6학년 전체, 맞춤법·어휘·문법·띄어쓰기 등).

    한 세트 중 단답형은 정확히 1문제(반대말/비슷한말 쓰기, 또는 맞춤법에 맞게 고쳐
    쓰기 중 하나)만 내고 나머지는 4지선다 객관식으로 낸다. 오류 교차검증 실패로
    generate_node가 이 함수를 여러 차례 나눠 호출할 수 있는데, 그때마다 "마지막
    1개는 단답형"을 반복 요청하면 세트 전체에 단답형이 여러 개 섞이므로(실제로 이
    문제가 있었다 — 첫 배치에서 단답형이 이미 검증 통과했는데 재시도 배치가 또
    단답형을 냄), 이미 단답형을 확보했으면 호출자가 `include_short_answer=False`로
    넘겨서 이번 배치는 전부 객관식만 내도록 한다. 객관식 오답 3개는 영어와 같은
    방식으로 난이도에 따라 헷갈리는 정도를 조절한다. 서술형·독해 지문은 내지 않는다
    (SERVICE.md 4번: 자동채점 가능한 유형만).
    """
    difficulty_hint = {
        "쉬움": "객관식 오답 3개는 정답과 확연히 달라서 뻔하게 틀린 것처럼 보이게 만들어라",
        "표준": "객관식 오답 3개는 적당히 헷갈리되 아는 아이라면 구별할 수 있게 만들어라",
        "어려움": "객관식 오답 3개는 정답과 아주 비슷한 형태로 헷갈리게 만들어라",
    }[difficulty]
    if include_short_answer and count >= 1:
        type_instr = (
            f"마지막 1개만 단답형으로 내라 — '반대말이나 비슷한말 쓰기' 또는 '맞춤법에 맞게 "
            "고쳐 쓰기' 중 하나를 골라서 내고, choices는 빈 리스트로 두고, 답이 사실상 "
            "하나로만 정해지는 문제여야 한다(예: 표준어 하나만 정답이 되게). "
            f"나머지 {count - 1}개는 4지선다 객관식으로 내라."
        )
    else:
        type_instr = "단답형은 내지 말고 전부 4지선다 객관식으로 내라(choices를 정확히 4개씩 채운다)."
    prompt = (
        f"초등학교 {grade}학년 수준의 국어 문제를 {count}개 만들어줘. "
        "맞춤법, 띄어쓰기, 어휘 뜻, 반대말·비슷한말, 높임말, 흉내 내는 말, 속담 등에서 "
        "골고루 내고, 서술형이나 긴 독해 지문은 내지 마라. "
        f"{type_instr} "
        f"난이도는 '{difficulty}'로 맞추고, {difficulty_hint}. "
        "객관식은 choices를 정확히 4개 채우고 그중 하나는 answer 필드와 토씨 하나 "
        "틀리지 않고 똑같아야 한다. "
        "문제마다 topic 필드에 다루는 주제를 아주 짧게 붙여라 (예: '맞춤법', '띄어쓰기', "
        "'반대말', '높임말')."
    )
    result: KoreanProblemSet = invoke_with_retry(
        lambda model_id: _model(temperature=0.7, model_id=model_id).with_structured_output(KoreanProblemSet), prompt
    )
    return result.problems


def generate_korean_hint(problem: KoreanProblem, grade: int) -> str:
    """정답을 알려주지 않고 맞춤법 규칙이나 낱말 뜻 힌트만 살짝 알려준다 (SERVICE.md 4번 힌트 정책)."""
    prompt = (
        f"초등학교 {grade}학년 아이가 다음 국어 문제를 풀다가 막혔다. "
        "정답은 절대 알려주지 말고, 관련된 맞춤법 규칙이나 낱말 뜻 힌트만 짧고 다정하게 "
        "1~2문장으로 줘라.\n\n"
        f"문제: {problem.question}\n"
        + (f"보기: {problem.choices}\n" if problem.choices else "")
        + f"(참고용, 아이에게 보이면 안 되는 정답: {problem.answer})"
    )
    for _ in range(2):
        result: Hint = invoke_with_retry(
            lambda model_id: _model(temperature=0.3, model_id=model_id).with_structured_output(Hint), prompt
        )
        if _normalize_korean_core(problem.answer) not in _normalize_korean_core(result.hint):
            return result.hint
    return "낱말의 뜻과 규칙을 다시 한 번 천천히 떠올려볼까?"


def cross_validate_korean_problem(problem: KoreanProblem, grade: int) -> ValidationResult:
    """생성된 국어 문제를 배포 전에 다른 호출로 전수 검증한다 (SERVICE.md 4번).

    객관식(choices 있음)은 영어와 동일하게 정답 유일성 + 오답 3개가 확실히 틀렸는지까지
    검증하고, 단답형(choices 없음)은 수학과 동일하게 정답 유일성만 검증한다.
    """
    if problem.choices:
        prompt = (
            f"다음은 초등학교 {grade}학년용으로 생성된 국어 객관식 문제다. "
            "answer가 유일하게 맞는 정답인지, choices 중 answer를 제외한 나머지 보기 3개가 "
            "확실히 틀린 것이 맞는지 모두 검증해라. 정답이 둘 이상 가능하거나, 오답인데 "
            "사실 답이 될 수도 있는 보기가 있다면 오류로 판정해라.\n\n"
            f"문제: {problem.question}\n보기: {problem.choices}\n제시된 정답: {problem.answer}"
        )
    else:
        prompt = (
            f"다음은 초등학교 {grade}학년용으로 생성된 국어 단답형 문제다. "
            "정답이 실제로 맞는지, 그리고 답이 사실상 하나로만 정해지는 문제인지(다른 "
            "표준적인 정답이 함께 존재하지 않는지), 오탈자나 표기 오류가 없는지 검증해라.\n\n"
            f"문제: {problem.question}\n제시된 정답: {problem.answer}"
        )
    return invoke_with_retry(
        lambda model_id: _model(temperature=0.0, model_id=model_id).with_structured_output(ValidationResult), prompt
    )


def _normalize_korean_core(text: str) -> str:
    """국어 단답형 채점용 정규화 (SERVICE.md 4번: 핵심 단어 일치).

    조사·띄어쓰기·마침표 차이는 무시하고 핵심 정답 단어만 남긴다.
    """
    text = unicodedata.normalize("NFKC", text).strip().lower()
    return re.sub(r"[\s.,!?~]+", "", text)


def grade_korean_answer(problem: KoreanProblem, child_answer: str, used_hint: bool) -> dict:
    """아이가 제출한 국어 답을 채점한다.

    객관식(choices 있음)은 영어처럼 선택한 보기 문자열을 그대로 비교한다. 단답형
    (choices 없음)은 조사·띄어쓰기·마침표 차이를 무시하고 핵심 정답 단어가 포함되면
    정답으로 인정한다(SERVICE.md 4번). 힌트를 사용했다면 정답이어도 50%만 인정한다.
    """
    if problem.choices:
        is_correct = child_answer.strip() == problem.answer.strip()
    else:
        core_answer = _normalize_korean_core(problem.answer)
        core_child = _normalize_korean_core(child_answer)
        is_correct = bool(core_answer) and bool(core_child) and (
            core_answer == core_child or core_answer in core_child or core_child in core_answer
        )
    score = 0.0
    if is_correct:
        score = 0.5 if used_hint else 1.0
    return {"is_correct": is_correct, "score": score, "used_hint": used_hint}
