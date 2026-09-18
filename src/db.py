"""저장소 (SQLite 하나, 테이블 4개: attempts · grades · stars · wrong_answers).

학습지 생성·채점 기록, 점수(별) 누적, 오답노트, 부모용 주간 리포트 집계를 담당한다.
LangGraph·FastAPI 어느 쪽에도 의존하지 않는 순수 영속성 계층이다.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

SUBJECTS = ["math", "english", "korean"]

# SERVICE.md 3·4번 오답노트: 과목별로 별도 노트, 노트 하나당 최대 이 개수만 유지한다 (FIFO).
WRONG_ANSWER_LIMIT = 50


def _local_date(ts: float) -> str:
    """자정(로컬 시간) 기준 날짜 문자열 (SERVICE.md 5번: 하루는 자정 기준으로 구분)."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY,
                created_at REAL,
                attempt_date TEXT,
                subject TEXT,
                grade INTEGER,
                difficulty TEXT,
                problems_json TEXT,
                used_fallback INTEGER,
                approval_mode TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS grades (
                id TEXT PRIMARY KEY,
                attempt_id TEXT,
                problem_index INTEGER,
                question TEXT,
                correct_answer TEXT,
                child_answer TEXT,
                is_correct INTEGER,
                score REAL,
                used_hint INTEGER,
                graded_at REAL,
                graded_date TEXT,
                topic TEXT,
                subject TEXT DEFAULT 'math'
            )
            """
        )
        # SERVICE.md 4번: 점수(별) 누적 — 단일 행만 갖는 카운터 (과목 통합, 절대 초기화 안 됨)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stars (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                total_stars INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute("INSERT OR IGNORE INTO stars (id, total_stars) VALUES (1, 0)")
        # SERVICE.md 3·4번: 오답노트 — 채점이 오답일 때마다 한 줄씩 쌓인다 (과목별 최대 50개, FIFO)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wrong_answers (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                grade INTEGER,
                wrong_date TEXT,
                question TEXT,
                choices_json TEXT,
                correct_answer TEXT,
                child_answer TEXT,
                tag TEXT,
                created_at REAL
            )
            """
        )
        _migrate_attempts_columns(conn)
        _migrate_grades_columns(conn)


def _migrate_attempts_columns(conn: sqlite3.Connection) -> None:
    """이전 버전 DB(신규 컬럼 없음)에 attempt_date·subject를 추가한다."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
    if "attempt_date" not in existing:
        conn.execute("ALTER TABLE attempts ADD COLUMN attempt_date TEXT")
    if "subject" not in existing:
        conn.execute("ALTER TABLE attempts ADD COLUMN subject TEXT")


def _migrate_grades_columns(conn: sqlite3.Connection) -> None:
    """이전 버전 DB(신규 컬럼 없음)에 topic·subject를 추가한다.

    topic은 부모 리포트의 오답 주제 표시용, subject는 영어 확장 이후 과목별 리포트
    분리용이다 (기존 행은 전부 수학이었으므로 DEFAULT 'math'로 채운다).
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(grades)")}
    if "topic" not in existing:
        conn.execute("ALTER TABLE grades ADD COLUMN topic TEXT")
    if "subject" not in existing:
        conn.execute("ALTER TABLE grades ADD COLUMN subject TEXT DEFAULT 'math'")


def log_attempt(
    grade: int,
    difficulty: str,
    problems: list[dict],
    used_fallback: bool,
    approval_mode: str | None = None,
    subject: str = "math",
) -> str:
    """학습지 생성 1건을 기록하고, 채점 기록과 연결할 attempt_id를 반환한다."""
    attempt_id = str(uuid.uuid4())
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO attempts
                (id, created_at, attempt_date, subject, grade, difficulty, problems_json, used_fallback, approval_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                now,
                _local_date(now),
                subject,
                grade,
                difficulty,
                json.dumps(problems, ensure_ascii=False),
                int(used_fallback),
                approval_mode,
            ),
        )
    return attempt_id


def stars_for_grade(is_correct: bool, used_hint: bool) -> int:
    """SERVICE.md 4번: 힌트 없이 정답=2개, 힌트로 정답=1개, 오답=0개 (수학·영어 공통, 과목 통합)."""
    if not is_correct:
        return 0
    return 1 if used_hint else 2


def log_grade(
    attempt_id: str | None,
    problem_index: int | None,
    question: str,
    correct_answer: str,
    child_answer: str,
    is_correct: bool,
    score: float,
    used_hint: bool,
    topic: str = "",
    subject: str = "math",
    choices: list[str] | None = None,
) -> int:
    """채점 결과 1건을 기록하고(부모 리포트의 정답률·힌트 비율·오답 주제 계산용),
    이번 문제로 딴 별을 누적 별 수에 더한다 (SERVICE.md 4번). 이번에 딴 별 개수를 반환한다.

    오답이면 그 과목의 오답노트에도 한 줄 남긴다 (SERVICE.md 3·4번).
    """
    now = time.time()
    stars_earned = stars_for_grade(is_correct, used_hint)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO grades
                (id, attempt_id, problem_index, question, correct_answer, child_answer,
                 is_correct, score, used_hint, graded_at, graded_date, topic, subject)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                attempt_id,
                problem_index,
                question,
                correct_answer,
                child_answer,
                int(is_correct),
                score,
                int(used_hint),
                now,
                _local_date(now),
                topic,
                subject,
            ),
        )
        if stars_earned:
            conn.execute("UPDATE stars SET total_stars = total_stars + ? WHERE id = 1", (stars_earned,))
        if not is_correct:
            grade_value = None
            if attempt_id:
                row = conn.execute("SELECT grade FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
                if row:
                    grade_value = row[0]
            _record_wrong_answer(conn, subject, grade_value, question, correct_answer, child_answer, topic, choices)
    return stars_earned


def _record_wrong_answer(
    conn: sqlite3.Connection,
    subject: str,
    grade: int | None,
    question: str,
    correct_answer: str,
    child_answer: str,
    tag: str,
    choices: list[str] | None,
) -> None:
    """오답노트에 한 줄 추가하고, 그 과목 노트가 50개를 넘으면 가장 오래된 것부터 지운다.

    ReAct 자연어 채점 경로(attempt_id=None)는 학년 정보가 없어 grade가 빈 값으로
    남는다 (topic이 문제은행·자연어 채점 경로에서 빈 문자열로 남는 것과 같은 이유).
    같은 문제를 여러 번 틀려도 매번 새 줄로 추가한다(중복 제거 안 함, SERVICE.md 3·4번).
    """
    now = time.time()
    conn.execute(
        """
        INSERT INTO wrong_answers
            (id, subject, grade, wrong_date, question, choices_json, correct_answer, child_answer, tag, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            subject,
            grade,
            _local_date(now),
            question,
            json.dumps(choices, ensure_ascii=False) if choices else None,
            correct_answer,
            child_answer,
            tag,
            now,
        ),
    )
    conn.execute(
        """
        DELETE FROM wrong_answers WHERE subject = ? AND id NOT IN (
            SELECT id FROM wrong_answers WHERE subject = ? ORDER BY created_at DESC LIMIT ?
        )
        """,
        (subject, subject, WRONG_ANSWER_LIMIT),
    )


def get_wrong_answers(subject: str) -> list[dict]:
    """부모 화면 오답노트 조회 (과목별, 최신순, 최대 WRONG_ANSWER_LIMIT개)."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM wrong_answers WHERE subject = ? ORDER BY created_at DESC LIMIT ?",
            (subject, WRONG_ANSWER_LIMIT),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "subject": r["subject"],
            "grade": r["grade"],
            "date": r["wrong_date"],
            "question": r["question"],
            "choices": json.loads(r["choices_json"]) if r["choices_json"] else [],
            "correct_answer": r["correct_answer"],
            "child_answer": r["child_answer"],
            "tag": r["tag"],
        }
        for r in rows
    ]


def get_stars_summary() -> dict:
    """아이 화면 상단바·부모 리포트에 보여줄 별 요약 (오늘 딴 별 수 + 누적 별 수)."""
    today = _local_date(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        total_row = conn.execute("SELECT total_stars FROM stars WHERE id = 1").fetchone()
        today_row = conn.execute(
            """
            SELECT SUM(CASE WHEN is_correct = 1 AND used_hint = 0 THEN 2
                             WHEN is_correct = 1 AND used_hint = 1 THEN 1
                             ELSE 0 END) AS today_stars
            FROM grades WHERE graded_date = ?
            """,
            (today,),
        ).fetchone()
    return {
        "today_stars": today_row["today_stars"] or 0,
        "total_stars": total_row["total_stars"] if total_row else 0,
    }


def _subject_report(conn: sqlite3.Connection, subject: str, days: int, start_str: str, end_str: str) -> dict:
    """한 과목의 리포트 조각(사용일수·정답률·날짜별·오답주제)을 계산한다."""
    days_used = conn.execute(
        "SELECT COUNT(DISTINCT graded_date) AS n FROM grades WHERE subject = ? AND graded_date BETWEEN ? AND ?",
        (subject, start_str, end_str),
    ).fetchone()["n"]

    attempts_count = conn.execute(
        "SELECT COUNT(*) AS n FROM attempts WHERE subject = ? AND attempt_date BETWEEN ? AND ?",
        (subject, start_str, end_str),
    ).fetchone()["n"]

    grade_stats = conn.execute(
        """
        SELECT COUNT(*) AS total, SUM(is_correct) AS correct, SUM(used_hint) AS hints
        FROM grades WHERE subject = ? AND graded_date BETWEEN ? AND ?
        """,
        (subject, start_str, end_str),
    ).fetchone()
    total = grade_stats["total"] or 0
    accuracy_rate = (grade_stats["correct"] or 0) / total if total else None
    hint_rate = (grade_stats["hints"] or 0) / total if total else None

    wrong_topics = conn.execute(
        """
        SELECT topic, COUNT(*) AS cnt FROM grades
        WHERE subject = ? AND graded_date BETWEEN ? AND ? AND is_correct = 0 AND topic != ''
        GROUP BY topic ORDER BY cnt DESC LIMIT 5
        """,
        (subject, start_str, end_str),
    ).fetchall()

    daily_rows = conn.execute(
        """
        SELECT graded_date, COUNT(*) AS total, SUM(is_correct) AS correct
        FROM grades WHERE subject = ? AND graded_date BETWEEN ? AND ?
        GROUP BY graded_date
        """,
        (subject, start_str, end_str),
    ).fetchall()

    start = datetime.strptime(start_str, "%Y-%m-%d")
    daily_map = {r["graded_date"]: r for r in daily_rows}
    daily = []
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        row = daily_map.get(d)
        day_total = row["total"] if row else 0
        daily.append(
            {
                "date": d,
                "accuracy_rate": (row["correct"] / day_total) if row and day_total else None,
                "problems_graded": day_total,
            }
        )

    return {
        "subject": subject,
        "days_used": days_used,
        "attempts_count": attempts_count,
        "problems_graded": total,
        "accuracy_rate": accuracy_rate,
        "hint_rate": hint_rate,
        "daily": daily,
        "wrong_topics": [{"topic": r["topic"], "count": r["cnt"]} for r in wrong_topics],
    }


def weekly_report(days: int = 7, weeks_ago: int = 0) -> dict:
    """N일(기본 7일)간의 부모용 리포트 요약을 계산한다 (SERVICE.md 3번: 주간 리포트).

    attempts·grades 테이블을 그대로 GROUP BY 해서 계산한다 (별도 집계 테이블 없음).
    과목이 여러 개(수학·영어)가 되면서 상단 overview(과목별 요약)와 과목별 상세
    (by_subject)로 나눠서 계산한다 (SERVICE.md 3·5번: 성공기준도 과목별 독립 측정).
    weeks_ago=0이면 오늘 기준 최근 7일(기존과 동일), 1이면 그보다 7일 전 시점을 끝으로 한
    그 이전 7일("지난 주") 식으로 밀려나간다 — 부모 화면의 주 선택 콤보박스용.
    """
    end = datetime.now() - timedelta(days=7 * weeks_ago)
    start = end - timedelta(days=days - 1)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        by_subject = {subj: _subject_report(conn, subj, days, start_str, end_str) for subj in SUBJECTS}
        latest = conn.execute(
            "SELECT grade, difficulty FROM attempts ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

    overview = [
        {
            "subject": subj,
            "days_used": report["days_used"],
            "accuracy_rate": report["accuracy_rate"],
            "hint_rate": report["hint_rate"],
            "attempts_count": report["attempts_count"],
        }
        for subj, report in by_subject.items()
    ]

    return {
        "range_start": start_str,
        "range_end": end_str,
        "overview": overview,
        "by_subject": by_subject,
        "current_grade": latest["grade"] if latest else None,
        "current_difficulty": latest["difficulty"] if latest else None,
        "total_stars": get_stars_summary()["total_stars"],
    }
