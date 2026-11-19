# -*- coding: utf-8 -*-
"""
모바일 웹 응시 페이지 독립 생성기 (CLI 대화형 메뉴)
- 기존 작업 상태(_data/작업상태.json) 또는 샘플 데이터를 읽어 모바일 웹 시험지(docs/ 또는 web-sample/)를 즉시 조립합니다.
- 배치 파일(2_웹앱_실행.bat)에서 번호 선택으로 바로 호출할 수 있습니다.
"""
import os
import sys
import json
import shutil
import http.server
import socketserver
import webbrowser
import threading

import listening_maker as L
import mobile_exam_generator as MEG

DEFAULT_SAMPLE_SCRIPT = """## 01 | 오늘 오후의 날씨 | 악센트=US | 속도=+0%
KO: 1번. 다음을 듣고, 오늘 오후의 날씨로 가장 적절한 것을 고르시오.
N: Good morning. Here is today's weather forecast. This morning is clear and sunny, so it's a great time to take a walk. However, thick clouds will roll in from the west in the afternoon. It will start raining around 3 p.m., and the rain will continue until tomorrow morning. When you go outside this afternoon, don't forget to take an umbrella with you.

## 02 | 구입할 물병 선택 | 악센트=US+UK | 속도=+0%
KO: 2번. 대화를 듣고, 남자가 구입할 물병으로 가장 적절한 것을 고르시오.
W: Hello, may I help you?
M: Yes, I'm looking for a water bottle for my son.
W: How about these round ones? They are very popular among middle school students.
M: They look nice. Does it have a handle? A handle makes it easier to carry around.
W: Yes, this one has a handle on top. We have two designs: one with stars and one with stripes.
M: My son loves stars. I'll take the round one with stars.
W: Great choice.

## 03 | 퀴즈 대회 결과 확인 후 심정 | 악센트=UK+US | 속도=+0%
KO: 3번. 대화를 듣고, 남자의 심정으로 가장 적절한 것을 고르시오.
W: Minho, did you check the results of the school science quiz contest?
M: No, I haven't yet, Sujin. Did the results come out?
W: Yes! I just saw the announcement board in the hallway.
M: Really? How did our team do?
W: We won first place!
M: Wow, are you serious? We practiced so hard every day after school!
W: I know! I was so nervous during the final round, but we really made it!
M: That's fantastic! I'm so thrilled, and my heart is beating fast.
"""

DEFAULT_SAMPLE_QUESTIONS = {
    "01": {
        "stem": "1번. 다음을 듣고, 오늘 오후의 날씨로 가장 적절한 것을 고르시오.",
        "choices": [
            "맑고 화창함",
            "안개가 짙게 낌",
            "오후 3시경부터 비가 옴",
            "강한 눈보라가 침",
            "바람이 많이 불고 건조함"
        ],
        "answer": 3,
        "explanation": "오전에는 맑지만 오후 서쪽에서 구름이 몰려와 오후 3시경부터 비가 내릴 것이라고 안내하고 있으므로 정답은 3번입니다.",
        "evidence": [{"text": "It will start raining around 3 p.m., and the rain will continue until tomorrow morning.", "found": True}],
        "approved": True
    },
    "02": {
        "stem": "2번. 대화를 듣고, 남자가 구입할 물병으로 가장 적절한 것을 고르시오.",
        "choices": [
            "손잡이가 없는 둥근 별무늬 물병",
            "손잡이가 달린 네모난 줄무늬 물병",
            "손잡이가 달린 둥근 별무늬 물병",
            "손잡이가 달린 둥근 줄무늬 물병",
            "손잡이가 없는 네모난 별무늬 물병"
        ],
        "answer": 3,
        "explanation": "손잡이가 위에 있고 아들이 좋아하는 별무늬가 들어간 둥근 물병을 선택했으므로 정답은 3번입니다.",
        "evidence": [{"text": "I'll take the round one with stars.", "found": True}],
        "approved": True
    },
    "03": {
        "stem": "3번. 대화를 듣고, 남자의 심정으로 가장 적절한 것을 고르시오.",
        "choices": [
            "thrilled and excited (흥분되고 매우 기쁜)",
            "worried and anxious (걱정되고 불안한)",
            "disappointed and upset (실망하고 속상한)",
            "bored and indifferent (지루하고 무관심한)",
            "embarrassed and shy (부끄럽고 당황스러운)"
        ],
        "answer": 1,
        "explanation": "과학 퀴즈 대회에서 팀이 1등을 했다는 소식을 듣고 심장이 빨리 뛰며 매우 신나고 감격해하고 있으므로 정답은 1번입니다.",
        "evidence": [{"text": "I'm so thrilled, and my heart is beating fast.", "found": True}],
        "approved": True
    }
}


def load_current_workspace_state():
    """_data/작업상태.json을 읽어옵니다."""
    state_file = os.path.join("_data", "작업상태.json")
    if os.path.isfile(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                d = json.load(f)
                script = d.get("script", "")
                questions = d.get("questions", {})
                title = d.get("exportTitle") or "영어 듣기 모바일 평가"
                if script.strip() and questions:
                    return title, script, questions
        except Exception:
            pass
    return "2026학년도 영어 듣기 모바일 평가 (샘플)", DEFAULT_SAMPLE_SCRIPT, DEFAULT_SAMPLE_QUESTIONS


def build_exam_action(output_target="docs", play_limit=2):
    """지정된 대상 폴더에 모바일 웹 시험지를 빌드합니다."""
    print("\n[진행] 모바일 웹 응시 페이지를 생성하고 있습니다...")
    title, script_text, questions = load_current_workspace_state()

    items, _ = L.parse_input(script_text)

    # 최근 음원 폴더 탐색
    job_out = None
    out_root = "output_web"
    if os.path.isdir(out_root):
        candidates = [os.path.join(out_root, d) for d in os.listdir(out_root)]
        candidates = [c for c in candidates if os.path.isdir(c) and not os.path.basename(c).startswith("_")]
        if candidates:
            candidates.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            job_out = candidates[0]

    os.makedirs(output_target, exist_ok=True)
    res = MEG.generate_mobile_exam_bundle(
        title=title,
        items=items,
        questions=questions,
        job_out=job_out,
        output_dir=output_target,
        options={"playLimit": play_limit}
    )

    print(f"[성공] 모바일 웹 응시 페이지가 생성되었습니다!")
    print(f" - 생성 위치: {os.path.abspath(output_target)}")
    print(f" - 메인 파일: {os.path.join(output_target, 'index.html')}")
    print(f" - 포함 문항: {res['questions_count']}개")
    print(f" - 포함 음원: {res['audio_count']}개")
    return output_target


def serve_and_open(folder, port=8080):
    """로컬 HTTP 서버를 띄워 모바일 웹 시험지를 브라우저로 엽니다."""
    abs_folder = os.path.abspath(folder)
    print(f"\n[안내] 로컬 테스트 서버를 시작합니다: http://localhost:{port}")
    print(f" - 루트 폴더: {abs_folder}")
    print(" - 브라우저에서 F12를 누르고 모바일 뷰(iPhone/Galaxy)로 전환해 보세요.")
    print(" - 테스트 종료 시 [Ctrl + C]를 누르세요.\n")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=abs_folder, **kwargs)

    try:
        with socketserver.TCPServer(("", port), Handler) as httpd:
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            webbrowser.open(f"http://localhost:{port}")
            print("서버가 백그라운드에서 동작 중입니다. 엔터를 누르면 메뉴로 돌아갑니다.")
            input()
            httpd.shutdown()
    except Exception as e:
        print(f"[오류] 서버 실행 실패: {e}")


def interactive_menu():
    while True:
        print("\n" + "=" * 54)
        print("    듣기 음원 만들기: 모바일 웹 응시 페이지 도구")
        print("=" * 54)
        print("  1. GitHub Pages 배포용 생성 (docs/ 폴더에 생성)")
        print("  2. 일반 웹 샘플 생성 (web-sample/ 폴더에 생성)")
        print("  3. 생성된 docs/ 페이지 로컬 브라우저로 열기 (포트 8080)")
        print("  4. 종료")
        print("=" * 54)
        choice = input("원하는 작업 번호를 선택하세요 (1-4) [1]: ").strip()
        if not choice:
            choice = "1"

        if choice == "1":
            build_exam_action(output_target="docs", play_limit=2)
        elif choice == "2":
            build_exam_action(output_target="web-sample", play_limit=2)
        elif choice == "3":
            if not os.path.exists("docs"):
                print("[알림] docs/ 폴더가 없습니다. 먼저 1번으로 생성합니다.")
                build_exam_action(output_target="docs", play_limit=2)
            serve_and_open("docs")
        elif choice == "4":
            print("프로그램을 종료합니다.")
            break
        else:
            print("[알림] 잘못된 입력입니다. 1에서 4 사이 번호를 입력하세요.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--build-docs":
        build_exam_action(output_target="docs", play_limit=2)
    elif len(sys.argv) > 1 and sys.argv[1] == "--build-sample":
        build_exam_action(output_target="web-sample", play_limit=2)
    else:
        interactive_menu()
