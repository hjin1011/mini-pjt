# 프로젝트 규칙

## 기술 스택
- Python 3.14, 가상환경은 `.venv` 사용 (`python -m venv .venv`)
- LangChain 1.x, LangGraph 1.x
- 모델은 Amazon Bedrock (ChatBedrockConverse), 기본 모델 ID는 `us.anthropic.claude-haiku-4-5-20251001-v1:0` (크로스리전 추론 프로파일 — `us.` 접두어 없이 온디맨드로는 호출 불가) — AWS 자격증명은 .env에서 읽는다
- Agent 생성은 langchain.agents 의 create_agent 를 쓴다
- 오류 교차검증(SERVICE.md 4번 정책)은 문제 생성과 별개로 같은 모델을 독립 호출해 구현한다
- **모델 폴백 체인 (2026-09-17, 실제 계정 일일 토큰 한도 초과를 겪고 추가함):** `src/tools.py`의 `MODEL_FALLBACK_CHAIN`(9개 모델 — Anthropic 계열의 `us.`/`global.` 리전 프로파일 5개를 우선 순위로 두고, Amazon Nova 계열 4개를 구조화 출력 검증 이력이 적어 마지막 안전망으로 둠)을 `invoke_with_retry()`(동기, `tools.py`의 문제 생성·힌트·교차검증 6곳이 사용)와 `agent.py`의 `_ainvoke_with_retry()`(비동기, `REACT_AGENT.ainvoke` 호출용, 필요 시 `_build_react_agent(model_id)`로 다른 모델을 물린 에이전트를 즉석에서 새로 만듦)가 순서대로 시도한다. `ThrottlingException`·`ServiceUnavailableException`·`ModelTimeoutException`·`ModelNotReadyException`(`is_retryable_bedrock_error()`로 판정)를 만나면 같은 모델을 기다리지 않고 바로 다음 모델로 교체해 즉시 재시도한다 — 기존의 "같은 모델을 지수 백오프로 재시도"하던 방식을 대체함(같은 모델을 기다려봐야 계정 전체 일일 한도라면 소용없고, 실제로 이 문제를 겪은 뒤 설계를 바꿨다). `ValidationException`처럼 재시도해도 성공할 수 없는 오류는 즉시 그대로 올린다. 체인의 모델을 전부 시도해도 실패하면 마지막 오류를 올린다. 이 판정 로직·체인 목록은 `tools.py`에 한 곳에만 두고 `agent.py`·`evaluation/run_eval.py`는 그걸 import해서 쓴다 (정책 이중 관리 방지)
- API 서버는 FastAPI + uvicorn
- 진행 상황 저장은 SQLite 파일 하나 (`data/app.db`), 테이블 4개
  - `attempts`: 학습지 생성 1건 (id, created_at, **attempt_date**(자정 기준 로컬 날짜), **subject**, grade, difficulty, problems_json, used_fallback, approval_mode)
  - `grades`: 채점 1건 (id, **attempt_id**(attempts.id 참조), **problem_index**, question, correct_answer, child_answer, is_correct, score, used_hint, graded_at, graded_date, **topic**, **subject**) — `POST /query`가 돌려주는 `attempt_id`를 프론트가 들고 있다가 `POST /grade` 호출 시 `problem_index`·`topic`·`subject`와 함께 되돌려줘서 연결한다
  - `stars`: 점수(별) 누적 카운터, 단일 행만 존재(`id=1`, `total_stars`) — SERVICE.md 4번 점수(별) 시스템용, 3차 구현 범위 참고
  - `wrong_answers`: 오답노트 1건(id, subject, grade, wrong_date, question, choices_json, correct_answer, child_answer, tag, created_at) — SERVICE.md 3·4번 오답노트용, 4차 구현 범위 참고. 과목별로 최대 50개(`WRONG_ANSWER_LIMIT`)만 유지하고 초과분은 `created_at` 기준 가장 오래된 것부터 삭제한다(FIFO)
  - `init_db()`가 구버전 DB에는 `ALTER TABLE`로 `attempt_date`/`subject`(attempts), `topic`/`subject`(grades) 컬럼을 자동 추가한다 (마이그레이션, `grades.subject`는 기존 행이 전부 수학이었으므로 `DEFAULT 'math'`로 채운다). `wrong_answers`는 신규 테이블이라 `CREATE TABLE IF NOT EXISTS`만으로 충분하고 별도 마이그레이션이 필요 없다
  - `topic`: `MathProblem`에 추가한 필드로, 문제 생성 시 LLM이 "두 자리 수 뺄셈(받아내림)"처럼 짧은 주제를 자유 텍스트로 붙인다 (고정 카테고리 대신 자유 텍스트로 결정 — 주간 리포트에서 "이번 주 오답 주제" 목록을 보여줄 때 씀, 오답노트의 `tag`도 이 값을 그대로 재사용한다). 문제은행(`question_bank.json`) 항목과 `grade_submission`/`grade_korean_submission`(ReAct 자연어 채점 경로)은 topic이 없어 빈 문자열로 남는다

## 1차 구현 범위
- 지금은 핵심 루프만 구현한다: 문제 생성 → 오류 교차검증 → 채점
- 점수(별), 인쇄용 출력, 카카오톡 알림은 이후 단계에서 추가한다 (주간 리포트는 구현 완료)

## 2차 구현 범위: 영어 확장 (2026-09-17, SERVICE.md discovery 인터뷰 기반 구현 완료)
- `src/tools.py`: `EnglishProblem`(question/choices/answer/topic), `generate_english_problems`(3~6학년, 어휘·문법만·철자 제외, 난이도별 오답 헷갈림 정도 조절), `cross_validate_english_problem`(정답 유일성 + 오답 3개의 확실한 오답 여부까지 판정 — 수학보다 검증 기준이 하나 더 있음), `generate_english_hint`(문법 규칙만, 정답 비노출), `grade_english_answer`(4지선다라 표기 정규화 불필요, 선택한 보기 문자열을 정답과 그대로 비교)
- `src/agent.py`: `SUBJECT_GRADE_RANGE = {"math": (1,6), "english": (3,6)}`로 `validate_input`을 과목별 학년 범위 검증으로 일반화. `WorksheetState`에 `subject` 필드를 추가해 `generate_node`/`validate_node`/`approval_node`가 과목별로 분기하도록 LangGraph를 재사용(파이프라인 자체는 수학과 동일 구조). `run_worksheet_graph(..., subject=...)`. 새 ReAct 도구 `generate_english_worksheet`/`grade_english_submission`을 추가(기존 `generate_worksheet`/`grade_submission`은 안 건드림 — `evaluation/test_queries.csv`의 `expected_tools` 호환성 유지). `QUESTION_BANK_PATHS`로 과목별 문제은행 파일 분리(`data/question_bank.json` vs `data/question_bank_english.json`, 영어는 3~6학년×표준 각 10문제=40개를 AI 생성+교차검증 파이프라인으로 시딩, 검증 반복 실패 문항은 스킵 후 대체 생성)
- SYSTEM_PROMPT를 "학습지 생성 요청"과 "채점 요청"으로 섹션을 나눠 재구성했다 — 채점 도구(`grade_submission`/`grade_english_submission`)는 학년·난이도 파라미터가 아예 없는데, 예전에 이 둘을 한 목록에 나열했더니 모델이 채점 요청에도 학년을 되묻는 회귀가 생겨서(자동 평가 실행기가 잡아냄) "채점 요청엔 학년·난이도가 필요 없다"를 명시적으로 분리했다
- DB: `grades` 테이블에 `subject TEXT DEFAULT 'math'` 컬럼 추가(기존 행은 전부 수학이므로 마이그레이션이 자동으로 'math'로 채움). `weekly_report()`를 과목별 `overview`(사용일수·정답률·힌트율·완료학습지수)와 `by_subject`(과목별 날짜별 그래프+오답주제)로 재구성
- 프론트엔드는 아래 "프론트엔드" 섹션 참고

## 3차 구현 범위: 점수(별) 시스템 (2026-09-17, 구현 완료)
- 산정 규칙(SERVICE.md 4번, `stars_for_grade()` in `src/db.py`): 문제 하나 채점할 때마다 힌트 없이 정답=별 2개, 힌트로 정답=별 1개, 오답=0개. 수학·영어 과목 통합(과목별로 안 나눔), 완료 여부·스트릭은 이 계산에 안 들어간다 — 순수하게 문제별 정답/힌트 여부로만 정해지는 점수제
- 표시: 아이 화면 상단바에 "오늘 딴 별 수"(`#today-stars-value`, 날짜·요일과 같은 자리)를 채점 즉시(문제 하나하나 채점될 때마다) 갱신해서 보여준다. 자정 지나면(다음 날 `graded_date` 기준 재계산이므로) 0부터 다시 센다
- 채점 결과 배너에도 별도 표시(2026-09-17 추가): 상단바와는 별개로, "채점하기"를 눌러 나오는 점수 배너(`.score-banner`) 안에 `.score-today-stars`/`.score-today-stars-value`를 두어 그 순간의 "오늘 딴 별 수"를 한 번 더 보여준다. `gradeSubjectPanel()`이 채점 루프를 도는 동안 마지막으로 받은 `result.today_stars`를 `latestTodayStars`에 저장해뒀다가, 다 끝나면 `showResultForPanel(subject, score, max, latestTodayStars)`로 넘겨 배너 렌더링 시 같이 채운다(상단바는 상시 노출용, 이 배너 표시는 "방금 이만큼 땄다"를 결과 확인 시점에 바로 보여주는 용도)
- 누적: "오늘 딴 별 수"와 별개로 전체 누적 별 수를 DB에 저장하고, `log_grade()`가 채점을 기록하는 같은 트랜잭션 안에서 `stars.total_stars`를 즉시 갱신한다(오늘 카운터는 저장 없이 `grades`에서 그때그때 계산, 누적만 실제로 저장). 절대 초기화 안 됨(스트릭이 끊기거나 날짜가 바뀌어도 유지)
- DB: `stars` 테이블 추가(단일 행 누적 카운터 — `id INTEGER PRIMARY KEY CHECK (id = 1)`, `total_stars INTEGER NOT NULL DEFAULT 0`, `init_db()`가 `INSERT OR IGNORE`로 첫 행을 만든다). 기존 `attempts`·`grades`에 이어 3번째 테이블
- API: `log_grade()`가 이번 문제로 딴 별 개수를 반환하도록 바뀜(반환형 `None`→`int`). `POST /grade` 응답에 `stars_earned`(이번 문제)·`today_stars`·`total_stars`를 포함(`get_stars_summary()`). `GET /stars`(페이지 최초 로딩용), `weekly_report()`에도 `total_stars` 포함(부모 리포트용). ReAct 채점 도구(`grade_submission`/`grade_english_submission`)도 결과 JSON에 `stars_earned`를 추가
- 프론트(`app.js`): `renderStarBreakdown(container, total)`이 누적 별 수를 1000/100/10/1 단위로 나눠(`STAR_TIERS`) "단위 아이콘(`ph-fill ph-star`) × 개수"로 그린다(0개인 단위는 생략). 단위가 클수록 CSS `.star-tier-*`가 font-size와 drop-shadow 글로우·`star-sparkle` 애니메이션을 단계적으로 키운다. 색도 메달 등급처럼 단위별로 다르다 — `.star-tier-1`은 동색(`#B08D57`), `.star-tier-10`은 은색(`#9AA5B1`), `.star-tier-100`은 금색(`#FFD700`), `.star-tier-1000`은 무지개 그라디언트(`background: linear-gradient(...)` + `-webkit-background-clip: text`로 아이콘 글리프에 그라디언트 입힘, `star-rainbow` 키프레임으로 색이 흘러가게 애니메이션). 별 아이콘 위에 그 단위 숫자(`.star-unit-label`, `position: absolute`로 아이콘 중앙에 겹쳐 그림)도 작게 표시해 어떤 단위인지 바로 구분되게 한다(흰 글자 + 검은 text-shadow 아웃라인이라 배경색과 무관하게 읽힘, 크기는 단위가 클수록 커짐). 단위 아이콘들 옆에 "총 N개"(`.star-total`, 2026-09-18 추가 — 단위 분해만으로는 정확한 누적 수치를 바로 알기 어렵다는 피드백 반영)도 같이 표시한다. 학년 선택 화면(`#grade-star-breakdown`)과 부모 리포트(`#report-star-breakdown`) 양쪽에서 재사용. `gradeSubjectPanel()`이 `/grade` 응답의 `today_stars`/`total_stars`로 두 표시를 즉시 갱신한다. 페이지 로딩 시 `fetchStars()`가 `GET /stars`로 초기값을 채운다
- 표시 UI(제안): "오늘 딴 별 수"는 상단바에 날짜·요일(`#topbar-date`)과 같은 자리에 상시 노출. 누적 별 수는 아이 화면 상단바 + 부모 주간 리포트 overview 양쪽에 표시. 누적 별 수는 그대로 숫자로 쓰지 않고 1/10/100/1000 자릿수로 분해해 "그 자릿수 전용 별 아이콘 × 개수"로 렌더링한다(예: `renderStarBreakdown(total)`가 `{1000: n, 100: n, 10: n, 1: n}`을 계산 → 0개인 자릿수는 생략). 아이콘은 새 이미지 없이 기존 Phosphor `ph-fill ph-star` 재사용, 자릿수가 클수록 CSS로 font-size와 반짝임 효과(box-shadow 글로우/스파클 애니메이션)를 단계적으로 키운다. 9,999를 넘는 경우의 상위 자릿수 처리는 아직 미정(실제 도달하면 그때 정함)

## 4차 구현 범위: 오답노트 (2026-09-17, discovery 인터뷰 9문항 기반 구현 완료)
- DB(`src/db.py`): `wrong_answers` 테이블 + `WRONG_ANSWER_LIMIT = 50`. `log_grade()`가 채점을 기록하는 같은 트랜잭션 안에서 `is_correct=False`이면 `_record_wrong_answer()`를 호출해 한 줄 추가하고, 곧바로 그 과목의 개수를 세어 50개 초과분을 `created_at` 오래된 순으로 `DELETE`한다(과목별로 독립적인 한도 — 수학·영어 각각 최대 50개, 통합 한도 아님). `get_wrong_answers(subject)`가 최신순으로 조회한다
- 학년 필드: `attempt_id`가 있으면(= `POST /grade`로 학습지를 풀다가 채점한 경우) `attempts.grade`를 조회해 채운다. `attempt_id`가 없으면(= ReAct 자연어 채점 도구, `grade_submission`/`grade_english_submission`) 학년을 알 방법이 없어 빈 값(`NULL`)으로 남는다 — `topic`이 같은 경로에서 빈 문자열로 남는 것과 같은 종류의 한계
- 태그: 새 분류 체계를 만들지 않고 기존 `topic` 필드를 그대로 재사용한다
- 보기(`choices`): 영어만 저장한다(4지선다 보기 배열을 JSON으로 직렬화). 수학은 빈 배열. `grade_english_submission` 도구와 `POST /grade`(영어) 둘 다 `log_grade(..., choices=...)`로 전달한다
- 정책(SERVICE.md 4번): 같은 문제를 여러 번 틀리면 매번 새 줄로 추가한다(중복 제거·병합 없음). 나중에 같은 문제를 다시 맞혀도 오답노트 항목을 자동으로 지우지 않는다(50개 한도로만 자연스럽게 밀려남) — 오답노트는 "미해결 목록"이 아니라 "한때 틀렸다"는 이력이기 때문
- API(`src/api.py`): `GET /wrong-answers?subject=math|english|korean`가 `{"items": [...]}`을 반환한다(`id` 제외 항목: subject·grade·date·question·choices·correct_answer·child_answer·tag)
- 프론트(`web/`): 아이 화면에는 전혀 노출하지 않는다. 부모 화면(승인 대기 목록, `#screen-parent`) 상단에 "주간 리포트 보기"와 나란히 "오답노트 보기"(`#wrong-answers-open-btn`) 버튼을 두고, 누르면 새 화면(`#screen-wrong-answers`)으로 이동한다(주간 리포트에 통합하지 않음, 상단 토글 버튼도 이 화면에서 "아이 화면"으로 바뀌어 `isParentSideScreen()`에 포함시킴). 화면 구성은 학습지 풀이 화면과 같은 과목별 탭 전환(`.worksheet-tab` 클래스 재사용) UI이고, 목록은 최신순(`created_at DESC`, 서버가 이미 그 순서로 반환)으로 카드 나열(날짜·태그·문제·보기(있으면)·정답/아이가 쓴 답을 초록/빨강으로 대비)

## 5차 구현 범위: 국어 확장 (2026-09-18, SERVICE.md discovery 인터뷰 기반 구현 완료)
- `src/tools.py`: `KoreanProblem`(question/choices/answer/topic — `choices`가 비어 있으면 단답형, 채워져 있으면 4지선다 객관식), `generate_korean_problems`(1~6학년 전체, 한 세트 10문제 중 마지막 1문제만 단답형(반대말·비슷한말 쓰기 또는 맞춤법에 맞게 고쳐 쓰기 중 하나)이고 나머지는 객관식, 난이도별 오답 헷갈림 정도 조절은 영어와 동일), `cross_validate_korean_problem`(객관식은 영어와 동일하게 정답 유일성 + 오답 3개 검증, 단답형은 수학과 동일하게 정답 유일성만 검증 — `problem.choices` 유무로 내부 분기), `generate_korean_hint`(정답 비노출, 맞춤법 규칙·낱말 뜻 힌트만), `grade_korean_answer`(객관식은 영어처럼 문자열 그대로 비교, 단답형은 `_normalize_korean_core()`로 조사·띄어쓰기·마침표를 지운 뒤 핵심 단어 포함 여부로 판정 — 수학의 표기 정규화와는 다른 별도 규칙)
- `src/agent.py`: `SUBJECT_GRADE_RANGE`에 `"korean": (1, 6)` 한 줄 추가(수학과 동일 범위라 학년 제외 규칙 없음), `QUESTION_BANK_PATHS`에 `question_bank_korean.json` 한 줄 추가. `generate_node`/`validate_node`가 `subject`에 따라 문제 클래스·생성/검증 함수를 고르도록 3-way 분기로 일반화(그래프 구조는 그대로, 함수 하나만 바뀜). 새 ReAct 도구 `generate_korean_worksheet`/`grade_korean_submission` 추가(`grade_korean_submission`은 `choices` 파라미터를 선택적으로 받아 객관식·단답형 둘 다 한 도구로 처리 — 기존 두 채점 도구처럼 문제 유형별로 도구를 나누지 않음, 국어는 한 세트 안에 유형이 섞여 있어서). 기존 도구는 안 건드림
- SYSTEM_PROMPT: "학습지 생성 요청" 섹션에 국어 분기(학년만 확인되면 바로 호출, 영어 같은 학년 제외 규칙 없음)를 추가하고, "채점 요청" 섹션에는 "국어 문제(객관식이든 단답형이든)면 grade_korean_submission을 쓰라"는 분기를 추가했다 — 회귀 방지를 위해 두 섹션 모두 기존 조건절 하나만 끼워넣지 않고 새 불릿으로 분리
- DB: 스키마 변경 없음 — `subject` 컬럼이 이미 자유 텍스트라 `"korean"` 값만 추가되면 된다. `src/db.py`의 `SUBJECTS` 리스트에 `"korean"` 추가(→ `weekly_report()`의 `by_subject`가 자동으로 국어를 포함, 프론트 `renderBySubject()`가 데이터 기반이라 템플릿 복제도 자동)
- 프론트엔드는 아래 "프론트엔드" 섹션 참고 — 국어가 한 학습지 안에서 객관식·단답형을 **섞어 낸다**는 점 때문에, 기존에 과목 단위로 렌더링을 분기하던 `renderWorksheetPanel`/`gradeSubjectPanel`/`hasUnansweredInPanel`을 문제 단위(`hasChoices(problem)`)로 분기하도록 리팩터링했다(영어 확장 때는 과목 전체가 객관식 아니면 전체가 단답형이라 `isEnglish` 플래그 하나로 충분했지만, 국어는 그 가정이 깨져서 구조를 일반화해야 했다)

## 과목 확장 절차 (영어 확장 경험 기반 — 한문 등 다음 과목도 이 순서로 진행)

영어를 2차로 추가하면서 실제로 밟은 순서다. 다음 과목을 확장할 때 그대로 따라간다.

**1단계 — SERVICE.md discovery 인터뷰부터, 코드는 건드리지 않는다.**
"질문만 해달라, 3개씩, 답하면 그 답을 파고드는 질문"으로 진행한다. 새 과목마다 반드시
확인해야 하는 항목(영어 때 실제로 물었던 것들):
- 학년 범위가 수학(1~6)과 같은지 다른지, 다르면 왜(교과과정상 이유) — 다르면 학년 선택
  화면에서 그 과목 버튼을 숨기는 조건도 같이 정한다
- 문제 형식(단답형/객관식 등)과 화면 언어 구성(지시문 언어, 문제 자체 언어)
- (객관식이면) 오답 설계 기준과 난이도별 헷갈림 조절 방식
- 오류 교차검증이 정답 하나만 보는지, 오답 후보까지 검증하는지
- 힌트 정책 — 특히 "점수 인정 기준(50%)이 수학과 같은지"는 반드시 명시적으로 재확인한다
- 문제은행 준비 범위(학년×난이도)와 시딩 방식
- 연습장(그림판) 같은 수학 전용 기능이 이 과목에도 필요한지
- 화면 흐름(과목 버튼 노출/비활성 조건), 미완료 게이트가 과목 통합인지
- 점수(별)·배지가 과목 통합인지 분리인지
- 성공 기준(5번)이 과목별 독립 측정인지, 새 과목은 언제부터 카운트 시작하는지
- 주간 리포트에 어떻게 통합되는지(이미 overview+by_subject 구조라 대부분 자동으로 해결됨)
결정된 내용은 SERVICE.md에 "기획 확정, 아직 미구현"으로 먼저 반영한다. 사용자가 실제
구현 착수를 요청하기 전까지는 코드·콘텐츠를 먼저 만들지 않는다.

**2단계 — 구현 (사용자가 명시적으로 "이제 진행해줘" 할 때만).**
- `src/tools.py`: 새 Problem 모델(`MathProblem`/`EnglishProblem`과 같은 모양 — question,
  정답 관련 필드, `topic`) + `generate_X_problems`/`cross_validate_X_problem`/
  `generate_X_hint`/`grade_X_answer` 4종 세트를 추가한다. 기존 함수는 이름·시그니처 그대로
  둔다(수정 아니라 추가).
- `src/agent.py`: `SUBJECT_GRADE_RANGE`에 학년 범위 한 줄 추가, `QUESTION_BANK_PATHS`에
  파일 경로 한 줄 추가. `WorksheetState`/`generate_node`/`validate_node`/`approval_node`는
  이미 `subject`로 분기하는 구조라 새 과목 분기만 추가하면 된다(그래프를 복제하지 않는다).
  새 ReAct 도구 `generate_X_worksheet`/`grade_X_submission`을 추가하고 `REACT_AGENT`의
  tools 목록에 더한다 — 기존 도구는 절대 안 건드린다(`evaluation/test_queries.csv`의
  `expected_tools` 호환성, 회귀 방지).
- SYSTEM_PROMPT는 "학습지 생성 요청"/"채점 요청"처럼 요청 유형별 섹션을 분리해서
  확장한다. 기존 섹션 안에 조건절 하나만 끼워넣지 않는다 — 영어 확장 때 그렇게 했다가
  채점 요청에 학년을 되묻는 회귀가 생겼다(자동 평가 실행기가 잡아냄).
- DB는 기존 컬럼(`subject`) 재사용을 우선 고려하고, 정말 새 테이블·컬럼이 필요하면
  최소한으로 추가하며 `DEFAULT` 값으로 기존 행이 깨지지 않게 마이그레이션한다.
- 문제은행: 스크래치패드에 임시 시딩 스크립트를 작성해(커밋 대상 아님) 실제 Bedrock으로
  생성+교차검증까지 돌려 `data/question_bank_X.json`을 만든다. 학년 단위로 즉시 저장해서
  중간에 실패해도 이전 진행분은 남긴다.
- 프론트: `PANELS`/`SUBJECTS`처럼 이미 과목 목록 기반으로 일반화된 구조에 새 과목
  항목만 추가한다(과목 버튼, 학년 조건, 문제 렌더링 분기 등). 화면 자체를 새로 안 만든다.

**3단계 — 검증.**
- 실제 Bedrock으로 Playwright E2E(생성→채점, 새 과목 UI 특유의 렌더링까지) 확인.
- `evaluation/run_eval.py` 전체 재실행해서 기존 케이스가 회귀 없는지 확인한다. 새 과목이
  지원되면서 기존 "미지원 거절" 케이스가 "실제 생성" 케이스로 뜻이 바뀔 수 있다 —
  `test_queries.csv`에서 그 케이스를 갱신하고, 새 과목의 학년범위 가드레일 검증용
  케이스를 하나 추가한다(영어 때 id 10을 positive로 바꾸고 id 21을 새로 추가했다).

**4단계 — 문서 갱신.** SERVICE.md·CLAUDE.md·README.md를 "기획 확정" → "구현 완료"로
갱신하고, 발견한 회귀나 트러블슈팅은 README "트라이앤에러 회고"에 남긴다.

**5단계 — 사용자가 실제 화면에서 써보고 준 피드백을 반영.** 레이아웃·인터랙션 방식은
한 번에 맞히기 어렵다(영어도 순차 진행 → 동시 탭 패널로, 버튼 배치 등 여러 차례
수정했다) — 매번 Playwright로 재검증한 뒤 결과를 보고한다.

## 적용 패턴 (12개 중 7개, Day 1~7)
- LCEL chain — 문제 생성·채점 결과를 Pydantic 구조화 출력으로 반환
- ReAct — 자연어 요청("오늘 수학 학습지 만들어줘")을 보고 어떤 도구를 호출할지 스스로 판단
- 도구 다중 — 문제 생성, 오류 교차검증, 채점 도구를 한 요청 안에서 순서대로 결합
- 가드레일 — 입력값(학년 범위, 과목, 문제 유형) 검증과 오류 교차검증을 통한 출력 필터링
- HITL — 검증 3회 실패로 문제은행 대체가 필요할 때 interrupt()로 부모 승인 요청 (30초 내 무응답 시 자동 대체 후 사후 알림)
- Observability — LangSmith로 문제 생성부터 채점까지 트레이스 기록
- 평가(LLM-as-Judge) — evaluation/run_eval.py의 judge_answer()가 채점과 별개의 독립 LLM 호출로 expected_traits 충족·forbidden 위반 여부를 판정 (id 6 채점 거절 회귀를 이 평가로 실제로 잡아냄)

미적용: RAG(retriever.py는 스텁, 1차 범위는 검색 없이 자체 생성으로 충분), MCP 서버 연동,
미들웨어(재시도/백오프는 hand-roll wrapper이지 LangChain 공식 middleware 추상화가 아님),
Multi-Agent Supervisor, Plan-Execute·장기 메모리(langgraph.store 미사용).

## 테스트 스키마 (evaluation/test_queries.csv, §4-4 고정 양식)
컬럼 7개: `id`(필수) · `category`(필수, positive/negative/edge/guardrail 중 하나) ·
`input`(필수, 자연어 질의) · `expected_traits`(필수, 세미콜론 구분) ·
`forbidden`(선택, 세미콜론 구분) · `expected_tools`(선택, 세미콜론 구분, 도구 이름은
`generate_worksheet`/`grade_submission`/`generate_english_worksheet`/`grade_english_submission`/
`generate_korean_worksheet`/`grade_korean_submission` 중 하나) · `note`(선택).
카테고리 비율은 positive 40% · negative 20% · edge 25% · guardrail 15% 권장.
지금은 24건(영어 확장 후 id 10을 영어 학습지 생성 positive 케이스로 교체하고
1~2학년+영어 거절 가드레일 검증용 id 21을 추가, 국어 확장 후 id 22~24를 새로
추가 — 국어 학습지 생성, 1학년 국어 지원 확인(영어 id 21과 대조), 국어 채점 도구
분기), 실제 `REACT_AGENT`로 대부분 검증 완료.
자동 평가 실행기 `evaluation/run_eval.py`(프로젝트 루트에서 `python evaluation/run_eval.py`로 실행)가 전체 케이스를 재현 가능하게 재실행한다: `expected_tools`는 trace를 파싱해 코드로, `expected_traits`·`forbidden`은 독립 LLM 호출(`judge_answer`, `cross_validate_problem`과 같은 "별도 호출 검증" 패턴)로 채점한다. `generate_worksheet`·`generate_english_worksheet`·`generate_korean_worksheet`를 유발하는 케이스(내부적으로 생성+문제당 교차검증까지 연쇄 호출돼 비용이 큼)는 1회만, 나머지 가벼운 케이스는 LLM 비결정성에 대비해 3회 반복 실행해 통과율(N/3)로 보고한다. 실행할 때마다 `evaluation/round{N}_report.md`를 새로 만든다(기존 파일은 안 건드림). 영어 확장 통합 시 이 실행기가 실제 회귀(채점 요청에 학년을 되묻는 문제)를 잡아냈다 — SYSTEM_PROMPT를 "학습지 생성"/"채점" 섹션으로 분리해 수정.

## 폴더 구조 (제출 규약. 필수 파일은 바꾸지 않는다)
src/agent.py       메인 에이전트 그래프 (LangGraph 핵심 루프 · ReAct 에이전트 · 가드레일)
src/tools.py       도메인 도구 (문제 생성 · 채점 · 오류 교차검증 · 풀이 힌트)
src/retriever.py   RAG 파이프라인 (학교 교과과정 · 한자 급수표 검색)
data/              사용한 문서와 데이터
evaluation/        test_queries.csv 와 평가 리포트
SERVICE.md         서비스 스펙
Dockerfile         클린 환경 재현
requirements.txt   의존성 목록
README.md          프로젝트 개요·실행 방법·평가 결과
run.sh             실행 스크립트 (선택)

**2026-09-17 추가 (제출 필수 목록 확장, 기존 3개 파일은 그대로 유지):** `agent.py`가
830줄까지 늘어나 "파일 하나에 한 가지 역할만" 규칙을 어기고 있다는 자체 감사 지적을
반영해, SQLite 영속성과 FastAPI 라우트를 분리했다.
- `src/db.py`   저장소 (SQLite, `attempts`·`grades`·`stars` 3테이블 — `init_db`·`log_attempt`·
  `log_grade`·`get_stars_summary`·`weekly_report` 등, LangGraph·FastAPI 어디에도 의존하지 않음)
- `src/api.py`  FastAPI 라우트 (`POST /query`·`/grade`·`/hint`, `GET /report/weekly`·`/stars`·
  `/pending-approvals`, `POST /approve`, 정적 파일 마운트) — 실제 실행 엔트리포인트가
  `src.agent:app`에서 `src.api:app`으로 바뀌었다 (`run.sh`·`Dockerfile` CMD 갱신 완료)
- `src/agent.py`는 이제 LangGraph 핵심 루프·ReAct 에이전트·가드레일(`validate_input`)만
  남아 설명("메인 에이전트 그래프")과 실제 내용이 일치한다

## 프론트엔드 (web/, 제출 규약 폴더 외 추가)
- 순수 정적 HTML/CSS/JS (`web/index.html`, `styles.css`, `app.js`) — 별도 빌드 도구 없음
- `src/api.py`가 FastAPI `StaticFiles`로 같은 오리진에 서빙한다 (API 라우트 등록 후 `/`에 마운트)
- 화면 흐름: 학년 선택(`#screen-grade`, 항상 1~6학년 전체 표시) → 과목·난이도 선택(`#screen-subject`, `openSubjectScreen()`) → POST /query로 학습지 생성 → 문제별 힌트 보기·입력/선택 → POST /grade로 채점 → 문제별 정오답 표시 + 총점 배너. 학년을 먼저 고르고 그 다음 화면에서 과목(수학/국어/영어)·난이도를 고른다 (SERVICE.md 4번 discovery 인터뷰 결정) — 학년이 1~2학년이면 그 화면에서 "영어" 버튼만 숨긴다(`ENGLISH_MIN_GRADE = 3`, 국어는 수학처럼 전 학년 지원이라 숨기지 않음)
- 과목은 다중 선택 가능(토글 방식, `selectedSubjects` 배열). 여러 개 고르면 `SUBJECT_ORDER = ["math","korean","english"]` 순서로 모두 생성한 뒤(`requestWorksheets()`), 한 화면에 **과목별 패널**(`#panel-math`/`#panel-korean`/`#panel-english`, 각자 문제 목록·채점 버튼·점수 배너를 따로 가짐)로 동시에 올려두고 위쪽 탭(`#worksheet-tabs`)으로 전환한다(`setActiveTab()`). 탭을 바꿔도 안 보이는 패널은 DOM에서 제거되지 않고 `is-hidden`으로만 숨기므로 입력한 답이 유지된다. 과목마다 "채점하기" 버튼이 따로 있어 독립적으로 채점할 수 있다(`gradeSubjectPanel(subject)`). "다른 학습지 받기"(`resetToGrade()`)는 이번에 생성한 **모든** 과목 패널이 답을 다 썼는지 확인한 뒤에만 통과시킨다(`hasAnyUnanswered()`) — 하나만 골랐으면 탭 없이 그 패널 하나만 보여 기존과 동일하게 동작한다
- 객관식 문제(영어 전체, 국어 일부): `problem.choices` 배열이 있으면 `renderWorksheetPanel()`이 텍스트 입력 대신 `.choice-group`(보기 4개 버튼)을 그린다. 가독성을 위해 `.choice-group`은 `.question-block` 안에 중첩해서 질문 바로 아래 한 줄로 나오게 한다(단답형의 `<input>`은 카드 오른쪽에 인라인으로 유지). 연습장 버튼은 수학에서만 보인다(SERVICE.md: 영어·국어는 객관식 위주라 풀이 낙서 불필요, 수학 전용 유지). 국어 확장(2026-09-18) 전에는 이 분기가 과목 단위(`isEnglish` 플래그)였는데, 국어가 한 세트 안에 객관식·단답형을 섞어 내면서 그 가정이 깨져 문제 단위(`hasChoices(problem)`)로 리팩터링했다 — `gradeSubjectPanel()`/`hasUnansweredInPanel()`도 같은 방식으로 문제마다 판정한다
- 페이지/헤더 제목은 "오늘의 학습지"(과목 중립적 — 예전엔 "오늘의 수학 학습지"였으나 영어 확장 후 변경)
- 과목 통합 미완료 게이트: `subjectDone = {math, english, korean}` (세션 메모리, 서버 강제 없음 — 기존 "이탈 방지"와 같은 프론트 UX 가드 수준). "다른 학습지 받기"(`resetToGrade()`)는 현재 학습지가 다 채워졌을 때만 통과시키고, 통과하면 그 과목을 `subjectDone[subject] = true`로 표시한 뒤 과목 선택 화면으로 돌아간다. 그날 지원되는 과목 중 하나라도 안 끝났으면(받은 적 없어도) 그 과목 버튼은 살아있고 나머지는 비활성화되며, 전부 끝내면 두 버튼 다 비활성화하고 "오늘 학습을 모두 마쳤어요" 안내를 보여준다(SERVICE.md 4번: 하루 세트 = 그날 지원되는 모든 과목의 학습지 각 1세트). 자정이 지나면(`checkDayRollover()`, 클라이언트 로컬 날짜 비교) 다음 방문 때 초기화된다
- 부모 화면: 상단바 "부모 화면" 토글(`#parent-toggle-btn`) → 4자리 PIN 모달(기본값 `0000`, `app.js`의 `PARENT_PIN` 상수) → 통과하면 같은 SPA 안에서 `GET /pending-approvals`를 3초 간격으로 폴링해 승인 대기 목록을 보여주고, 항목마다 `POST /approve`로 즉시 승인 가능. 부모 화면·주간 리포트·오답노트 화면(셋 다 "부모 쪽" 화면, `isParentSideScreen()`)에 있는 동안은 이 상단 토글 버튼 자체가 아이콘·문구 모두 "아이 화면"으로 바뀌고(`syncParentToggleBtn()`, `showScreen()`이 화면 전환마다 호출), 누르면 PIN 없이 바로 `backToChildScreen()`으로 부모 화면 진입 전 화면(학년 선택/문제 풀이 등)으로 복귀하며 폴링을 멈춘다 — 하단의 기존 "아이 화면으로 돌아가기"/"부모 화면으로 돌아가기" 버튼과 별개로 상단 토글도 같은 왕복 동작을 하도록 추가함
- 오답노트(부모 전용, SERVICE.md 3·4번 참고): 부모 화면 상단 "오답노트 보기"(`#wrong-answers-open-btn`) 또는 주간 리포트 화면 하단의 같은 버튼(`#report-wrong-answers-open-btn`, 2026-09-18 추가 — 리포트를 보다가 바로 오답노트로 넘어가고 싶다는 요청 반영)이 둘 다 같은 `openWrongAnswers()`를 호출해 `#screen-wrong-answers`로 이동한다. 어디서 열었든 뒤로가기는 항상 부모 화면으로 돌아간다(이 앱의 하위 화면은 모두 부모 화면을 허브로 삼는 구조라 리포트로 돌아가지 않음). `openWrongAnswers()`가 수학 탭부터 연다. 탭(`#wrong-answers-tabs`, `.worksheet-tab` 재사용, 국어 확장 후 수학/국어/영어 3개)을 누르면 `setActiveWrongAnswersTab(subject)`가 패널을 전환하고 `GET /wrong-answers?subject=...`를 호출해 그 과목 것만 새로 불러온다(과목을 한 번에 안 불러오고 탭 전환 시점에 불러옴). `renderWrongAnswerList()`가 카드마다 날짜·태그·문제·(보기가 있으면)보기·정답/아이가 쓴 답을 그린다. 아이 화면 어디에도 이 화면으로 가는 경로가 없다
- 미완료 이탈 방지: `resetToGrade()`(= "다른 학습지 받기" 클릭)는 `hasAnyUnanswered()`로 이번에 생성한 모든 활성 패널의 입력칸/보기 선택이 전부 채워졌는지 프론트에서만 확인한다. 하나라도 비어 있으면 alert로 막고 화면 전환을 하지 않는다 — 하루 생성 횟수 제한은 없고, "풀던 것을 안 끝내고 건너뛰는 것"만 막는다 (서버 쪽 강제는 없음, PIN과 마찬가지로 프론트 UX 가드). "다른 학습지 받기" 버튼은 패널마다 하나씩(`.restart-btn` 클래스, 같은 `resetToGrade` 핸들러) 그 패널의 "채점하기" 버튼 바로 옆(`action-row`)에 둔다 — 버튼 두 개의 경계가 겹쳐 보인다는 피드백을 받아 같은 줄에 나란히 배치함
- 힌트 버튼: `POST /hint`(`generate_hint`/`generate_english_hint`/`generate_korean_hint`, `subject` 필드로 분기)가 정답은 숨기고 풀이 방향(영어는 문법 규칙, 국어는 맞춤법 규칙·낱말 뜻)만 알려준다 (정답 문자열이 섞여 나오면 1회 재시도, 그래도 새면 일반 격려 문구로 대체). 힌트를 본 문제는 `used_hint=true`로 채점 요청 (SERVICE.md 4번: 힌트 사용 시 정답이어도 50%만 인정 — 영어·국어도 동일 기준)
- 연습장(그림판, 수학 전용): 문제별 "✏️ 연습장" 버튼 → `<canvas>` 모달, pointer 이벤트로 마우스·터치·펜 통합 처리. 서버로 전송하지 않는 순수 프론트 전용 낙서장이라 SERVICE.md 정책과는 무관 — 채점에 영향 없음. 문제별로 그린 내용을 dataURL로 메모리에 보관했다가 재열람 시 복원, 지우면 삭제. 새 학습지를 받으면 초기화된다
- 주간 리포트: 부모 화면의 "주간 리포트 보기" → `GET /report/weekly`(`weekly_report()`, `attempts`+`grades`를 과목별로 GROUP BY해서 계산, 별도 집계 테이블 없음)가 `overview`(과목별 사용일수·정답률·힌트 사용률·완료 학습지 수 요약 배열)와 `by_subject`(과목별 날짜별 `daily`·오답 topic 상위 5개)를 나눠서 돌려준다 (SERVICE.md 3번: 과목이 늘어나면 overview+과목별 상세로 구성). `renderOverview()`가 요약 카드를, `renderBySubject()`가 `<template id="subject-report-template">`을 과목 수만큼 복제해 상세 섹션(차트+오답 목록)을 그린다. 난이도 조정 칩을 누르면 `localStorage`(`preferredDifficulty`)에 저장되고, 학년 선택 화면이 다음 방문 때 그 값을 기본 난이도로 미리 선택한다 (서버에 별도 "현재 난이도" 상태를 두지 않고 이 기기의 localStorage로만 반영, 과목 구분 없이 공용 — 여러 기기/보호자나 과목별 난이도를 지원하게 되면 서버 저장으로 옮겨야 함)
- 주 선택 콤보박스: 리포트 화면 상단의 `#report-week-select`(이번 주~8주 전)가 `weekly_report(weeks_ago=N)`/`GET /report/weekly?weeks_ago=N`을 호출한다. `weeks_ago`는 요청 시점에서 `7*weeks_ago`일 전을 끝 날짜로 삼아 그 이전 7일을 계산하는 방식(달력 월~일 주 단위가 아니라 오늘 기준 굴러가는 7일 창이 통째로 뒤로 밀리는 방식)이라, 기존 "최근 7일" 정의(`weeks_ago=0`)와 그대로 호환된다. 리포트 화면을 열 때마다(`openReport()`) 선택값을 "이번 주"로 초기화한다
- 상단바에 오늘 날짜·요일(`renderTopbarDate()`, 예: "9월 17일 목요일")을 표시한다 — 아이가 오늘이 며칠·무슨 요일인지 바로 볼 수 있게, 서버 호출 없이 클라이언트 시계 기준으로 계산한다
- 날짜별 정답률 그래프: `dataviz` 스킬 절차(폼 선정 → 색상 배정 → `validate_palette.js`로 검증 → 마크 스펙 적용 → 인터랙션 → 접근성)를 따라 만든 순수 SVG 막대그래프(`renderDailyChart(daily, {svg, readout, table, tableBody})`, `app.js` — 과목별로 복제된 DOM 조각을 인자로 받도록 일반화됨). 막대는 `--color-primary`(`#2563EB`) 단일 색상(추이 비교이므로 sequential 컬러 규칙 적용, 검증 통과), 굵기 ≤24px, 상단만 4px 라운드(베이스라인은 직각, `roundedTopBarPath()`로 직접 path 생성), 0/50/100% 지점에 헤어라인 그리드라인. 문제를 안 푼 날은 점선 테두리의 빈 자리표시자(투명 히트박스를 전체 높이로 깔아 터치 타겟을 확보 — 시각적 마크가 작아도 클릭 영역은 그보다 크게, `dataviz` 인터랙션 규칙)로 따로 표시. 막대를 누르거나 포커스하면 차트 readout에 정확한 값이 뜨고, "표로 보기" 버튼으로 `<table>` 대안 뷰(스크린리더·저시력 사용자용 필수 컴포넌트)로 전환 가능. 추가로 막대 바로 위에도 SVG 자체 미니 툴팁(`chart-tooltip` 그룹, 배경 pill + 텍스트 한 줄)이 떠서 어떤 막대인지 바로 보이게 했다 — 데이터 있는 막대는 막대 꼭대기 위, 빈 날은 점선 표시 위쪽에 뜨고, 차트 좌우 끝(0~300 viewBox)을 벗어나지 않게 x좌표를 clamp한다. 장식용이라 `aria-hidden`이며 기존 readout(스크린리더용)은 그대로 유지. 모바일 390px·태블릿 820px 모두 Playwright로 스크린샷 검증 완료
- 디자인은 `ui-ux-pro-max` 스킬의 design-system 검색(`"kids education learning tablet app" --design-system`) 결과를 그대로 적용한 **Claymorphism**이었으나, 2026-09-18에 "더 부드럽게" 요청을 받아 같은 팔레트·폰트 계열을 유지한 채 톤만 낮췄다(스킬로 뽑은 3개 디자인 초안 중 "부드러운 클레이모피즘" 안을 선택 — 나머지 두 안은 소프트 뉴모피즘·소프트 UI 에볼루션이었다). 색상은 Learning blue `#2563EB`→`#3B6FB6`, play yellow `#F59E0B`→`#F5A94D`, fun pink `#EC4899`→`#F080B3`로 채도를 낮췄고(버튼 텍스트 대비 4.5:1 이상은 유지 확인), 배경 `#EFF6FF`→`#F7FAFF`·테두리 `#E4ECFC`→`#E7EEFC`로 더 밝게, 테두리는 3px→2px로 얇게, 라운드는 `--radius-lg` 24→28px·`--radius-md` 16→18px로 키우고, 버튼/카드의 "누르는" 그림자 깊이(6px→4px, 호버 8→6, 액티브 2→1)와 호버/액티브 이동 거리(2px/4px→1px/2px)도 줄여 덜 통통 튀게 했다. 폰트(Baloo 2/Comic Neue)와 팔레트 구성 자체(러닝블루+플레이옐로우+펀핑크)는 그대로 유지. 아이콘은 이모지 대신 Phosphor Icons(`@phosphor-icons/web` CDN, `<i class="ph ph-*">`)를 쓴다 (스킬의 "No emojis as icons" 체크리스트 반영)
  - 이전 라운드(hallmark 스킬 기반 Hum 테마)는 전체 교체됨 — hallmark는 이 세션에서 `Skill` 도구로 호출이 안 돼(새로 설치된 프로젝트 스킬이라 인식 실패) SKILL.md를 직접 읽어 수동 적용했었고, `ui-ux-pro-max`는 플러그인이라 `Skill` 도구로 정상 호출되어 `search.py --design-system`으로 토큰을 받아왔다
- Playwright로 시각 검증 완료 (모바일 390px·태블릿 820px), CSS 명시도 버그(ID 셀렉터가 `.is-hidden`을 덮어씀) 한 건 발견해 수정함

## 주고받는 형식 (제출 규약)
- POST /query 로 받고 question 필드를 읽는다 (예: "오늘 수학 학습지 만들어줘", "이번 주 결과 보여줘")
- 답은 answer, contexts, trace 세 키로 돌려준다
  - answer: 아이·부모에게 보여줄 응답 (생성된 문제, 채점 결과, 주간 리포트 등)
  - contexts: 답변에 사용한 참고자료 (교과과정 기준, 한자 급수표, 문제은행 항목 등)
  - trace: 어떤 도구를 어떤 순서로 호출했는지 기록 (문제 생성 → 오류 교차검증 → 채점 등)

## 코드 규칙
- 파일 하나에 한 가지 역할만 둔다
- 함수와 도구에는 한국어 docstring 을 쓴다
- 비밀 값은 .env 에서 읽고 코드에 적지 않는다

## 하지 말 것
- 요청하지 않은 파일을 새로 만들지 않는다
- 기존 파일을 통째로 다시 쓰지 않는다. 바뀐 부분만 고친다
