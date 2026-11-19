# -*- coding: utf-8 -*-
"""
AI 대본 점검 + 5지선다 문항 만들기 (Google Gemini API, 무료 등급 키 사용 가능)
 - 추가 설치 없이 파이썬 기본 기능(urllib)만 사용합니다.
 - 대본 내용이 Google 서버로 전송됩니다. 학생 개인정보는 넣지 마세요.
"""
import json
import re
import time
import urllib.error
import urllib.request

API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash"
SPK_NAME = {"M": "남자", "W": "여자", "N": "해설(한 사람)", "KO": "한국어 안내"}


class AIError(Exception):
    pass


def _request(url, key, body=None, timeout=180):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(raw).get("error", {}).get("message", raw)
        except Exception:
            msg = raw
        if e.code == 429:
            raise AIError("QUOTA|무료 사용 한도에 걸렸습니다. 1~2분 뒤 다시 하거나, 하루 한도라면 내일 다시 해 주세요. (%s)" % msg[:160])
        if e.code == 403 and "free tier" in msg.lower():
            raise AIError("FREE|이 키가 속한 Google 프로젝트는 무료 등급을 쓸 수 없도록 막혀 있습니다. "
                          "같은 프로젝트에서 키를 새로 만들어도 안 됩니다. AI Studio의 API 키 목록에서 '무료 등급'으로 표시된 다른 프로젝트의 키를 넣어 주세요.")
        if e.code in (400, 401, 403) and ("API key" in msg or "API_KEY" in msg or "permission" in msg.lower()):
            raise AIError("KEY|API 키가 올바르지 않거나 이 모델을 쓸 권한이 없습니다. 키를 다시 확인해 주세요. (%s)" % msg[:160])
        if e.code == 404:
            raise AIError("MODEL|이 키로는 이 모델을 쓸 수 없습니다: %s" % msg[:160])
        raise AIError("HTTP|AI 서버 오류(%d): %s" % (e.code, msg[:200]))
    except urllib.error.URLError as e:
        raise AIError("NET|AI 서버에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요. (%s)" % e.reason)


def list_models(key):
    """generateContent 를 쓸 수 있는 모델 이름 목록 (빠른 'flash' 모델이 앞으로)."""
    names, token = [], ""
    for _ in range(5):
        url = API + "/models?pageSize=200" + ("&pageToken=" + token if token else "")
        d = _request(url, key)
        for m in d.get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                n = m["name"].split("/", 1)[-1]
                if not re.search(r"(tts|image|embedding|aqa|live|audio|vision)", n):
                    names.append(n)
        token = d.get("nextPageToken", "")
        if not token:
            break

    def rank(n):
        v = re.findall(r"(\d+(?:\.\d+)?)", n)
        ver = float(v[0]) if v else 0
        return (0 if "flash" in n else 1, 1 if ("preview" in n or "exp" in n) else 0, -ver, n)
    return sorted(set(names), key=rank)


# ----------------------------------------------------------------------
# 요청 내용
# ----------------------------------------------------------------------
SCHEMA = {
    "type": "OBJECT",
    "properties": {"items": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING"},
            "issues": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "severity": {"type": "STRING", "enum": ["높음", "보통", "낮음"]},
                "message": {"type": "STRING"},
                "evidence": {"type": "STRING"}}, "required": ["severity", "message"]}},
            "stem": {"type": "STRING"},
            "choices": {"type": "ARRAY", "items": {"type": "STRING"}},
            "answer": {"type": "INTEGER"},
            "explanation": {"type": "STRING"},
            "evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
            "table_rows": {"type": "ARRAY", "items": {"type": "STRING"}},
            "figure_note": {"type": "STRING"},
        },
        "required": ["id", "issues", "stem", "choices", "answer", "explanation", "evidence"]}}},
    "required": ["items"],
}

INSTRUCTION = """당신은 한국 고등학교 영어 듣기평가(수능·학력평가 형식) 출제와 검토를 함께 맡은 전문가입니다.
아래 문항마다 두 가지를 하세요. 대본에 없는 정보는 절대 지어내지 마세요.

[1] 내용 점검(issues)
 - 발문(한국어 안내)과 대본이 맞지 않는 곳을 찾으세요.
   예: 발문은 '여자의 의견'인데 실제로 의견을 말하는 사람은 남자, 발문은 '여자가 할 일'인데 할 일을 맡는 사람은 남자,
       금액 문제인데 계산에 필요한 정보가 없음, 그림·표 문제인데 대본만으로 정답을 정할 수 없음.
 - 정답이 하나로 정해지지 않는 경우, 영어 표현이 어색하거나 틀린 경우도 적으세요.
 - severity: 높음(문항이 성립하지 않음), 보통(고치는 것이 좋음), 낮음(참고).
 - message 는 한국어로 짧게, evidence 에는 근거가 되는 대본의 영어 문장을 그대로 옮기세요.
 - 문제가 없으면 issues 는 빈 배열로 두세요.

[2] 5지선다 문항(stem, choices, answer, explanation, evidence)
 - stem: 한국어 발문. 발문이 있으면 그 유형을 따르되, [1]에서 발문 오류를 찾았다면 고친 발문을 쓰세요.
   발문이 없으면 대본에 맞는 수능형 발문을 새로 만드세요. 앞의 번호('1번.')는 빼세요.
 - choices: 정확히 5개. 선택지 앞에 번호(①, 1.)를 붙이지 마세요. 유형에 맞게 한국어 또는 영어로 쓰세요.
   오답은 대본에 나온 다른 정보를 활용해 그럴듯하게 만들되, 정답은 하나뿐이어야 합니다.
 - answer: 정답 번호(1~5). 여러 문항의 정답 위치가 한쪽으로 몰리지 않게 하세요.
 - explanation: 한국어 해설 2~3문장.
 - evidence: 정답의 근거가 되는 대본 문장을 한 글자도 바꾸지 말고 그대로 옮긴 영어 문장 1~3개.
 - table_rows: 표를 보고 푸는 문항일 때만. 첫 줄은 머리글, 칸은 ' | ' 로 구분 (예: "Model | Price | Capacity").
   대본의 조건으로 정답이 하나만 남도록 5개 행을 만드세요. 필요 없으면 빈 배열.
 - figure_note: 그림을 보고 푸는 문항일 때만, 그림에 그려야 할 내용을 한국어로 설명. 아니면 빈 문자열.
 - id 는 입력의 문항 번호를 그대로 쓰세요."""


def build_prompt(items):
    parts = [INSTRUCTION, "", "===== 문항 ====="]
    for it in items:
        ko = " ".join(ln["text"] for ln in it["lines"] if ln["spk"] == "KO")
        parts.append("")
        parts.append("[문항 %s] 제목: %s" % (it["id"], it.get("title") or "-"))
        parts.append("발문(한국어 안내): " + (ko or "(없음 - 새로 만드세요)"))
        parts.append("대본:")
        for ln in it["lines"]:
            if ln["spk"] != "KO":
                parts.append("%s(%s): %s" % (ln["spk"], SPK_NAME.get(ln["spk"], ln["spk"]), ln["text"]))
    return "\n".join(parts)


WEB_FORMAT = """

[출력 형식] 다른 설명 없이 아래 모양의 JSON 하나만 ```json 코드 블록 안에 출력하세요. 모든 문항을 빠짐없이 넣으세요.
{"items": [
  {"id": "01",
   "issues": [{"severity": "높음|보통|낮음", "message": "문제 설명", "evidence": "근거 영어 문장"}],
   "stem": "발문",
   "choices": ["선택지1", "선택지2", "선택지3", "선택지4", "선택지5"],
   "answer": 3,
   "explanation": "해설",
   "evidence": ["대본에서 그대로 옮긴 영어 문장"],
   "table_rows": [],
   "figure_note": ""}
]}"""


def web_prompt(items):
    """Gemini 웹사이트에 붙여넣을 요청문 (API 없이 쓸 때)"""
    return build_prompt(items) + WEB_FORMAT


def _loads_loose(s):
    s = s.strip().lstrip("\ufeff")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    try:
        return json.loads(s)
    except Exception:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", s))   # 끝에 남은 쉼표 정리


def parse_answer(answer, items):
    """Gemini 웹사이트 답을 읽어 문항으로 바꾼다. 반환: (id->문항, 답에 없던 문항 번호)"""
    text = answer or ""
    blocks = re.findall(r"```(?:json|JSON)?\s*(.*?)```", text, re.S)
    cands = blocks + [text]
    data = None
    for c in cands:
        for piece in (c, c[c.find("{"):c.rfind("}") + 1] if "{" in c else "", c[c.find("["):c.rfind("]") + 1] if "[" in c else ""):
            if not piece.strip():
                continue
            try:
                data = _loads_loose(piece)
                break
            except Exception:
                continue
        if data is not None:
            break
    if data is None:
        raise AIError("FORMAT|붙여넣은 답에서 문항을 읽지 못했습니다. Gemini 답 아래의 '복사' 버튼으로 답 전체를 복사해 다시 붙여넣어 주세요.")
    lst = data.get("items", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    got = {}
    for x in lst:
        if isinstance(x, dict):
            got[str(x.get("id", "")).strip().lstrip("#").strip()] = x
    out, missing = {}, []
    for it in items:
        q = got.get(it["id"]) or got.get(it["id"].lstrip("0")) or got.get(it["id"].zfill(2))
        if q:
            out[it["id"]] = verify(it, q)
        elif any(ln["spk"] != "KO" for ln in it["lines"]):
            missing.append(it["id"])
    if not out:
        raise AIError("FORMAT|답에 이 대본의 문항 번호가 없습니다. 요청문을 다시 복사해 보내 보세요.")
    return out, missing


def call_model(key, model, prompt):
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json", "responseSchema": SCHEMA}}
    d = _request("%s/models/%s:generateContent" % (API, model), key, body)
    cands = d.get("candidates") or []
    if not cands:
        fb = d.get("promptFeedback", {})
        raise AIError("EMPTY|AI가 답을 주지 않았습니다. (%s)" % (fb.get("blockReason") or "빈 응답"))
    text = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []) if not p.get("thought"))
    if cands[0].get("finishReason") == "MAX_TOKENS":
        raise AIError("LONG|AI 답이 너무 길어 중간에 끊겼습니다. 문항 수를 줄여 다시 해 주세요.")
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise AIError("FORMAT|AI 답을 읽지 못했습니다. 다시 시도해 주세요.")


# ----------------------------------------------------------------------
# 검증 (AI 답을 그대로 믿지 않고 대본과 대조)
# ----------------------------------------------------------------------
def _norm(s):
    s = s.lower().replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def verify(item, q):
    script = _norm(" ".join(ln["text"] for ln in item["lines"] if ln["spk"] != "KO"))
    warn = []
    choices = [re.sub(r"^\s*(?:[①②③④⑤]|\(?[1-5][.)])\s*", "", c).strip() for c in (q.get("choices") or [])]
    if len(choices) != 5:
        warn.append("선택지가 5개가 아닙니다(%d개). 직접 고쳐 주세요." % len(choices))
    while len(choices) < 5:
        choices.append("")
    choices = choices[:5]
    ans = q.get("answer")
    try:
        ans = int(ans)
    except Exception:
        ans = 0
    if ans not in (1, 2, 3, 4, 5):
        warn.append("정답 번호가 올바르지 않습니다. 직접 골라 주세요.")
        ans = 0
    if len(set(c for c in choices if c)) < len([c for c in choices if c]):
        warn.append("같은 선택지가 두 번 있습니다.")
    ev = []
    for e in q.get("evidence") or []:
        e = (e or "").strip()
        if e:
            ev.append({"text": e, "found": bool(_norm(e)) and _norm(e) in script})
    if not ev:
        warn.append("정답 근거 문장이 없습니다.")
    elif not all(x["found"] for x in ev):
        warn.append("근거 문장 중 대본에서 그대로 찾을 수 없는 것이 있습니다. AI가 지어냈을 수 있으니 확인해 주세요.")
    rows = [r for r in (q.get("table_rows") or []) if r and r.strip()]
    issues = []
    for x in q.get("issues") or []:
        if x.get("message"):
            issues.append({"severity": x.get("severity") or "보통", "message": x["message"],
                           "evidence": x.get("evidence") or "",
                           "found": (not x.get("evidence")) or _norm(x.get("evidence", "")) in script})
    return {"stem": re.sub(r"^\s*\d+\s*번\s*[.)]?\s*", "", (q.get("stem") or "").strip()),
            "choices": choices, "answer": ans, "explanation": (q.get("explanation") or "").strip(),
            "evidence": ev, "table": "\n".join(rows), "figure_note": (q.get("figure_note") or "").strip(),
            "issues": issues, "warnings": warn}


def mask(key):
    return (key[:4] + "…" + key[-4:]) if len(key) > 10 else "키"


class KeyPool:
    """여러 API 키를 차례로 쓴다. 무료 등급이 막히거나 한도에 걸리면 다음 키로 넘어간다."""

    def __init__(self, keys, model):
        self.keys = [k for k in keys if k]
        self.i = 0
        self.model = {k: model or DEFAULT_MODEL for k in self.keys}
        self.tried_models = {k: set() for k in self.keys}
        self.dead = {}   # 키 -> 이유

    def alive(self):
        return [k for k in self.keys if k not in self.dead]

    def current(self):
        live = self.alive()
        if not live:
            return None
        return live[self.i % len(live)]

    def kill(self, key, why):
        self.dead[key] = why

    def rotate(self):
        self.i += 1

    def next_model(self, key):
        """이 키로 쓸 수 있는 다른 모델을 고른다. 없으면 None"""
        self.tried_models[key].add(self.model[key])
        for n in list_models(key):
            if n not in self.tried_models[key]:
                self.model[key] = n
                return n
        return None


def call_with_pool(pool, prompt, max_tries=8):
    last = None
    quota_waited = set()
    for _ in range(max_tries):
        key = pool.current()
        if key is None:
            break
        try:
            return call_model(key, pool.model[key], prompt), key
        except AIError as e:
            code, msg = str(e).split("|", 1)
            last = e
            if code in ("FREE", "KEY"):
                pool.kill(key, msg)
            elif code == "MODEL":
                try:
                    if not pool.next_model(key):
                        pool.kill(key, "이 키로 쓸 수 있는 모델이 없습니다.")
                except AIError as e2:
                    pool.kill(key, str(e2).split("|", 1)[-1])
            elif code == "QUOTA":
                if len(pool.alive()) > 1:
                    pool.rotate()
                elif key not in quota_waited:
                    quota_waited.add(key)
                    time.sleep(25)
                else:
                    raise
            elif code in ("FORMAT", "EMPTY", "HTTP"):
                time.sleep(3)
            else:
                raise
    if pool.current() is None and pool.dead:
        raise AIError("ALLDEAD|저장된 키를 모두 쓸 수 없습니다. " + " / ".join("%s: %s" % (mask(k), w[:90]) for k, w in pool.dead.items()))
    raise last or AIError("HTTP|AI가 답하지 않았습니다.")


def test_keys(keys, model=None):
    """키마다 아주 짧은 요청을 보내 무료로 쓸 수 있는지 확인한다."""
    out = []
    for k in keys:
        pool = KeyPool([k], model)
        body = {"contents": [{"role": "user", "parts": [{"text": "Reply with the single word OK."}]}], "generationConfig": {"maxOutputTokens": 20}}
        res = {"masked": mask(k), "ok": False, "model": "", "message": ""}
        for _ in range(4):
            try:
                _request("%s/models/%s:generateContent" % (API, pool.model[k]), k, body, timeout=60)
                res.update(ok=True, model=pool.model[k], message="사용 가능")
                break
            except AIError as e:
                code, msg = str(e).split("|", 1)
                if code == "MODEL":
                    try:
                        if pool.next_model(k):
                            continue
                    except AIError as e2:
                        msg = str(e2).split("|", 1)[-1]
                if code == "QUOTA":
                    res.update(ok=True, model=pool.model[k], message="지금은 한도에 걸렸지만 무료 등급입니다 (잠시 뒤 사용 가능)")
                    break
                res["message"] = msg[:200]
                break
        out.append(res)
    return out


def review(keys, model, items, chunk=5, progress=None):
    """문항들을 chunk 개씩 보내 점검 + 문항 생성. 반환: (결과 dict id->문항, 사용한 모델, 오류 목록)"""
    if isinstance(keys, str):
        keys = [keys]
    pool = KeyPool(keys, model)
    out, errors, used = {}, [], model or DEFAULT_MODEL
    groups = [items[i:i + chunk] for i in range(0, len(items), chunk)]
    for gi, group in enumerate(groups):
        if progress:
            progress("AI 점검 중… (%d/%d 묶음)" % (gi + 1, len(groups)))
        prompt = build_prompt(group)
        data = None
        try:
            data, k = call_with_pool(pool, prompt)
            used = pool.model[k]
        except AIError as e:
            code = str(e).split("|", 1)[0]
            if code in ("KEY", "FREE", "NET", "ALLDEAD"):
                raise
            errors.append({"ids": [g["id"] for g in group], "message": str(e).split("|", 1)[-1]})
        if data is None:
            continue
        got = {str(x.get("id", "")).strip(): x for x in data.get("items", [])}
        for it in group:
            q = got.get(it["id"]) or got.get(it["id"].lstrip("0"))
            if not q:
                errors.append({"ids": [it["id"]], "message": "AI가 이 문항의 답을 주지 않았습니다."})
                continue
            out[it["id"]] = verify(it, q)
        if gi + 1 < len(groups):
            time.sleep(2)
    return out, used, errors
