(() => {
  "use strict";

  const GRADES = [1, 2, 3, 4, 5, 6];
  const ENGLISH_MIN_GRADE = 3; // SERVICE.md 4번: 영어는 3~6학년만 (1~2학년은 정규 영어 교과과정이 없음)

  const screens = {
    grade: document.getElementById("screen-grade"),
    subject: document.getElementById("screen-subject"),
    loading: document.getElementById("screen-loading"),
    worksheet: document.getElementById("screen-worksheet"),
    parent: document.getElementById("screen-parent"),
    report: document.getElementById("screen-report"),
    wrongAnswers: document.getElementById("screen-wrong-answers"),
  };

  const PARENT_PIN = "0000"; // 가정용 기본값 — 실제 인증이 아니라 아이가 우연히 못 들어오게 막는 정도
  const PREFERRED_DIFFICULTY_KEY = "preferredDifficulty"; // 부모가 리포트에서 조정한 난이도 (이 기기에만 저장)

  const parentToggleBtn = document.getElementById("parent-toggle-btn");
  const parentToggleIcon = document.getElementById("parent-toggle-icon");
  const parentToggleLabel = document.getElementById("parent-toggle-label");
  const parentBackBtn = document.getElementById("parent-back-btn");
  const pendingList = document.getElementById("pending-list");
  const pendingEmpty = document.getElementById("pending-empty");
  const pendingRefreshBtn = document.getElementById("pending-refresh-btn");

  const reportOpenBtn = document.getElementById("report-open-btn");
  const reportBackBtn = document.getElementById("report-back-btn");
  const reportRefreshBtn = document.getElementById("report-refresh-btn");
  const reportRange = document.getElementById("report-range");
  const reportWeekSelect = document.getElementById("report-week-select");
  const reportOverviewEl = document.getElementById("report-overview");
  const reportBySubjectEl = document.getElementById("report-by-subject");
  const subjectReportTemplate = document.getElementById("subject-report-template");
  const currentDifficultyLabel = document.getElementById("current-difficulty-label");
  const reportDifficultyRow = document.getElementById("report-difficulty-row");
  const difficultyAdjustConfirm = document.getElementById("difficulty-adjust-confirm");

  const wrongAnswersOpenBtn = document.getElementById("wrong-answers-open-btn");
  const wrongAnswersBackBtn = document.getElementById("wrong-answers-back-btn");
  const wrongAnswersRefreshBtn = document.getElementById("wrong-answers-refresh-btn");
  const wrongAnswersTabs = document.getElementById("wrong-answers-tabs");
  let activeWrongAnswersSubject = "math";

  const pinModal = document.getElementById("pin-modal");
  const pinInput = document.getElementById("pin-input");
  const pinError = document.getElementById("pin-error");
  const pinSubmitBtn = document.getElementById("pin-submit");
  const pinCancelBtn = document.getElementById("pin-cancel");

  const gradeGrid = document.getElementById("grade-grid");
  const subjectScreenGradeLabel = document.getElementById("subject-screen-grade-label");
  const subjectRow = document.getElementById("subject-row");
  const subjectAllDoneHelp = document.getElementById("subject-all-done-help");
  const subjectBackBtn = document.getElementById("subject-back-btn");
  const subjectSubmitBtn = document.getElementById("subject-submit-btn");
  const difficultyRow = document.getElementById("difficulty-row");
  const worksheetMeta = document.getElementById("worksheet-meta");
  const worksheetTabs = document.getElementById("worksheet-tabs");
  const restartBtns = document.querySelectorAll(".restart-btn"); // 패널마다 하나씩 있음 (둘 다 같은 전체 게이트를 검사)

  // 과목별 패널(수학/영어를 동시에 골랐을 때 탭으로 전환) — 패널마다 자기 문제 목록·
  // 채점 버튼·점수 배너를 따로 갖는다.
  const PANELS = {
    math: { panel: document.getElementById("panel-math"), list: document.getElementById("problem-list-math") },
    english: { panel: document.getElementById("panel-english"), list: document.getElementById("problem-list-english") },
  };
  Object.values(PANELS).forEach((refs) => {
    refs.gradeBtn = refs.panel.querySelector(".grade-btn");
    refs.banner = refs.panel.querySelector(".score-banner");
    refs.scoreValue = refs.banner.querySelector(".score-value");
    refs.scoreMax = refs.banner.querySelector(".score-max");
    refs.scoreCaption = refs.banner.querySelector(".score-caption");
    refs.scoreTodayStarsValue = refs.banner.querySelector(".score-today-stars-value");
  });

  const scratchpadModal = document.getElementById("scratchpad-modal");
  const scratchpadTitle = document.getElementById("scratchpad-title");
  const scratchpadCanvas = document.getElementById("scratchpad-canvas");
  const scratchpadClearBtn = document.getElementById("scratchpad-clear");
  const scratchpadCloseBtn = document.getElementById("scratchpad-close");
  const padCtx = scratchpadCanvas.getContext("2d");

  let selectedDifficulty = "표준";
  /** @type {string[]} 과목 선택 화면에서 다중 선택된 과목들 (예: ["math","english"]) */
  let selectedSubjects = [];
  /** @type {string[]} 이번 회차에 실제로 생성된 과목들 (탭이 이 목록 기준으로 보임/안 보임 결정) */
  let activeSubjects = [];
  let activeTab = "math";
  /** @type {Record<string, {problems: object[], attemptId: string|null}|null>} 과목별 학습지 데이터 */
  let worksheets = { math: null, english: null };
  let currentGrade = 3;
  /** @type {Record<number, string>} 문제 index -> 캔버스 dataURL (연습장 낙서 보관용, 수학 전용) */
  let scratchpadData = {};
  let currentPadIndex = null;
  let isDrawing = false;
  let lastPoint = null;
  let padHasContent = false;
  let screenBeforeParent = "grade";
  let pendingPollTimer = null;

  // 오늘 과목별로 학습지를 다 풀었는지 (SERVICE.md 4번: 과목 통합 게이트 — 그날 지원되는
  // 모든 과목을 다 받아서 다 풀기 전엔 새 학습지를 못 받는다). 서버 강제는 없는 프론트 UX
  // 가드라 새로고침하면 초기화된다 — 날짜가 바뀌면(자정) 로컬 자정 기준으로 다시 초기화한다.
  let sessionDate = new Date().toDateString();
  let subjectDone = { math: false, english: false };

  function checkDayRollover() {
    const today = new Date().toDateString();
    if (today !== sessionDate) {
      sessionDate = today;
      subjectDone = { math: false, english: false };
    }
  }

  function subjectLabel(subject) {
    return subject === "english" ? "영어" : "수학";
  }

  const topbarDateEl = document.getElementById("topbar-date");
  const WEEKDAY_LABELS = ["일", "월", "화", "수", "목", "금", "토"];

  /** 아이 화면 상단에 오늘 날짜·요일을 보여준다 (예: "9월 17일 목요일"). */
  function renderTopbarDate() {
    if (!topbarDateEl) return;
    const now = new Date();
    topbarDateEl.textContent = `${now.getMonth() + 1}월 ${now.getDate()}일 ${WEEKDAY_LABELS[now.getDay()]}요일`;
  }

  // ── 점수(별) — SERVICE.md 4번: 힌트 없이 정답 2개, 힌트로 정답 1개, 오답 0개 ────
  const todayStarsValueEl = document.getElementById("today-stars-value");
  const gradeStarBreakdown = document.getElementById("grade-star-breakdown");
  const reportStarBreakdown = document.getElementById("report-star-breakdown");
  const STAR_TIERS = [1000, 100, 10, 1];

  function updateTodayStars(todayStars) {
    if (todayStarsValueEl) todayStarsValueEl.textContent = String(todayStars);
  }

  /** 누적 별 수를 1/10/100/1000 단위로 쪼개 "단위 아이콘 × 개수"로 그린다 (단위가 클수록 크고 반짝임). */
  function renderStarBreakdown(container, total) {
    if (!container) return;
    container.innerHTML = "";
    if (!total) {
      container.innerHTML = `<span class="star-empty">아직 모은 별이 없어요. 문제를 풀고 별을 모아보자!</span>`;
      return;
    }
    let remaining = total;
    STAR_TIERS.forEach((tier) => {
      const count = Math.floor(remaining / tier);
      remaining -= count * tier;
      if (count <= 0) return;
      const group = document.createElement("span");
      group.className = `star-group star-tier-${tier}`;
      group.setAttribute("aria-label", `${tier}개 단위 별 ${count}개`);
      group.innerHTML = `
        <span class="star-icon-wrap">
          <i class="ph-fill ph-star" aria-hidden="true"></i>
          <span class="star-unit-label">${tier}</span>
        </span>
        <span class="star-count">×${count}</span>
      `;
      container.appendChild(group);
    });
  }

  async function fetchStars() {
    try {
      const res = await fetch("/stars");
      const data = await res.json();
      updateTodayStars(data.today_stars || 0);
      renderStarBreakdown(gradeStarBreakdown, data.total_stars || 0);
    } catch {
      /* 별 정보를 못 가져와도 학습지 풀이 자체는 계속 할 수 있어야 하므로 조용히 무시 */
    }
  }

  // 부모 화면(승인 대기 목록)·주간 리포트·오답노트 셋 다 "부모 쪽" 화면으로 취급한다 —
  // 상단 토글 버튼이 이 화면들에서는 "아이 화면"으로 바뀌어 바로 아이 화면으로 돌아간다.
  function isParentSideScreen(name) {
    return name === "parent" || name === "report" || name === "wrongAnswers";
  }

  function syncParentToggleBtn(name) {
    const inParentSide = isParentSideScreen(name);
    parentToggleIcon.className = inParentSide ? "ph ph-smiley" : "ph ph-users-three";
    parentToggleLabel.textContent = inParentSide ? "아이 화면" : "부모 화면";
  }

  function showScreen(name) {
    for (const key of Object.keys(screens)) {
      screens[key].classList.toggle("is-hidden", key !== name);
    }
    syncParentToggleBtn(name);
  }

  function getCurrentScreenName() {
    return Object.keys(screens).find((key) => !screens[key].classList.contains("is-hidden")) || "grade";
  }

  function buildGradeTiles() {
    for (const grade of GRADES) {
      const btn = document.createElement("button");
      btn.className = "grade-tile";
      btn.type = "button";
      btn.innerHTML = `${grade}<span>학년</span>`;
      btn.addEventListener("click", () => openSubjectScreen(grade));
      gradeGrid.appendChild(btn);
    }
  }

  // ── 학년 다음 화면: 과목·난이도 선택 ──────────────────────────────────
  function openSubjectScreen(grade) {
    checkDayRollover();
    currentGrade = grade;
    subjectScreenGradeLabel.textContent = `${grade}학년`;

    const mathTile = subjectRow.querySelector('[data-subject="math"]');
    const englishTile = subjectRow.querySelector('[data-subject="english"]');
    englishTile.classList.toggle("is-hidden", grade < ENGLISH_MIN_GRADE);
    mathTile.disabled = subjectDone.math;
    englishTile.disabled = subjectDone.english;

    const eligibleSubjects = grade < ENGLISH_MIN_GRADE ? ["math"] : ["math", "english"];
    const allDone = eligibleSubjects.every((s) => subjectDone[s]);
    subjectAllDoneHelp.classList.toggle("is-hidden", !allDone);
    subjectSubmitBtn.disabled = true;
    selectedSubjects = [];
    subjectRow.querySelectorAll(".subject-tile").forEach((t) => {
      t.classList.remove("is-selected");
      t.setAttribute("aria-pressed", "false");
    });

    showScreen("subject");
  }

  // 과목은 여러 개 동시에 고를 수 있다 (토글) — 고른 순서와 무관하게 항상 수학을 먼저,
  // 영어를 나중에 생성한다 (SUBJECT_ORDER).
  const SUBJECT_ORDER = ["math", "english"];

  function bindSubjectTiles() {
    subjectRow.querySelectorAll(".subject-tile").forEach((tile) => {
      tile.addEventListener("click", () => {
        if (tile.disabled) return;
        const subject = tile.dataset.subject;
        const isSelected = tile.classList.toggle("is-selected");
        tile.setAttribute("aria-pressed", String(isSelected));
        if (isSelected) {
          selectedSubjects.push(subject);
        } else {
          selectedSubjects = selectedSubjects.filter((s) => s !== subject);
        }
        subjectSubmitBtn.disabled = selectedSubjects.length === 0;
      });
    });
  }

  function bindDifficultyChips() {
    const chips = difficultyRow.querySelectorAll(".chip");
    let preferred = null;
    try {
      preferred = localStorage.getItem(PREFERRED_DIFFICULTY_KEY);
    } catch {
      preferred = null;
    }
    chips.forEach((chip) => {
      const isPreferred = preferred ? chip.dataset.difficulty === preferred : chip.hasAttribute("data-default");
      chip.classList.toggle("is-active", isPreferred);
      if (isPreferred) selectedDifficulty = chip.dataset.difficulty;
      chip.addEventListener("click", () => {
        chips.forEach((c) => c.classList.remove("is-active"));
        chip.classList.add("is-active");
        selectedDifficulty = chip.dataset.difficulty;
      });
    });
  }

  /** 고른 과목들을 순서대로(수학→영어) 전부 생성한 뒤, 과목별 패널+탭으로 한 화면에 보여준다. */
  async function requestWorksheets(grade, subjects, difficulty) {
    showScreen("loading");
    const results = {};
    try {
      for (const subject of subjects) {
        const question = `${grade}학년 ${subjectLabel(subject)} 학습지 만들어줘 (난이도: ${difficulty})`;
        const res = await fetch("/query", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question }),
        });
        if (!res.ok) throw new Error(`서버 오류 (${res.status})`);
        const data = await res.json();
        const problems = Array.isArray(data.problems) ? data.problems : [];
        if (problems.length === 0) {
          throw new Error(`${subjectLabel(subject)} 학습지를 만들지 못했어요.\n\n` + (data.answer || ""));
        }
        results[subject] = { problems, attemptId: data.attempt_id || null };
      }
    } catch (err) {
      alert("학습지를 가져오는 중 문제가 생겼어요: " + err.message);
      openSubjectScreen(grade);
      return;
    }

    currentGrade = grade;
    activeSubjects = subjects;
    worksheets = { math: results.math || null, english: results.english || null };
    worksheetMeta.textContent = `${grade}학년 · ${difficulty}`;

    subjects.forEach((subject) => renderWorksheetPanel(subject, worksheets[subject].problems));
    worksheetTabs.classList.toggle("is-hidden", subjects.length < 2);
    Object.entries(PANELS).forEach(([subject, refs]) => {
      refs.panel.classList.toggle("is-hidden", !subjects.includes(subject));
    });
    setActiveTab(subjects[0]);

    showScreen("worksheet");
  }

  /** 과목 탭을 전환한다 (패널은 그대로 DOM에 남아있어 입력한 답은 유지된다). */
  function setActiveTab(subject) {
    activeTab = subject;
    Object.entries(PANELS).forEach(([s, refs]) => {
      if (activeSubjects.includes(s)) {
        refs.panel.classList.toggle("is-hidden", s !== subject);
      }
    });
    worksheetTabs.querySelectorAll(".worksheet-tab").forEach((tab) => {
      tab.classList.toggle("is-active", tab.dataset.subject === subject);
    });
  }

  function renderWorksheetPanel(subject, problems) {
    const isEnglish = subject === "english";
    const refs = PANELS[subject];
    refs.list.innerHTML = "";
    refs.gradeBtn.disabled = false;
    refs.gradeBtn.querySelector(".btn__label").textContent = "채점하기";
    refs.banner.classList.add("is-hidden");
    if (subject === "math") scratchpadData = {};

    problems.forEach((problem, index) => {
      const li = document.createElement("li");
      li.className = "problem-card";
      li.dataset.index = String(index);
      li.dataset.hintUsed = "false";

      // 영어(4지선다)는 보기를 질문 바로 아래 한 줄로 두는 게 가독성이 좋아서
      // question-block 안에 중첩한다 (수학의 <input>은 질문과 같은 줄, 카드 오른쪽에 둔다)
      const choiceGroupHtml = isEnglish
        ? `<div class="choice-group" role="radiogroup" aria-label="${index + 1}번 문제 보기">
            ${(problem.choices || [])
              .map((c) => `<button type="button" class="choice-btn" data-choice="${escapeHtml(c)}">${escapeHtml(c)}</button>`)
              .join("")}
          </div>`
        : "";
      const answerControl = isEnglish
        ? ""
        : `<input type="text" inputmode="text" autocomplete="off" aria-label="${index + 1}번 문제 답" />`;
      const padButton = isEnglish
        ? ""
        : `<button type="button" class="pad-btn"><i class="ph ph-pencil-simple" aria-hidden="true"></i> 연습장</button>`;

      li.innerHTML = `
        <span class="num">${index + 1}</span>
        <div class="question-block">
          <span class="question">${escapeHtml(problem.question)}</span>
          <div class="question-actions">
            <button type="button" class="hint-btn"><i class="ph ph-lightbulb" aria-hidden="true"></i> 힌트 보기</button>
            ${padButton}
          </div>
          <span class="hint-text is-hidden"></span>
          ${choiceGroupHtml}
        </div>
        ${answerControl}
        <span class="feedback"></span>
      `;
      li.querySelector(".hint-btn").addEventListener("click", (e) => showHint(subject, li, problem, e.currentTarget));
      if (isEnglish) {
        li.querySelectorAll(".choice-btn").forEach((btn) => {
          btn.addEventListener("click", () => {
            li.querySelectorAll(".choice-btn").forEach((b) => b.classList.remove("is-selected"));
            btn.classList.add("is-selected");
          });
        });
      } else {
        li.querySelector(".pad-btn").addEventListener("click", () => openScratchpad(index));
      }
      refs.list.appendChild(li);
    });
  }

  async function showHint(subject, card, problem, btn) {
    btn.disabled = true;
    btn.innerHTML = `<i class="ph ph-circle-notch ph-spin" aria-hidden="true"></i> 힌트 불러오는 중...`;
    const hintEl = card.querySelector(".hint-text");

    try {
      const body = {
        question: problem.question,
        correct_answer: problem.answer,
        grade: currentGrade,
        subject,
      };
      if (subject === "english") body.choices = problem.choices || [];
      const res = await fetch("/hint", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`서버 오류 (${res.status})`);
      const data = await res.json();

      card.dataset.hintUsed = "true";
      card.classList.add("hint-used");
      hintEl.textContent = `힌트: ${data.hint}`;
      hintEl.classList.remove("is-hidden");
      btn.innerHTML = `<i class="ph-fill ph-lightbulb" aria-hidden="true"></i> 힌트를 봤어요`;
    } catch (err) {
      btn.disabled = false;
      btn.innerHTML = `<i class="ph ph-lightbulb" aria-hidden="true"></i> 힌트 보기`;
      alert("힌트를 가져오지 못했어요: " + err.message);
    }
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  async function gradeSubjectPanel(subject) {
    const refs = PANELS[subject];
    const isEnglish = subject === "english";
    refs.gradeBtn.disabled = true;
    refs.gradeBtn.querySelector(".btn__label").textContent = "채점 중...";

    const cards = Array.from(refs.list.querySelectorAll(".problem-card"));
    const { problems, attemptId } = worksheets[subject];
    let totalScore = 0;
    let latestTodayStars = null;

    for (const card of cards) {
      const index = Number(card.dataset.index);
      const problem = problems[index];
      const feedbackEl = card.querySelector(".feedback");
      const usedHint = card.dataset.hintUsed === "true";
      let childAnswer;
      if (isEnglish) {
        const selected = card.querySelector(".choice-btn.is-selected");
        childAnswer = selected ? selected.dataset.choice : "";
      } else {
        childAnswer = card.querySelector("input").value.trim();
      }

      card.classList.remove("is-correct", "is-incorrect");

      if (!childAnswer) {
        feedbackEl.textContent = "—";
        continue;
      }

      try {
        const body = {
          question: problem.question,
          correct_answer: problem.answer,
          child_answer: childAnswer,
          used_hint: usedHint,
          attempt_id: attemptId,
          problem_index: index,
          topic: problem.topic || "",
          subject,
        };
        if (isEnglish) body.choices = problem.choices || [];
        const res = await fetch("/grade", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const result = await res.json();
        totalScore += result.score || 0;
        card.classList.add(result.is_correct ? "is-correct" : "is-incorrect");
        // SERVICE.md 4번: 채점 하나하나가 끝나는 즉시 "오늘 딴 별 수"를 갱신한다
        if (typeof result.today_stars === "number") {
          updateTodayStars(result.today_stars);
          latestTodayStars = result.today_stars;
        }
        if (typeof result.total_stars === "number") {
          renderStarBreakdown(gradeStarBreakdown, result.total_stars);
        }
        const icon = result.is_correct ? "ph-fill ph-check-circle" : "ph-fill ph-x-circle";
        if (result.is_correct && result.used_hint) {
          feedbackEl.innerHTML = `<i class="${icon}" aria-hidden="true"></i> 0.5점`;
        } else {
          feedbackEl.innerHTML = `<i class="${icon}" aria-hidden="true"></i>`;
        }
      } catch {
        feedbackEl.innerHTML = `<i class="ph ph-question" aria-hidden="true"></i>`;
      }
    }

    showResultForPanel(subject, totalScore, cards.length, latestTodayStars);
    refs.gradeBtn.disabled = false;
    refs.gradeBtn.querySelector(".btn__label").textContent = "다시 채점하기";
  }

  function showResultForPanel(subject, score, max, todayStars) {
    const refs = PANELS[subject];
    refs.scoreValue.textContent = String(Math.round(score * 10) / 10);
    refs.scoreMax.textContent = String(max);
    const ratio = max > 0 ? score / max : 0;
    refs.scoreCaption.textContent =
      ratio >= 0.8 ? "정말 잘했어요! 최고예요 🎉" : ratio >= 0.5 ? "잘하고 있어요, 조금만 더!" : "괜찮아요, 다음엔 더 잘할 수 있어요";
    if (typeof todayStars === "number") refs.scoreTodayStarsValue.textContent = String(todayStars);
    refs.banner.classList.remove("is-hidden");
    if (ratio >= 0.8) fireStarBurst(refs.banner.querySelector(".score-counter"));
  }

  function fireStarBurst(anchorEl) {
    if (!anchorEl) return;
    const rect = anchorEl.getBoundingClientRect();
    const star = document.createElement("div");
    star.className = "star-burst";
    star.style.left = `${rect.left + rect.width / 2 - 11 + window.scrollX}px`;
    star.style.top = `${rect.top - 10 + window.scrollY}px`;
    document.body.appendChild(star);
    star.addEventListener("animationend", () => star.remove());
  }

  function hasUnansweredInPanel(subject) {
    const cards = Array.from(PANELS[subject].list.querySelectorAll(".problem-card"));
    if (subject === "english") {
      return cards.some((card) => !card.querySelector(".choice-btn.is-selected"));
    }
    return cards.some((card) => card.querySelector("input").value.trim() === "");
  }

  function hasAnyUnanswered() {
    return activeSubjects.some((s) => hasUnansweredInPanel(s));
  }

  function resetToGrade() {
    if (activeSubjects.length > 0 && hasAnyUnanswered()) {
      alert("아직 안 쓴 답이 있어요! 모든 문제(과목이 여러 개면 전부)에 답을 쓰고 나서 새 학습지를 받을 수 있어요.");
      return;
    }
    // SERVICE.md 4번: 과목 통합 게이트 — 이번에 생성한 과목들을 전부 다 풀었다고 기록한다.
    activeSubjects.forEach((s) => {
      subjectDone[s] = true;
    });
    activeSubjects = [];
    worksheets = { math: null, english: null };
    openSubjectScreen(currentGrade);
  }

  // ── 연습장 (그림판, 수학 전용) ─────────────────────────────────────────
  function resizeScratchpadCanvas() {
    const rect = scratchpadCanvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    scratchpadCanvas.width = Math.round(rect.width * ratio);
    scratchpadCanvas.height = Math.round(rect.height * ratio);
    padCtx.scale(ratio, ratio);
    padCtx.lineJoin = "round";
    padCtx.lineCap = "round";
    padCtx.lineWidth = 3;
    padCtx.strokeStyle = "oklch(20% 0.012 250)";
  }

  function openScratchpad(index) {
    currentPadIndex = index;
    padHasContent = Boolean(scratchpadData[index]);
    scratchpadTitle.textContent = `${index + 1}번 문제 연습장`;
    scratchpadModal.classList.remove("is-hidden");
    resizeScratchpadCanvas();

    const saved = scratchpadData[index];
    if (saved) {
      const img = new Image();
      img.onload = () => padCtx.drawImage(img, 0, 0, scratchpadCanvas.clientWidth, scratchpadCanvas.clientHeight);
      img.src = saved;
    }
  }

  function closeScratchpad() {
    if (currentPadIndex !== null) {
      const card = PANELS.math.list.querySelector(`.problem-card[data-index="${currentPadIndex}"]`);
      if (padHasContent) {
        scratchpadData[currentPadIndex] = scratchpadCanvas.toDataURL();
        card?.querySelector(".pad-btn")?.classList.add("has-drawing");
      } else {
        delete scratchpadData[currentPadIndex];
        card?.querySelector(".pad-btn")?.classList.remove("has-drawing");
      }
    }
    scratchpadModal.classList.add("is-hidden");
    currentPadIndex = null;
  }

  function clearScratchpad() {
    padCtx.clearRect(0, 0, scratchpadCanvas.width, scratchpadCanvas.height);
    padHasContent = false;
    if (currentPadIndex !== null) {
      delete scratchpadData[currentPadIndex];
      const card = PANELS.math.list.querySelector(`.problem-card[data-index="${currentPadIndex}"]`);
      card?.querySelector(".pad-btn")?.classList.remove("has-drawing");
    }
  }

  function pointFromEvent(e) {
    const rect = scratchpadCanvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  scratchpadCanvas.addEventListener("pointerdown", (e) => {
    isDrawing = true;
    lastPoint = pointFromEvent(e);
    scratchpadCanvas.setPointerCapture(e.pointerId);
  });
  scratchpadCanvas.addEventListener("pointermove", (e) => {
    if (!isDrawing) return;
    const point = pointFromEvent(e);
    padCtx.beginPath();
    padCtx.moveTo(lastPoint.x, lastPoint.y);
    padCtx.lineTo(point.x, point.y);
    padCtx.stroke();
    lastPoint = point;
    padHasContent = true;
  });
  ["pointerup", "pointerleave", "pointercancel"].forEach((evt) =>
    scratchpadCanvas.addEventListener(evt, () => {
      isDrawing = false;
      lastPoint = null;
    })
  );

  scratchpadClearBtn.addEventListener("click", clearScratchpad);
  scratchpadCloseBtn.addEventListener("click", closeScratchpad);
  scratchpadModal.addEventListener("click", (e) => {
    if (e.target === scratchpadModal) closeScratchpad();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !scratchpadModal.classList.contains("is-hidden")) closeScratchpad();
  });

  // ── 부모 화면 (PIN 토글) ─────────────────────────────────────────────
  function openPinModal() {
    pinInput.value = "";
    pinError.classList.add("is-hidden");
    pinModal.classList.remove("is-hidden");
    pinInput.focus();
  }

  function closePinModal() {
    pinModal.classList.add("is-hidden");
  }

  function submitPin() {
    if (pinInput.value === PARENT_PIN) {
      closePinModal();
      screenBeforeParent = getCurrentScreenName();
      showScreen("parent");
      startPendingPolling();
    } else {
      pinError.classList.remove("is-hidden");
      pinInput.value = "";
      pinInput.focus();
    }
  }

  function backToChildScreen() {
    stopPendingPolling();
    showScreen(screenBeforeParent === "parent" ? "grade" : screenBeforeParent);
  }

  function startPendingPolling() {
    fetchPending();
    stopPendingPolling();
    pendingPollTimer = setInterval(fetchPending, 3000);
  }

  function stopPendingPolling() {
    if (pendingPollTimer) {
      clearInterval(pendingPollTimer);
      pendingPollTimer = null;
    }
  }

  async function fetchPending() {
    try {
      const res = await fetch("/pending-approvals");
      const data = await res.json();
      renderPending(data);
    } catch {
      pendingList.innerHTML = "";
      pendingEmpty.textContent = "승인 목록을 가져오지 못했어요.";
      pendingEmpty.classList.remove("is-hidden");
    }
  }

  function renderPending(data) {
    const entries = Object.entries(data);
    pendingList.innerHTML = "";
    pendingEmpty.classList.toggle("is-hidden", entries.length > 0);
    pendingEmpty.textContent = "지금은 승인 대기 중인 요청이 없어요.";

    entries.forEach(([threadId, info]) => {
      const li = document.createElement("li");
      li.className = "pending-item";
      li.innerHTML = `
        <p class="pending-reason">${escapeHtml(info.reason || "문제은행 대체가 필요합니다.")}</p>
        <p class="pending-meta">${escapeHtml(subjectLabel(info.subject || "math"))} · ${escapeHtml(String(info.grade ?? ""))}학년 · ${escapeHtml(info.difficulty ?? "")} · ${escapeHtml(String(info.timeout_sec ?? ""))}초 내 무응답 시 자동 대체</p>
        <button type="button" class="btn btn--primary btn--sm approve-btn">지금 승인하기</button>
      `;
      li.querySelector(".approve-btn").addEventListener("click", async (e) => {
        e.currentTarget.disabled = true;
        e.currentTarget.textContent = "승인 중...";
        try {
          await fetch("/approve", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ thread_id: threadId }),
          });
        } finally {
          fetchPending();
        }
      });
      pendingList.appendChild(li);
    });
  }

  // ── 주간 리포트 ────────────────────────────────────────────────────────
  const WEEK_SELECT_OPTIONS = 9; // 이번 주 + 지난 8주

  function buildWeekSelectOptions() {
    reportWeekSelect.innerHTML = "";
    for (let weeksAgo = 0; weeksAgo < WEEK_SELECT_OPTIONS; weeksAgo++) {
      const opt = document.createElement("option");
      opt.value = String(weeksAgo);
      opt.textContent = weeksAgo === 0 ? "이번 주" : `${weeksAgo}주 전`;
      reportWeekSelect.appendChild(opt);
    }
  }

  function openReport() {
    showScreen("report");
    reportWeekSelect.value = "0"; // 리포트 화면을 열 때마다 이번 주부터 보여준다
    fetchReport();
  }

  function closeReport() {
    showScreen("parent");
  }

  async function fetchReport() {
    try {
      const weeksAgo = Number(reportWeekSelect.value || 0);
      const res = await fetch(`/report/weekly?weeks_ago=${weeksAgo}`);
      const data = await res.json();
      renderReport(data);
    } catch (err) {
      reportRange.textContent = "리포트를 가져오지 못했어요: " + err.message;
    }
  }

  function pct(ratio) {
    return ratio === null || ratio === undefined ? "–" : `${Math.round(ratio * 100)}%`;
  }

  function renderReport(data) {
    reportRange.textContent = `${data.range_start} ~ ${data.range_end}`;
    renderStarBreakdown(reportStarBreakdown, data.total_stars || 0);
    renderOverview(data.overview || []);
    renderBySubject(data.by_subject || {});

    currentDifficultyLabel.textContent = data.current_difficulty || "표준";
    const chips = reportDifficultyRow.querySelectorAll(".chip");
    chips.forEach((chip) => chip.classList.toggle("is-active", chip.dataset.difficulty === data.current_difficulty));
    difficultyAdjustConfirm.classList.add("is-hidden");
  }

  function renderOverview(overview) {
    reportOverviewEl.innerHTML = "";
    overview.forEach((item) => {
      const card = document.createElement("div");
      card.className = "overview-card";
      const subjectEl = document.createElement("p");
      subjectEl.className = "overview-subject";
      subjectEl.textContent = subjectLabel(item.subject);
      const statsEl = document.createElement("div");
      statsEl.className = "overview-stats";
      statsEl.innerHTML = `
        <span>사용 ${item.days_used}/7일</span>
        <span>정답률 ${pct(item.accuracy_rate)}</span>
        <span>학습지 ${item.attempts_count}개</span>
        <span>힌트 ${pct(item.hint_rate)}</span>
      `;
      card.append(subjectEl, statsEl);
      reportOverviewEl.appendChild(card);
    });
  }

  function renderBySubject(bySubject) {
    reportBySubjectEl.innerHTML = "";
    Object.entries(bySubject).forEach(([subject, report]) => {
      const fragment = subjectReportTemplate.content.cloneNode(true);
      const root = fragment.querySelector(".subject-report-block");
      root.querySelector(".subject-report-title").textContent = `${subjectLabel(subject)} 상세`;

      const svg = root.querySelector(".daily-chart");
      const readout = root.querySelector(".chart-readout");
      const tableToggle = root.querySelector(".chart-table-toggle");
      const table = root.querySelector(".chart-table");
      const tableBody = table.querySelector("tbody");
      const wrongList = root.querySelector(".wrong-topic-list");
      const wrongEmpty = root.querySelector(".wrong-topic-empty");

      renderDailyChart(report.daily || [], { svg, readout, table, tableBody });
      tableToggle.addEventListener("click", () => {
        const showingTable = !table.classList.contains("is-hidden");
        table.classList.toggle("is-hidden", showingTable);
        svg.classList.toggle("is-hidden", !showingTable);
        tableToggle.textContent = showingTable ? "표로 보기" : "차트로 보기";
      });

      wrongList.innerHTML = "";
      const topics = report.wrong_topics || [];
      wrongEmpty.classList.toggle("is-hidden", topics.length > 0);
      topics.forEach((t) => {
        const li = document.createElement("li");
        li.className = "wrong-topic-item";
        li.innerHTML = `${escapeHtml(t.topic)} <span class="count">×${t.count}</span>`;
        wrongList.appendChild(li);
      });

      reportBySubjectEl.appendChild(root);
    });
  }

  const CHART_TOP = 10;
  const CHART_BASE = 120;
  const CHART_LEFT = 12;
  const CHART_RIGHT = 292;

  function roundedTopBarPath(x, y, width, height, radius) {
    const r = Math.min(radius, height);
    const bottom = y + height;
    if (r <= 0) {
      return `M${x},${bottom} L${x},${y} L${x + width},${y} L${x + width},${bottom} Z`;
    }
    return `M${x},${bottom} L${x},${y + r} Q${x},${y} ${x + r},${y} L${x + width - r},${y} Q${x + width},${y} ${x + width},${y + r} L${x + width},${bottom} Z`;
  }

  const svgns = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs) {
    const el = document.createElementNS(svgns, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v));
    return el;
  }

  const TOOLTIP_HEIGHT = 16;
  const TOOLTIP_PAD_X = 8;
  const TOOLTIP_CHAR_WIDTH = 4.6; // 7.5px SVG 폰트 기준 대략치, 정밀 측정(getBBox) 대신 근사

  /** 과목별로 복제된 차트 DOM 조각(svg/readout/table/tableBody)에 그려 넣는다. */
  function renderDailyChart(daily, { svg, readout, table, tableBody }) {
    svg.innerHTML = "";
    tableBody.innerHTML = "";
    readout.textContent = "막대를 누르면 그날의 정답률을 볼 수 있어요.";

    const n = daily.length || 7;
    const slotWidth = (CHART_RIGHT - CHART_LEFT) / n;
    const barWidth = Math.min(24, slotWidth * 0.55);

    // 막대 바로 위에 뜨는 미니 툴팁 (아래 readout과 별개, 장식용이라 스크린리더에는 안 읽힘)
    const tooltipGroup = svgEl("g", { class: "chart-tooltip", "aria-hidden": "true", style: "display:none" });
    const tooltipBg = svgEl("rect", { class: "chart-tooltip-bg", rx: 4, ry: 4, height: TOOLTIP_HEIGHT });
    const tooltipText = svgEl("text", { class: "chart-tooltip-text" });
    tooltipGroup.append(tooltipBg, tooltipText);

    const showTooltip = (centerX, topY, text) => {
      const boxWidth = text.length * TOOLTIP_CHAR_WIDTH + TOOLTIP_PAD_X * 2;
      const boxX = Math.max(2, Math.min(centerX - boxWidth / 2, 300 - boxWidth - 2));
      const boxY = Math.max(2, topY - TOOLTIP_HEIGHT - 6);
      tooltipBg.setAttribute("x", boxX);
      tooltipBg.setAttribute("y", boxY);
      tooltipBg.setAttribute("width", boxWidth);
      tooltipText.setAttribute("x", boxX + boxWidth / 2);
      tooltipText.setAttribute("y", boxY + TOOLTIP_HEIGHT / 2 + 2.6);
      tooltipText.textContent = text;
      tooltipGroup.style.display = "";
    };
    const hideTooltip = () => { tooltipGroup.style.display = "none"; };

    [0, 0.5, 1].forEach((frac) => {
      const y = CHART_BASE - frac * (CHART_BASE - CHART_TOP);
      svg.appendChild(svgEl("line", { class: "chart-gridline", x1: CHART_LEFT, x2: CHART_RIGHT, y1: y, y2: y }));
      const axisLabel = svgEl("text", { class: "chart-axis-label", x: 0, y: y + 2.5 });
      axisLabel.textContent = `${Math.round(frac * 100)}`;
      svg.appendChild(axisLabel);
    });

    daily.forEach((day, i) => {
      const slotCenter = CHART_LEFT + i * slotWidth + slotWidth / 2;
      const x = slotCenter - barWidth / 2;
      const label = (day.date || "").slice(5).replace("-", "/");
      const dayLabel = svgEl("text", { class: "chart-day-label", x: slotCenter, y: 138 });
      dayLabel.textContent = label;
      svg.appendChild(dayLabel);

      let bar;
      let readoutText;
      let tableValue;
      let tooltipTopY;
      let tooltipShortText;
      if (day.accuracy_rate === null || day.accuracy_rate === undefined) {
        bar = svgEl("rect", {
          class: "chart-bar is-empty",
          x, y: CHART_TOP, width: barWidth, height: CHART_BASE - CHART_TOP,
          fill: "transparent",
          tabindex: "0", role: "button",
          "aria-label": `${day.date}: 푼 문제 없음`,
        });
        const dashedMark = svgEl("rect", {
          class: "chart-bar-mark is-empty",
          x, y: CHART_BASE - 4, width: barWidth, height: 4,
        });
        svg.appendChild(dashedMark);
        readoutText = `${day.date}: 그날은 문제를 풀지 않았어요.`;
        tableValue = "–";
        tooltipTopY = CHART_BASE - 4;
        tooltipShortText = `${label} · 문제 없음`;
      } else {
        const height = Math.max(2, day.accuracy_rate * (CHART_BASE - CHART_TOP));
        const path = roundedTopBarPath(x, CHART_BASE - height, barWidth, height, 4);
        bar = svgEl("path", {
          class: "chart-bar",
          d: path,
          tabindex: "0", role: "button",
          "aria-label": `${day.date}: 정답률 ${Math.round(day.accuracy_rate * 100)}%, ${day.problems_graded}문제`,
        });
        readoutText = `${day.date}: 정답률 ${Math.round(day.accuracy_rate * 100)}% (${day.problems_graded}문제 풀이)`;
        tableValue = pct(day.accuracy_rate);
        tooltipTopY = CHART_BASE - height;
        tooltipShortText = `${label} · ${Math.round(day.accuracy_rate * 100)}% (${day.problems_graded}문제)`;
      }
      const showReadout = () => {
        readout.textContent = readoutText;
        showTooltip(slotCenter, tooltipTopY, tooltipShortText);
      };
      bar.addEventListener("pointerenter", showReadout);
      bar.addEventListener("focus", showReadout);
      bar.addEventListener("click", showReadout);
      bar.addEventListener("pointerleave", hideTooltip);
      bar.addEventListener("blur", hideTooltip);
      svg.appendChild(bar);

      const row = document.createElement("tr");
      const dateCell = document.createElement("td");
      dateCell.textContent = day.date;
      const accCell = document.createElement("td");
      accCell.textContent = tableValue;
      const countCell = document.createElement("td");
      countCell.textContent = String(day.problems_graded ?? 0);
      row.append(dateCell, accCell, countCell);
      tableBody.appendChild(row);
    });

    svg.appendChild(tooltipGroup);
  }

  function bindReportDifficultyChips() {
    const chips = reportDifficultyRow.querySelectorAll(".chip");
    chips.forEach((chip) => {
      chip.addEventListener("click", () => {
        chips.forEach((c) => c.classList.remove("is-active"));
        chip.classList.add("is-active");
        try {
          localStorage.setItem(PREFERRED_DIFFICULTY_KEY, chip.dataset.difficulty);
        } catch {
          /* localStorage를 못 쓰는 환경이면 그냥 이번 세션에서만 적용 안 됨 */
        }
        difficultyAdjustConfirm.classList.remove("is-hidden");
      });
    });
  }

  // ── 오답노트 (부모 화면 하위, 과목별 탭 전환·최신순, 아이 화면엔 노출 안 함) ──
  function openWrongAnswers() {
    showScreen("wrongAnswers");
    setActiveWrongAnswersTab("math");
  }

  function closeWrongAnswers() {
    showScreen("parent");
  }

  function setActiveWrongAnswersTab(subject) {
    activeWrongAnswersSubject = subject;
    document.getElementById("wrong-panel-math").classList.toggle("is-hidden", subject !== "math");
    document.getElementById("wrong-panel-english").classList.toggle("is-hidden", subject !== "english");
    wrongAnswersTabs.querySelectorAll(".worksheet-tab").forEach((tab) => {
      tab.classList.toggle("is-active", tab.dataset.subject === subject);
    });
    fetchWrongAnswers(subject);
  }

  async function fetchWrongAnswers(subject) {
    const list = document.getElementById(`wrong-answer-list-${subject}`);
    const empty = document.querySelector(`#wrong-panel-${subject} .wrong-answers-empty`);
    try {
      const res = await fetch(`/wrong-answers?subject=${subject}`);
      const data = await res.json();
      renderWrongAnswerList(list, empty, data.items || []);
    } catch (err) {
      list.innerHTML = "";
      empty.textContent = "오답노트를 가져오지 못했어요: " + err.message;
      empty.classList.remove("is-hidden");
    }
  }

  function renderWrongAnswerList(list, empty, items) {
    list.innerHTML = "";
    empty.classList.toggle("is-hidden", items.length > 0);
    items.forEach((item) => {
      const li = document.createElement("li");
      li.className = "wrong-answer-item";
      const choicesHtml = item.choices && item.choices.length
        ? `<p class="wrong-answer-choices">보기: ${item.choices.map(escapeHtml).join(" / ")}</p>`
        : "";
      const gradeLabel = item.grade ? `${item.grade}학년 · ` : "";
      li.innerHTML = `
        <p class="wrong-answer-meta">${escapeHtml(gradeLabel)}${escapeHtml(item.date || "")}${item.tag ? ` · <span class="wrong-answer-tag">${escapeHtml(item.tag)}</span>` : ""}</p>
        <p class="wrong-answer-question">${escapeHtml(item.question)}</p>
        ${choicesHtml}
        <p class="wrong-answer-compare">
          <span class="wrong-answer-correct"><i class="ph-fill ph-check-circle" aria-hidden="true"></i> 정답: ${escapeHtml(item.correct_answer)}</span>
          <span class="wrong-answer-childanswer"><i class="ph-fill ph-x-circle" aria-hidden="true"></i> 아이 답: ${escapeHtml(item.child_answer)}</span>
        </p>
      `;
      list.appendChild(li);
    });
  }

  parentToggleBtn.addEventListener("click", () => {
    if (isParentSideScreen(getCurrentScreenName())) {
      backToChildScreen();
    } else {
      openPinModal();
    }
  });
  pinCancelBtn.addEventListener("click", closePinModal);
  pinSubmitBtn.addEventListener("click", submitPin);
  pinInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitPin();
  });
  pinModal.addEventListener("click", (e) => {
    if (e.target === pinModal) closePinModal();
  });
  parentBackBtn.addEventListener("click", backToChildScreen);
  pendingRefreshBtn.addEventListener("click", fetchPending);
  reportOpenBtn.addEventListener("click", openReport);
  reportBackBtn.addEventListener("click", closeReport);
  reportRefreshBtn.addEventListener("click", fetchReport);
  reportWeekSelect.addEventListener("change", fetchReport);
  buildWeekSelectOptions();
  bindReportDifficultyChips();

  wrongAnswersOpenBtn.addEventListener("click", openWrongAnswers);
  wrongAnswersBackBtn.addEventListener("click", closeWrongAnswers);
  wrongAnswersRefreshBtn.addEventListener("click", () => fetchWrongAnswers(activeWrongAnswersSubject));
  wrongAnswersTabs.querySelectorAll(".worksheet-tab").forEach((tab) => {
    tab.addEventListener("click", () => setActiveWrongAnswersTab(tab.dataset.subject));
  });

  subjectBackBtn.addEventListener("click", () => showScreen("grade"));
  subjectSubmitBtn.addEventListener("click", () => {
    if (selectedSubjects.length === 0) return;
    const subjects = SUBJECT_ORDER.filter((s) => selectedSubjects.includes(s));
    requestWorksheets(currentGrade, subjects, selectedDifficulty);
  });
  bindSubjectTiles();

  worksheetTabs.querySelectorAll(".worksheet-tab").forEach((tab) => {
    tab.addEventListener("click", () => setActiveTab(tab.dataset.subject));
  });
  Object.entries(PANELS).forEach(([subject, refs]) => {
    refs.gradeBtn.addEventListener("click", () => gradeSubjectPanel(subject));
  });

  renderTopbarDate();
  fetchStars();
  buildGradeTiles();
  bindDifficultyChips();
  restartBtns.forEach((btn) => btn.addEventListener("click", resetToGrade));
})();
