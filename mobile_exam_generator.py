# -*- coding: utf-8 -*-
"""
모바일 웹 응시 페이지(정적 OMR 시험지) 생성기
- 학생이 스마트폰(390px 뷰포트 기준)에서 열어 문항별 오디오를 듣고 바로 답을 선택한 뒤 제출하는 정적 웹 시험지입니다.
- 서버 없이 순수 HTML5 + CSS3 + Vanilla JS로 동작하며 GitHub Pages 및 로컬 file:/// 환경에서 100% 동작합니다.
- 기능: 문항별 재생 횟수 제한, 커스텀 오디오 컨트롤러, OMR 선택지, 플로팅 답안 현황, 즉시 자동 채점 및 해설/대본 제공.
"""
import html
import json
import os
import shutil

CIRCLED = "①②③④⑤"
SPK_KO = {"M": "남", "W": "여", "N": "담화", "KO": "안내"}


def build_mobile_exam_html(title, questions, options=None):
    """
    questions: list of question dicts
    options: {
        'playLimit': 2,        # 문항당 최대 재생 횟수 (0: 무제한)
        'timeLimitMin': 0,     # 시험 제한 시간(분, 0: 무제한)
        'showScript': True,    # 채점 후 대본 보기 허용 여부
        'showExplanation': True# 채점 후 해설 보기 허용 여부
    }
    """
    opt = {
        "playLimit": 2,
        "timeLimitMin": 0,
        "showScript": True,
        "showExplanation": True
    }
    if options:
        opt.update(options)

    # JSON 직렬화 전 안전 정제
    clean_questions = []
    for q in questions:
        clean_q = {
            "id": str(q.get("id", "")),
            "num": str(q.get("num", "")),
            "title": str(q.get("title", "")),
            "stem": str(q.get("stem", "")),
            "choices": list(q.get("choices", [])),
            "answer": int(q.get("answer", 0)),
            "explanation": str(q.get("explanation", "")),
            "evidence": list(q.get("evidence", [])),
            "figure_note": str(q.get("figure_note", "")),
            "table": str(q.get("table", "")),
            "audio_file": str(q.get("audio_file", "")),
            "script": list(q.get("script", []))
        }
        clean_questions.append(clean_q)

    data_json = json.dumps({
        "title": title,
        "options": opt,
        "questions": clean_questions
    }, ensure_ascii=False)

    template = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <title>{html.escape(title)} - 모바일 듣기평가</title>
  <style>
    :root {{
      --primary: #2563eb;
      --primary-hover: #1d4ed8;
      --primary-light: #eff6ff;
      --primary-ring: rgba(37, 99, 235, 0.2);
      --success: #16a34a;
      --success-light: #f0fdf4;
      --danger: #dc2626;
      --danger-light: #fef2f2;
      --warning: #ea580c;
      --gray-50: #f8fafc;
      --gray-100: #f1f5f9;
      --gray-200: #e2e8f0;
      --gray-300: #cbd5e1;
      --gray-600: #475569;
      --gray-700: #334155;
      --gray-800: #1e293b;
      --gray-900: #0f172a;
      --radius: 14px;
      --radius-sm: 8px;
      --shadow-sm: 0 1px 3px rgba(0,0,0,0.08);
      --shadow-md: 0 4px 12px rgba(0,0,0,0.08);
      --shadow-lg: 0 10px 25px -5px rgba(0,0,0,0.1), 0 8px 10px -6px rgba(0,0,0,0.1);
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Pretendard", "맑은 고딕", Roboto, sans-serif;
      background-color: #f1f5f9;
      color: var(--gray-800);
      line-height: 1.5;
      padding-bottom: 100px;
    }}

    /* 상단 헤더 */
    .app-header {{
      position: sticky;
      top: 0;
      z-index: 50;
      background: rgba(255, 255, 255, 0.95);
      backdrop-filter: blur(8px);
      border-bottom: 1px solid var(--gray-200);
      padding: 12px 16px;
    }}
    .header-top {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
    }}
    .exam-title {{
      font-size: 16px;
      font-weight: 700;
      color: var(--gray-900);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .exam-progress-badge {{
      background: var(--primary-light);
      color: var(--primary);
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 600;
      white-space: nowrap;
    }}
    .progress-bar-wrap {{
      width: 100%;
      height: 4px;
      background: var(--gray-200);
      border-radius: 2px;
      margin-top: 8px;
      overflow: hidden;
    }}
    .progress-bar {{
      height: 100%;
      width: 0%;
      background: var(--primary);
      transition: width 0.3s ease;
    }}

    /* 컨테이너 */
    .container {{
      max-width: 480px;
      margin: 0 auto;
      padding: 14px 14px 20px;
    }}

    /* 수험자 정보 카드 */
    .student-card {{
      background: #fff;
      border-radius: var(--radius);
      padding: 14px 16px;
      margin-bottom: 14px;
      box-shadow: var(--shadow-sm);
      border: 1px solid var(--gray-200);
      display: flex;
      gap: 10px;
      align-items: center;
    }}
    .student-card input {{
      flex: 1;
      padding: 8px 12px;
      border: 1px solid var(--gray-300);
      border-radius: var(--radius-sm);
      font-size: 14px;
      outline: none;
      transition: border-color 0.2s;
    }}
    .student-card input:focus {{
      border-color: var(--primary);
    }}

    /* 문항 카드 */
    .q-card {{
      background: #fff;
      border-radius: var(--radius);
      padding: 18px 16px;
      margin-bottom: 16px;
      box-shadow: var(--shadow-sm);
      border: 1px solid var(--gray-200);
      transition: border-color 0.2s, box-shadow 0.2s;
    }}
    .q-card.answered {{
      border-color: #93c5fd;
    }}
    .q-card.graded-correct {{
      border-left: 5px solid var(--success);
      background: linear-gradient(to bottom, #f0fdf4 0%, #ffffff 40px);
    }}
    .q-card.graded-wrong {{
      border-left: 5px solid var(--danger);
      background: linear-gradient(to bottom, #fef2f2 0%, #ffffff 40px);
    }}

    .q-header {{
      display: flex;
      align-items: baseline;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .q-num-badge {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 28px;
      height: 28px;
      padding: 0 8px;
      background: var(--gray-800);
      color: #fff;
      font-size: 14px;
      font-weight: 700;
      border-radius: 8px;
    }}
    .q-stem {{
      font-size: 15px;
      font-weight: 600;
      color: var(--gray-900);
      line-height: 1.45;
      flex: 1;
    }}

    /* 오디오 플레이어 */
    .audio-player-box {{
      background: var(--gray-50);
      border: 1px solid var(--gray-200);
      border-radius: var(--radius-sm);
      padding: 10px 12px;
      margin: 12px 0 14px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    .audio-controls {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .btn-play {{
      width: 44px;
      height: 44px;
      border-radius: 50%;
      border: none;
      background: var(--primary);
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 18px;
      cursor: pointer;
      box-shadow: 0 2px 6px rgba(37,99,235,0.3);
      transition: all 0.2s;
      flex-shrink: 0;
    }}
    .btn-play:active {{
      transform: scale(0.94);
    }}
    .btn-play:disabled {{
      background: var(--gray-300);
      box-shadow: none;
      cursor: not-allowed;
    }}
    .audio-info {{
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .audio-time-row {{
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      color: var(--gray-600);
      font-variant-numeric: tabular-nums;
    }}
    .play-limit-badge {{
      font-size: 11px;
      font-weight: 600;
      padding: 2px 6px;
      border-radius: 4px;
      background: var(--gray-200);
      color: var(--gray-700);
    }}
    .play-limit-badge.exhausted {{
      background: #fee2e2;
      color: var(--danger);
    }}
    .audio-seek-bar {{
      width: 100%;
      height: 6px;
      background: var(--gray-200);
      border-radius: 3px;
      overflow: hidden;
      cursor: pointer;
    }}
    .audio-seek-progress {{
      height: 100%;
      width: 0%;
      background: var(--primary);
      border-radius: 3px;
      transition: width 0.1s linear;
    }}

    /* 부가 자료 (그림/표 안내) */
    .figure-note {{
      background: #fffbeb;
      border: 1px dashed #f59e0b;
      color: #b45309;
      font-size: 13px;
      padding: 8px 12px;
      border-radius: 6px;
      margin: 8px 0;
    }}
    .q-table-wrap {{
      overflow-x: auto;
      margin: 10px 0;
    }}
    .q-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    .q-table th, .q-table td {{
      border: 1px solid var(--gray-300);
      padding: 6px 10px;
      text-align: center;
    }}
    .q-table th {{
      background: var(--gray-100);
    }}

    /* 5지선다 OMR 선택지 */
    .choices-list {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin-top: 10px;
    }}
    .choice-btn {{
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 12px 14px;
      border-radius: 10px;
      border: 1.5px solid var(--gray-200);
      background: #fff;
      cursor: pointer;
      transition: all 0.15s ease;
      text-align: left;
      font-size: 14.5px;
      color: var(--gray-800);
    }}
    .choice-btn:active {{
      background: var(--primary-light);
    }}
    .choice-btn.selected {{
      border-color: var(--primary);
      background: var(--primary-light);
      font-weight: 600;
      color: var(--primary);
    }}
    .choice-idx {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 24px;
      height: 24px;
      border-radius: 50%;
      background: var(--gray-100);
      color: var(--gray-600);
      font-weight: 700;
      font-size: 12px;
      flex-shrink: 0;
      transition: all 0.15s;
    }}
    .choice-btn.selected .choice-idx {{
      background: var(--primary);
      color: #fff;
    }}
    .choice-text {{
      flex: 1;
      word-break: break-word;
    }}

    /* 채점 피드백 스타일 */
    .choice-btn.graded-ans {{
      border-color: var(--success) !important;
      background: #dcfce7 !important;
      color: #15803d !important;
      font-weight: 700 !important;
    }}
    .choice-btn.graded-ans .choice-idx {{
      background: var(--success) !important;
      color: #fff !important;
    }}
    .choice-btn.graded-wrong-choice {{
      border-color: var(--danger) !important;
      background: #fee2e2 !important;
      color: #b91c1c !important;
      text-decoration: line-through;
    }}
    .choice-btn.graded-wrong-choice .choice-idx {{
      background: var(--danger) !important;
      color: #fff !important;
    }}

    /* 해설 & 대본 카드 */
    .solution-box {{
      margin-top: 14px;
      padding: 12px;
      background: var(--gray-50);
      border-radius: var(--radius-sm);
      border: 1px solid var(--gray-200);
      font-size: 13px;
      display: none;
    }}
    .solution-box.show {{
      display: block;
    }}
    .sol-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 6px;
    }}
    .sol-title {{
      font-weight: 700;
      color: var(--gray-900);
    }}
    .sol-ans-badge {{
      background: var(--success);
      color: #fff;
      padding: 2px 8px;
      border-radius: 12px;
      font-size: 12px;
      font-weight: 700;
    }}
    .sol-text {{
      color: var(--gray-700);
      line-height: 1.5;
      margin-bottom: 8px;
    }}
    .sol-evidence {{
      background: #fef9c3;
      padding: 6px 8px;
      border-radius: 4px;
      color: #854d0e;
      font-size: 12px;
      margin-bottom: 8px;
    }}
    .script-accordion summary {{
      font-size: 12px;
      font-weight: 600;
      color: var(--primary);
      cursor: pointer;
      user-select: none;
      padding: 4px 0;
    }}
    .script-content {{
      margin-top: 6px;
      padding: 8px;
      background: #fff;
      border-radius: 6px;
      font-size: 12.5px;
      border: 1px solid var(--gray-200);
      line-height: 1.6;
    }}
    .spk-label {{
      font-weight: 700;
      color: var(--primary);
      display: inline-block;
      min-width: 22px;
    }}

    /* 플로팅 제출 바 */
    .floating-bar {{
      position: fixed;
      bottom: 0;
      left: 0;
      right: 0;
      background: rgba(255, 255, 255, 0.95);
      backdrop-filter: blur(8px);
      border-top: 1px solid var(--gray-200);
      padding: 10px 16px calc(10px + env(safe-area-inset-bottom, 0px));
      z-index: 40;
    }}
    .floating-inner {{
      max-width: 480px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}
    .omr-summary {{
      display: flex;
      flex-direction: column;
    }}
    .omr-count {{
      font-size: 13px;
      font-weight: 600;
      color: var(--gray-900);
    }}
    .omr-unanswered {{
      font-size: 11px;
      color: var(--danger);
    }}
    .btn-submit {{
      background: var(--primary);
      color: #fff;
      border: none;
      padding: 12px 24px;
      border-radius: 12px;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
      box-shadow: 0 3px 8px rgba(37,99,235,0.3);
      transition: all 0.2s;
    }}
    .btn-submit:active {{
      transform: scale(0.96);
    }}
    .btn-reset {{
      background: var(--gray-200);
      color: var(--gray-800);
      border: none;
      padding: 12px 20px;
      border-radius: 12px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      display: none;
    }}

    /* 결과 요약 카드 */
    .result-card {{
      background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 100%);
      color: #fff;
      border-radius: var(--radius);
      padding: 24px 20px;
      margin-bottom: 20px;
      box-shadow: var(--shadow-md);
      text-align: center;
      display: none;
    }}
    .result-card.show {{
      display: block;
      animation: popIn 0.3s ease;
    }}
    .result-score-wrap {{
      margin: 10px 0;
    }}
    .result-score {{
      font-size: 46px;
      font-weight: 800;
      letter-spacing: -1px;
    }}
    .result-score-sub {{
      font-size: 15px;
      opacity: 0.9;
    }}
    .result-stats-row {{
      display: flex;
      justify-content: space-around;
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid rgba(255,255,255,0.2);
    }}
    .result-stat-item b {{
      display: block;
      font-size: 18px;
    }}
    .result-stat-item span {{
      font-size: 12px;
      opacity: 0.8;
    }}

    /* 모달 */
    .modal-backdrop {{
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0,0,0,0.5);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 100;
      padding: 20px;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.2s ease;
    }}
    .modal-backdrop.active {{
      opacity: 1;
      pointer-events: auto;
    }}
    .modal-card {{
      background: #fff;
      border-radius: var(--radius);
      padding: 20px;
      width: 100%;
      max-width: 340px;
      box-shadow: var(--shadow-lg);
      text-align: center;
      transform: scale(0.9);
      transition: transform 0.2s ease;
    }}
    .modal-backdrop.active .modal-card {{
      transform: scale(1);
    }}
    .modal-title {{
      font-size: 17px;
      font-weight: 700;
      margin-bottom: 8px;
    }}
    .modal-msg {{
      font-size: 14px;
      color: var(--gray-600);
      margin-bottom: 18px;
      line-height: 1.5;
    }}
    .modal-actions {{
      display: flex;
      gap: 10px;
    }}
    .modal-actions button {{
      flex: 1;
      padding: 10px;
      border-radius: 8px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      border: none;
    }}
    .btn-modal-cancel {{
      background: var(--gray-200);
      color: var(--gray-800);
    }}
    .btn-modal-confirm {{
      background: var(--primary);
      color: #fff;
    }}

    @keyframes popIn {{
      0% {{ opacity: 0; transform: scale(0.95); }}
      100% {{ opacity: 1; transform: scale(1); }}
    }}
  </style>
</head>
<body>

  <!-- 헤더 -->
  <header class="app-header">
    <div class="header-top">
      <div class="exam-title" id="topExamTitle">{html.escape(title)}</div>
      <div class="exam-progress-badge" id="topProgressBadge">0 / 0 문항</div>
    </div>
    <div class="progress-bar-wrap">
      <div class="progress-bar" id="topProgressBar"></div>
    </div>
  </header>

  <!-- 메인 컨테이너 -->
  <main class="container">

    <!-- 수험자 카드 -->
    <section class="student-card" id="studentCard">
      <label for="studentName" style="font-size:13px; font-weight:700; color:var(--gray-700); white-space:nowrap;">수험자</label>
      <input type="text" id="studentName" placeholder="이름 (예: 홍길동)" maxlength="20">
      <input type="text" id="studentId" placeholder="학번/번호 (선택)" maxlength="20" style="max-width:110px;">
    </section>

    <!-- 결과 요약 카드 (제출 후 표시) -->
    <section class="result-card" id="resultCard">
      <div style="font-size:14px; font-weight:600; opacity:0.9;">시험 결과표</div>
      <div class="result-score-wrap">
        <span class="result-score" id="resScore">0</span>
        <span class="result-score-sub">점 / 100점</span>
      </div>
      <div id="resSummaryText" style="font-size:13px; opacity:0.95; margin-top:2px;"></div>
      <div class="result-stats-row">
        <div class="result-stat-item">
          <b id="resCorrectCount">0</b>
          <span>정답</span>
        </div>
        <div class="result-stat-item">
          <b id="resWrongCount" style="color:#fca5a5;">0</b>
          <span>오답</span>
        </div>
        <div class="result-stat-item">
          <b id="resAccuracy">0%</b>
          <span>정답률</span>
        </div>
      </div>
    </section>

    <!-- 문항 목록 렌더링 영역 -->
    <div id="questionsContainer"></div>

  </main>

  <!-- 하단 고정 답안 바 -->
  <div class="floating-bar">
    <div class="floating-inner">
      <div class="omr-summary">
        <span class="omr-count" id="omrAnsweredCount">0문항 응답</span>
        <span class="omr-unanswered" id="omrUnansweredMsg">미응답 문항 존재</span>
      </div>
      <div style="display:flex; gap:8px;">
        <button class="btn-reset" id="btnResetExam">다시 풀기</button>
        <button class="btn-submit" id="btnSubmitExam">답안 제출</button>
      </div>
    </div>
  </div>

  <!-- 제출 확인 모달 -->
  <div class="modal-backdrop" id="submitModal">
    <div class="modal-card">
      <div class="modal-title">답안을 제출할까요?</div>
      <div class="modal-msg" id="modalMsg">아직 풀지 않은 문항이 있습니다. 그대로 제출하고 채점하시겠습니까?</div>
      <div class="modal-actions">
        <button class="btn-modal-cancel" id="btnModalCancel">계속 풀기</button>
        <button class="btn-modal-confirm" id="btnModalConfirm">제출 및 채점</button>
      </div>
    </div>
  </div>

  <!-- 인라인 데이터 및 로직 스크립트 -->
  <script id="exam-data" type="application/json">
{data_json}
  </script>

  <script>
    (function() {{
      const rawData = JSON.parse(document.getElementById('exam-data').textContent);
      const title = rawData.title;
      const options = rawData.options;
      const questions = rawData.questions;

      const userAnswers = {{}}; // qIndex -> chosen 1-based number
      const playCounts = {{}};  // qIndex -> play count
      let currentAudio = null;
      let currentPlayingIndex = null;
      let isGraded = false;

      const CIRCLED_CHARS = ['①', '②', '③', '④', '⑤'];

      const $ = id => document.getElementById(id);
      const container = $('questionsContainer');

      // 1. 초기 렌더링
      function init() {{
        $('topExamTitle').textContent = title;
        updateHeaderProgress();

        container.innerHTML = questions.map((q, idx) => {{
          const qNum = q.num || (idx + 1);
          const hasAudio = !!q.audio_file;
          const playLimit = options.playLimit || 0;
          const limitBadge = playLimit > 0 ? `<span class="play-limit-badge" id="limitBadge_${{idx}}">재생 0/${{playLimit}}회</span>` : '';

          let extraHtml = '';
          if (q.figure_note) {{
            extraHtml += `<div class="figure-note"><b>[그림 설명]</b> ${{escapeHtml(q.figure_note)}}</div>`;
          }}
          if (q.table) {{
            const rows = q.table.split('\\n').filter(r => r.trim());
            if (rows.length > 0) {{
              extraHtml += '<div class="q-table-wrap"><table class="q-table"><tbody>';
              rows.forEach((r, rIdx) => {{
                const cols = r.split('|').map(c => c.trim());
                extraHtml += '<tr>' + cols.map(c => rIdx === 0 ? `<th>${{escapeHtml(c)}}</th>` : `<td>${{escapeHtml(c)}}</td>`).join('') + '</tr>';
              }});
              extraHtml += '</tbody></table></div>';
            }}
          }}

          const audioBoxHtml = hasAudio ? `
            <div class="audio-player-box" id="audioBox_${{idx}}">
              <div class="audio-controls">
                <button type="button" class="btn-play" id="btnPlay_${{idx}}" data-idx="${{idx}}" aria-label="재생">▶</button>
                <div class="audio-info">
                  <div class="audio-time-row">
                    <span id="timeCur_${{idx}}">0:00</span>
                    ${{limitBadge}}
                    <span id="timeDur_${{idx}}">0:00</span>
                  </div>
                  <div class="audio-seek-bar" id="seekBar_${{idx}}" data-idx="${{idx}}">
                    <div class="audio-seek-progress" id="seekProg_${{idx}}"></div>
                  </div>
                </div>
              </div>
              <audio id="audio_${{idx}}" preload="metadata" src="${{escapeHtml(q.audio_file)}}"></audio>
            </div>
          ` : '';

          const choicesHtml = q.choices.map((c, cIdx) => {{
            const choiceNum = cIdx + 1;
            return `
              <div class="choice-btn" data-qidx="${{idx}}" data-cnum="${{choiceNum}}" id="choice_${{idx}}_${{choiceNum}}">
                <span class="choice-idx">${{CIRCLED_CHARS[cIdx] || choiceNum}}</span>
                <span class="choice-text">${{escapeHtml(c)}}</span>
              </div>
            `;
          }}).join('');

          // 대본 스크립트 렌더링
          let scriptHtml = '';
          if (q.script && q.script.length > 0) {{
            scriptHtml = q.script.map(line => {{
              const spkName = {{ 'M':'남', 'W':'여', 'N':'담화', 'KO':'안내' }}[line.spk] || line.spk;
              return `<div><span class="spk-label">${{spkName}}:</span> ${{escapeHtml(line.text)}}</div>`;
            }}).join('');
          }}

          const solutionBoxHtml = `
            <div class="solution-box" id="solBox_${{idx}}">
              <div class="sol-header">
                <span class="sol-title">정답 및 해설</span>
                <span class="sol-ans-badge" id="solBadge_${{idx}}">정답: ${{CIRCLED_CHARS[q.answer - 1] || q.answer}}번</span>
              </div>
              <div class="sol-text">${{escapeHtml(q.explanation || '해설이 제공되지 않았습니다.')}}</div>
              ${{q.evidence && q.evidence.length > 0 ? `<div class="sol-evidence"><b>💡 단서 문장:</b> "${{escapeHtml(q.evidence.map(e => e.text).join(', '))}}"</div>` : ''}}
              ${{scriptHtml ? `
                <details class="script-accordion">
                  <summary>대본 전체 보기</summary>
                  <div class="script-content">${{scriptHtml}}</div>
                </details>
              ` : ''}}
            </div>
          `;

          return `
            <article class="q-card" id="qcard_${{idx}}">
              <div class="q-header">
                <span class="q-num-badge">${{qNum}}번</span>
                <h2 class="q-stem">${{escapeHtml(q.stem)}}</h2>
              </div>
              ${{extraHtml}}
              ${{audioBoxHtml}}
              <div class="choices-list">
                ${{choicesHtml}}
              </div>
              ${{solutionBoxHtml}}
            </article>
          `;
        }}).join('');

        setupAudioEvents();
        setupChoiceEvents();
        setupSubmitEvents();
      }}

      function escapeHtml(str) {{
        return String(str || '').replace(/[&<>'"]/g, tag => ({{
          '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
        }}[tag] || tag));
      }}

      // 2. 오디오 플레이어 제어
      function setupAudioEvents() {{
        questions.forEach((q, idx) => {{
          const audio = $(`audio_${{idx}}`);
          const btn = $(`btnPlay_${{idx}}`);
          const prog = $(`seekProg_${{idx}}`);
          const timeCur = $(`timeCur_${{idx}}`);
          const timeDur = $(`timeDur_${{idx}}`);
          const seekBar = $(`seekBar_${{idx}}`);
          const limitBadge = $(`limitBadge_${{idx}}`);

          if (!audio || !btn) return;

          playCounts[idx] = 0;

          audio.addEventListener('loadedmetadata', () => {{
            timeDur.textContent = formatTime(audio.duration);
          }});

          audio.addEventListener('timeupdate', () => {{
            if (!audio.duration) return;
            const pct = (audio.currentTime / audio.duration) * 100;
            prog.style.width = pct + '%';
            timeCur.textContent = formatTime(audio.currentTime);
          }});

          audio.addEventListener('ended', () => {{
            btn.textContent = '▶';
            prog.style.width = '0%';
            timeCur.textContent = '0:00';
            checkPlayLimit(idx);
          }});

          btn.addEventListener('click', () => {{
            const limit = options.playLimit || 0;
            if (limit > 0 && playCounts[idx] >= limit && audio.paused) {{
              alert('이 문항의 재생 가능 횟수를 모두 소진했습니다.');
              return;
            }}

            if (audio.paused) {{
              // 다른 문항 정지
              if (currentAudio && currentAudio !== audio) {{
                currentAudio.pause();
                currentAudio.currentTime = 0;
                if (currentPlayingIndex !== null) {{
                  const prevBtn = $(`btnPlay_${{currentPlayingIndex}}`);
                  if (prevBtn) prevBtn.textContent = '▶';
                }}
              }}

              // 재생 카운트 증가
              if (audio.currentTime === 0) {{
                playCounts[idx]++;
                updateLimitBadge(idx);
              }}

              audio.play().then(() => {{
                btn.textContent = '⏸';
                currentAudio = audio;
                currentPlayingIndex = idx;
              }}).catch(e => {{
                console.warn('재생 오류:', e);
                alert('음원을 재생할 수 없습니다. 오디오 파일을 확인하세요.');
              }});
            }} else {{
              audio.pause();
              btn.textContent = '▶';
            }}
          }});

          seekBar.addEventListener('click', e => {{
            if (!audio.duration) return;
            const rect = seekBar.getBoundingClientRect();
            const clickPos = (e.clientX - rect.left) / rect.width;
            audio.currentTime = clickPos * audio.duration;
          }});
        }});
      }}

      function checkPlayLimit(idx) {{
        const limit = options.playLimit || 0;
        if (limit > 0 && playCounts[idx] >= limit) {{
          const btn = $(`btnPlay_${{idx}}`);
          if (btn) {{
            btn.disabled = true;
            btn.textContent = '소진';
          }}
          const badge = $(`limitBadge_${{idx}}`);
          if (badge) {{
            badge.classList.add('exhausted');
            badge.textContent = `재생 ${{limit}}/${{limit}}회 (소진)`;
          }}
        }}
      }}

      function updateLimitBadge(idx) {{
        const limit = options.playLimit || 0;
        if (limit > 0) {{
          const badge = $(`limitBadge_${{idx}}`);
          if (badge) {{
            badge.textContent = `재생 ${{playCounts[idx]}}/${{limit}}회`;
          }}
        }}
      }}

      function formatTime(sec) {{
        if (!sec || isNaN(sec)) return '0:00';
        const m = Math.floor(sec / 60);
        const s = Math.floor(sec % 60);
        return m + ':' + (s < 10 ? '0' : '') + s;
      }}

      // 3. 선택지 탭 이벤트
      function setupChoiceEvents() {{
        container.addEventListener('click', e => {{
          if (isGraded) return; // 채점 완료 후 선택 변경 불가

          const btn = e.target.closest('.choice-btn');
          if (!btn) return;

          const qIdx = parseInt(btn.dataset.qidx, 10);
          const cNum = parseInt(btn.dataset.cnum, 10);

          // 기존 선택 해제
          const prevSelected = container.querySelector(`.choice-btn.selected[data-qidx="${{qIdx}}"]`);
          if (prevSelected) prevSelected.classList.remove('selected');

          // 새 선택
          btn.classList.add('selected');
          userAnswers[qIdx] = cNum;

          $(`qcard_${{qIdx}}`).classList.add('answered');

          updateHeaderProgress();
          updateOmrSummary();
        }});
      }}

      // 4. 진행 현황 업데이트
      function updateHeaderProgress() {{
        const answeredCount = Object.keys(userAnswers).length;
        const total = questions.length;
        $('topProgressBadge').textContent = `${{answeredCount}} / ${{total}} 문항`;
        const pct = total > 0 ? (answeredCount / total) * 100 : 0;
        $('topProgressBar').style.width = pct + '%';
      }}

      function updateOmrSummary() {{
        const answeredCount = Object.keys(userAnswers).length;
        const total = questions.length;
        $('omrAnsweredCount').textContent = `${{answeredCount}} / ${{total}} 문항 응답`;

        const unanswered = [];
        for (let i = 0; i < total; i++) {{
          if (!userAnswers[i]) unanswered.push(questions[i].num || (i + 1));
        }}

        if (unanswered.length === 0) {{
          $('omrUnansweredMsg').textContent = '✔ 모든 문항 응답 완료';
          $('omrUnansweredMsg').style.color = 'var(--success)';
        }} else {{
          $('omrUnansweredMsg').textContent = `미응답: ${{unanswered.slice(0, 5).join(', ')}}${{unanswered.length > 5 ? ' 외' : ''}}`;
          $('omrUnansweredMsg').style.color = 'var(--danger)';
        }}
      }}

      // 5. 답안 제출 및 자동 채점
      function setupSubmitEvents() {{
        const modal = $('submitModal');

        $('btnSubmitExam').addEventListener('click', () => {{
          if (isGraded) return;

          const total = questions.length;
          const answeredCount = Object.keys(userAnswers).length;

          if (answeredCount < total) {{
            $('modalMsg').textContent = `총 ${{total}}문항 중 ${{total - answeredCount}}문항에 아직 응답하지 않았습니다. 그대로 제출하고 채점하시겠습니까?`;
          }} else {{
            $('modalMsg').textContent = `모든 문항(${{total}}문항)에 응답했습니다. 최종 제출하시겠습니까?`;
          }}
          modal.classList.add('active');
        }});

        $('btnModalCancel').addEventListener('click', () => {{
          modal.classList.remove('active');
        }});

        $('btnModalConfirm').addEventListener('click', () => {{
          modal.classList.remove('active');
          gradeExam();
        }});

        $('btnResetExam').addEventListener('click', () => {{
          if (confirm('모든 답안을 지우고 처음부터 다시 풀까요?')) {{
            location.reload();
          }}
        }});
      }}

      function gradeExam() {{
        if (currentAudio) {{
          currentAudio.pause();
        }}

        isGraded = true;
        let correctCount = 0;
        const total = questions.length;

        questions.forEach((q, idx) => {{
          const userAns = userAnswers[idx];
          const correctAns = q.answer;
          const card = $(`qcard_${{idx}}`);
          const solBox = $(`solBox_${{idx}}`);

          const isCorrect = (userAns === correctAns);
          if (isCorrect) correctCount++;

          // 카드 상태
          card.classList.remove('answered');
          card.classList.add(isCorrect ? 'graded-correct' : 'graded-wrong');

          // 선택지 하이라이트
          q.choices.forEach((_, cIdx) => {{
            const cNum = cIdx + 1;
            const cBtn = $(`choice_${{idx}}_${{cNum}}`);
            if (cNum === correctAns) {{
              cBtn.classList.add('graded-ans');
            }} else if (cNum === userAns && !isCorrect) {{
              cBtn.classList.add('graded-wrong-choice');
            }}
          }});

          // 해설 박스 열기
          if (solBox) {{
            solBox.classList.add('show');
          }}
        }});

        // 결과 통계 계산
        const score = total > 0 ? Math.round((correctCount / total) * 100) : 0;
        const wrongCount = total - correctCount;
        const accuracy = total > 0 ? Math.round((correctCount / total) * 100) : 0;

        $('resScore').textContent = score;
        $('resCorrectCount').textContent = correctCount;
        $('resWrongCount').textContent = wrongCount;
        $('resAccuracy').textContent = accuracy + '%';

        const sName = ($('studentName').value || '').trim();
        $('resSummaryText').textContent = sName ? `${{sName}} 수험생의 채점 결과입니다.` : '채점이 완료되었습니다.';

        $('resultCard').classList.add('show');
        $('btnSubmitExam').style.display = 'none';
        $('btnResetExam').style.display = 'block';

        // 상단으로 부드럽게 스크롤
        window.scrollTo({{ top: 0, behavior: 'smooth' }});
      }}

      init();
    }})();
  </script>
</body>
</html>
"""
    return template


def generate_mobile_exam_bundle(title, items, questions, job_out=None, output_dir=None, options=None):
    """
    모바일 웹 응시 페이지 번들(HTML 1개 + audio/ 폴더)을 대상 디렉토리에 생성합니다.
    - title: 시험 명칭
    - items: 파싱된 대본 항목 리스트
    - questions: 문항 딕셔너리 (id -> dict)
    - job_out: 음원 mp3 파일들이 들어있는 디렉토리 (선택)
    - output_dir: 출력 대상 폴더 (기본: output_dir)
    - options: playLimit 등 설정
    """
    if not output_dir:
        output_dir = "web_exam_bundle"

    os.makedirs(output_dir, exist_ok=True)
    audio_dir = os.path.join(output_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)

    items_by_id = {it["id"]: it for it in items}

    # 승인된 문항 순회
    exam_questions = []
    audio_files_to_copy = []

    for i, it in enumerate(items):
        qid = it["id"]
        q = questions.get(qid)
        if not q or not q.get("approved"):
            continue

        qnum = str(int(qid)) if qid.isdigit() else (qid or str(i + 1))

        # 오디오 파일 탐색 (job_out 내부에서 매칭)
        # 1) 문항 ID가 포함된 mp3 파일 검색 (예: 01_오늘오후의날씨.mp3, 01.mp3)
        audio_rel_path = ""
        matched_audio = None
        if job_out and os.path.isdir(job_out):
            for fname in sorted(os.listdir(job_out)):
                if fname.lower().endswith((".mp3", ".wav")) and not fname.startswith("_") and "전체합본" not in fname:
                    if fname.startswith(f"{qid}_") or fname.startswith(f"{int(qid):02d}_") if qid.isdigit() else False:
                        matched_audio = fname
                        break
                    if qid in fname:
                        matched_audio = fname
                        break

            if matched_audio:
                src_audio = os.path.join(job_out, matched_audio)
                dst_audio = os.path.join(audio_dir, matched_audio)
                if not os.path.exists(dst_audio) or os.path.getsize(src_audio) != os.path.getsize(dst_audio):
                    shutil.copy2(src_audio, dst_audio)
                audio_rel_path = f"audio/{matched_audio}"

        exam_q = {
            "id": qid,
            "num": qnum,
            "title": it.get("title", f"{qnum}번 문항"),
            "stem": q.get("stem", ""),
            "choices": q.get("choices", []),
            "answer": q.get("answer", 0),
            "explanation": q.get("explanation", ""),
            "evidence": q.get("evidence", []),
            "figure_note": q.get("figure_note", ""),
            "table": q.get("table", ""),
            "audio_file": audio_rel_path,
            "script": it.get("lines", [])
        }
        exam_questions.append(exam_q)

    html_content = build_mobile_exam_html(title, exam_questions, options=options)
    html_path = os.path.join(output_dir, "index.html")

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return {
        "html_path": html_path,
        "questions_count": len(exam_questions),
        "audio_count": len(os.listdir(audio_dir))
    }
