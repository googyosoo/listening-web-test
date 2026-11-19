# -*- coding: utf-8 -*-
"""
듣기 음원 만들기 웹앱 (내 컴퓨터에서만 열리는 화면)
 - 실행: python app.py   (또는 웹앱_실행.bat 더블클릭)
 - 주소: http://127.0.0.1:8765  (이 컴퓨터에서만 접속 가능)
listening_maker.py 의 제작/검수 기능을 그대로 사용합니다.
"""
import argparse
import asyncio
import contextlib
import csv
import io
import json
import mimetypes
import os
import sys
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import hashlib
import re

import ai_helper as AI
import exporter as EX
import listening_maker as L

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(HERE, "output_web")
CACHE_DIR = os.path.join(HERE, "_cache")       # 줄 단위 음성 저장소 (수정한 줄만 다시 만들기용)
STATE_FILE = os.path.join(HERE, "_data", "작업상태.json")  # 대본·목소리·옵션 자동 저장
AI_FILE = os.path.join(HERE, "_data", "AI설정.json")          # Gemini API 키 (이 컴퓨터에만 저장)
PRON_FILE = os.path.join(HERE, "_data", "발음사전.json")     # 발음 사전 (화면에서 등록한 단어)


def read_pron():
    try:
        with open(PRON_FILE, "r", encoding="utf-8") as f:
            return [e for e in json.load(f) if isinstance(e, dict)]
    except Exception:
        return []


def write_pron(entries):
    clean = [{"word": str(e.get("word", "")).strip(), "say": str(e.get("say", "")).strip(), "only": str(e.get("only", "")).strip()}
             for e in entries if isinstance(e, dict)]
    os.makedirs(os.path.dirname(PRON_FILE), exist_ok=True)
    with open(PRON_FILE, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=1)
    return clean


def merged_pron(text):
    """대본의 @발음 줄이 먼저, 그다음 화면의 발음 사전 (같은 단어면 대본 것이 우선)"""
    return L.parse_pron(text or "") + read_pron()
PORT = int(os.environ.get("LM_PORT", "8765"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ----------------------------------------------------------------------
# 목소리 목록
# ----------------------------------------------------------------------
_VOICES = None
_VOICES_LOCK = threading.Lock()


def load_voices():
    global _VOICES
    with _VOICES_LOCK:
        if _VOICES is not None:
            return _VOICES
        voices = []
        try:
            voices = asyncio.run(L.load_available_voices())
        except Exception as e:
            print("목소리 목록을 온라인으로 받지 못했습니다: %s" % e)
        source = "online"
        if not voices:
            source = "fallback"
            p = os.path.join(HERE, "voices_fallback.json")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    voices = json.load(f)
        _VOICES = {"source": source, "voices": voices}
        return _VOICES


def voice_list_for_ui():
    d = load_voices()
    out = []
    for v in d["voices"]:
        out.append({"name": v["ShortName"], "gender": v["Gender"], "locale": v["Locale"],
                    "localeName": v.get("LocaleName", v["Locale"])})
    return {"source": d["source"], "voices": out}


# ----------------------------------------------------------------------
# 작업(제작) 관리
# ----------------------------------------------------------------------
class Job:
    def __init__(self, jid):
        self.id = jid
        self.status = "running"
        self.log = []
        self.total = 0
        self.done = 0
        self.error = ""
        self.started = time.time()
        self.out = os.path.join(OUT_ROOT, jid)
        self._partial = ""
        self.reused = 0
        self.result = None

    def write(self, s):
        self._partial += s
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            if line.strip():
                self.log.append(line.rstrip())

    def flush(self):
        pass


JOBS = {}
JOB_LOCK = threading.Lock()


def any_running():
    return any(j.status == "running" for j in JOBS.values())


def parse_for_ui(text):
    items, warnings = L.parse_input(text)
    avail = load_voices()["voices"]
    out = []
    for it in items:
        roles = []
        for ln in it["lines"]:
            if ln["spk"] not in roles:
                roles.append(ln["spk"])
        voices = {r: L.voice_for(it, r, avail) for r in roles}
        out.append({
            "id": it["id"], "title": it["title"], "accents": it["accents"], "hash": item_hash(it),
            "rate": L.rate_pct(it["rate"]), "repeat": min(2, it["repeat"]),
            "roles": roles, "voices": voices,
            "lines": [{"spk": ln["spk"], "text": ln["text"]} for ln in it["lines"]],
        })
    checks = L.rule_check(text)
    names = {v["ShortName"] for v in avail}
    for it in items:
        for role, v in it.get("script_voices", {}).items():
            if names and v not in names:
                checks.append({"id": it["id"], "level": "오류", "message": "대본에 적은 목소리 '%s'를 찾을 수 없습니다. (예: en-US-AriaNeural)" % v})
            if role not in {ln["spk"] for ln in it["lines"]}:
                checks.append({"id": it["id"], "level": "주의", "message": "목소리%s를 적었지만 이 문항에 %s 대사가 없습니다." % (role, role)})
    for o_, it in zip(out, items):
        o_["script_voices"] = it.get("script_voices", {})
    for e in read_pron():
        if e.get("word") and e.get("say") and not any(L.apply_pron(ln["text"], [e], it["id"]) != ln["text"] for it in items for ln in it["lines"]):
            checks.append({"id": e.get("only", ""), "level": "주의", "message": "발음 사전의 '%s'가 이 대본에 그대로 나오지 않습니다. (다른 대본용이면 그대로 두셔도 됩니다)" % e["word"]})
    return {"items": out, "warnings": warnings, "checks": checks, "pron_script": L.parse_pron(text)}


def item_hash(it):
    """문항 대본의 지문. 문항을 만든 뒤 대본이 바뀌었는지 알아낼 때 씁니다."""
    s = "\n".join("%s:%s" % (ln["spk"], re.sub(r"\s+", " ", ln["text"].strip())) for ln in it["lines"])
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


# ----------------------------------------------------------------------
# AI 설정 / 줄 단위 작업 / 내보내기
# ----------------------------------------------------------------------
def read_ai():
    try:
        with open(AI_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    keys = [k for k in d.get("keys") or [] if k]
    if d.get("key") and d["key"] not in keys:   # 예전 방식(키 1개) 저장 파일
        keys.insert(0, d["key"])
    d["keys"] = keys
    d.pop("key", None)
    return d


def split_keys(text):
    out = []
    for k in re.split(r"[\s,;]+", text or ""):
        k = k.strip()
        if k and k not in out:
            out.append(k)
    return out


def write_ai(d):
    os.makedirs(os.path.dirname(AI_FILE), exist_ok=True)
    with open(AI_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def ai_public():
    d = read_ai()
    ks = d["keys"]
    return {"hasKey": bool(ks), "count": len(ks), "masked": ", ".join(AI.mask(k) for k in ks),
            "model": d.get("model") or AI.DEFAULT_MODEL}


def find_rows(job, key):
    return [r for r in (job.result or {}).get("rows", []) if r.get("_key") == key]


def status_from_meta(meta, spk):
    if spk == "KO":
        return "한국어안내"
    if meta.get("checked"):
        return "통과" if meta.get("ok") else "확인필요"
    return "검수안함"


def approve_line(job, key, approved):
    hit = L.cache_get(CACHE_DIR, key)
    if not hit:
        raise ValueError("저장된 음성을 찾지 못했습니다. 음원을 다시 만든 뒤 해 주세요.")
    meta, mp3 = hit
    meta["approved"] = bool(approved)
    L.cache_put(CACHE_DIR, key, mp3, meta)
    for r in find_rows(job, key):
        r["상태"] = "교사확인" if approved else status_from_meta(meta, r["화자"])
    L.recompute(job.result)
    L.write_reports(job.out, job.result)


def retry_line(job, key, model):
    """문제 줄을 속도를 조금씩 바꿔 다시 만들고, 가장 잘 들리는 것을 저장한다. (같은 조건이면 같은 음성이 나오므로)"""
    rows = find_rows(job, key)
    if not rows:
        raise ValueError("해당 줄을 찾지 못했습니다.")
    row = rows[0]
    text, voice, base = row["대본"], row["목소리"], row.get("_rate", "+0%")
    spoken = row.get("_spoken") or text
    L.get_model(model)
    tmp = os.path.join(job.out, "_임시")
    os.makedirs(tmp, exist_ok=True)
    cur = L.cache_get(CACHE_DIR, key)
    cur_sim = (cur[0].get("sim") or 0) if cur and cur[0].get("checked") else -1
    best = None
    try:
        for n, d in enumerate((-6, 6, -10, 10), 1):
            rate = L.norm_rate(L.rate_pct(base) + d)
            p = os.path.join(tmp, "retry_%s_%d.mp3" % (key, n))
            asyncio.run(L.synth(spoken, voice, rate, p))
            samples = L.load_audio(p, L.SR)
            heard = L.transcribe_samples(L.load_audio(p, 16000), model)
            ok, sim, dur, reasons = L.evaluate2(text, spoken, heard, len(samples) / float(L.SR), rate)
            cand = {"ok": ok, "sim": sim, "dur": dur, "heard": heard, "reasons": reasons, "file": p, "rate": rate, "n": n}
            if best is None or (ok and not best["ok"]) or (ok == best["ok"] and sim > best["sim"]):
                best = cand
            if ok:
                break
        improved = best["ok"] or best["sim"] > cur_sim
        if improved:
            L.cache_put(CACHE_DIR, key, best["file"], {
                "approved": False, "text": text, "voice": voice, "base_rate": base, "used_rate": best["rate"],
                "checked": True, "model": model, "ok": best["ok"], "sim": best["sim"], "dur": best["dur"],
                "heard": best["heard"], "reasons": best["reasons"], "attempt": best["n"]})
            for r in rows:
                r.update({"상태": "통과" if best["ok"] else "확인필요", "출처": "다시 만듦(교사 요청)",
                          "유사도(%)": round(best["sim"] * 100), "길이비율(%)": round(best["dur"] * 100),
                          "사유": "; ".join(best["reasons"]), "받아쓰기": best["heard"], "_changed": True})
            job.result["needs_reassemble"] = True
            L.recompute(job.result)
            L.write_reports(job.out, job.result)
    finally:
        for n in os.listdir(tmp):
            if n.startswith("retry_"):
                try:
                    os.remove(os.path.join(tmp, n))
                except OSError:
                    pass
    return {"improved": improved, "ok": best["ok"], "sim": round(best["sim"] * 100), "heard": best["heard"],
            "rate": best["rate"]}


VARIANTS = [("조금 느리게", -6, "+0Hz"), ("조금 빠르게", 6, "+0Hz"),
            ("목소리 조금 낮게", 0, "-6Hz"), ("목소리 조금 높게", 0, "+6Hz")]
VAR_DIR = None


def var_dir():
    d = os.path.join(CACHE_DIR, "_후보")
    os.makedirs(d, exist_ok=True)
    return d


def make_variants(job, key, model):
    """한 줄을 여러 버전(속도·높낮이)으로 만들어 교사가 들어보고 고를 수 있게 한다."""
    rows = find_rows(job, key)
    if not rows:
        raise ValueError("해당 줄을 찾지 못했습니다.")
    row = rows[0]
    text, voice, base = row["대본"], row["목소리"], row.get("_rate", "+0%")
    spoken = row.get("_spoken") or text
    check = row["화자"] != "KO"
    if check:
        try:
            L.get_model(model)
        except Exception:
            check = False
    d = var_dir()
    out = []
    for n, (label, delta, pitch) in enumerate(VARIANTS, 1):
        rate = L.norm_rate(L.rate_pct(base) + delta)
        p = os.path.join(d, "%s_%d.mp3" % (key, n))
        asyncio.run(L.synth(spoken, voice, rate, p, pitch=pitch))
        samples = L.load_audio(p, L.SR)
        e = {"n": n, "label": label, "rate": rate, "pitch": pitch, "seconds": round(len(samples) / float(L.SR), 1),
             "url": "/api/variant/%s_%d" % (key, n)}
        if check:
            heard = L.transcribe_samples(L.load_audio(p, 16000), model)
            ok, sim, dur, reasons = L.evaluate2(text, spoken, heard, len(samples) / float(L.SR), rate)
            e.update({"ok": ok, "sim": round(sim * 100), "dur": dur, "heard": heard, "reasons": reasons, "model": model})
        out.append(e)
    with open(os.path.join(d, key + ".json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return out


def choose_variant(job, key, n):
    """교사가 고른 버전을 이 줄의 음성으로 저장 (다음 조립부터 사용)."""
    rows = find_rows(job, key)
    d = var_dir()
    with open(os.path.join(d, key + ".json"), "r", encoding="utf-8") as f:
        info = json.load(f)
    e = [x for x in info if x["n"] == int(n)][0]
    p = os.path.join(d, "%s_%d.mp3" % (key, int(n)))
    row = rows[0]
    L.cache_put(CACHE_DIR, key, p, {
        "approved": True, "text": row["대본"], "voice": row["목소리"], "base_rate": row.get("_rate", "+0%"),
        "used_rate": e["rate"], "pitch": e["pitch"], "checked": "ok" in e, "model": e.get("model"),
        "ok": e.get("ok"), "sim": (e["sim"] / 100.0) if "sim" in e else None, "dur": e.get("dur"),
        "heard": e.get("heard", ""), "reasons": e.get("reasons", []), "attempt": "교사 선택: " + e["label"]})
    for r in rows:
        r.update({"상태": "교사확인" if r["화자"] != "KO" else "한국어안내", "출처": "교사가 고른 버전(%s)" % e["label"],
                  "받아쓰기": e.get("heard", r.get("받아쓰기", "")), "_changed": True})
    job.result["needs_reassemble"] = True
    L.recompute(job.result)
    L.write_reports(job.out, job.result)
    return {"ok": True, "label": e["label"]}


def latest_job_dir():
    best, t = None, 0
    if os.path.isdir(OUT_ROOT):
        for n in os.listdir(OUT_ROOT):
            p = os.path.join(OUT_ROOT, n, "_결과정보.json")
            if os.path.exists(p) and os.path.getmtime(p) > t:
                best, t = os.path.join(OUT_ROOT, n), os.path.getmtime(p)
    return best


EXPORTS = {}


def do_export(data):
    text = data.get("text", "")
    items, _ = L.parse_input(text)
    jid = data.get("job") or ""
    job_out, result = None, None
    if data.get("audio", True):
        if jid in JOBS and JOBS[jid].status != "running":
            job_out = JOBS[jid].out
            result = JOBS[jid].result
        else:
            job_out = latest_job_dir()
        if job_out and result is None:
            try:
                with open(os.path.join(job_out, "_결과정보.json"), "r", encoding="utf-8") as f:
                    result = json.load(f)
            except Exception:
                result = None
    qs = {}
    for k, q in (data.get("questions") or {}).items():
        if q and q.get("approved"):
            q = dict(q)
            q["choices"] = (list(q.get("choices") or []) + [""] * 5)[:5]
            try:
                q["answer"] = int(q.get("answer") or 0)
            except Exception:
                q["answer"] = 0
            qs[k] = q
    eid = uuid.uuid4().hex[:10]
    d = os.path.join(OUT_ROOT, "_exports")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, eid + ".zip")
    info = EX.build(path, data.get("title") or "영어 듣기평가", items, qs, text, job_out, result,
                    include_srt=data.get("srt", True))
    if job_out:
        info["audio_from"] = time.strftime("%m월 %d일 %H:%M", time.localtime(os.path.getmtime(job_out)))
    name = re.sub(r'[\\/:*?"<>|]+', "_", (data.get("title") or "영어 듣기평가").strip()) + "_평가자료.zip"
    EXPORTS[eid] = (path, name)
    return dict(info, url="/api/export/" + eid, name=name)


def apply_cfg(text, cfg_items):
    """화면에서 고른 목소리·속도·재생 횟수를 대본 문항에 적용한다."""
    items, _ = L.parse_input(text)
    for i, it in enumerate(items):
        cfg = cfg_items[i] if i < len(cfg_items) else {}
        if "rate" in cfg:
            it["rate"] = L.norm_rate(cfg["rate"])
        if "repeat" in cfg:
            it["repeat"] = max(1, min(2, int(cfg["repeat"])))
        if cfg.get("voices"):
            v = dict(it.get("script_voices") or {})
            v.update({k: x for k, x in cfg["voices"].items() if x})   # 화면에서 고른 목소리가 우선
            it["voices"] = v
    return items


def make_args(job_out, opts):
    mode = opts.get("combine", "none")
    return argparse.Namespace(
        input=None, out=job_out, model=opts.get("model") or "base.en",
        format=opts.get("format", "mp3"), skip_check=not opts.get("check", True),
        only=None, dry_run=False, keep_temp=False, repeat=None,
        combine=mode in ("both", "only"), combine_only=(mode == "only"),
        item_gap=float(opts.get("itemGap", 5)),
        cache_dir=CACHE_DIR, force_new=not opts.get("reuse", True),
    )


def plan(text, cfg_items, opts):
    """만들기 전에 '새로 만들 줄 / 재사용할 줄'을 미리 알려 준다. (인터넷 사용 없음)"""
    items = apply_cfg(text, cfg_items)
    avail = load_voices()["voices"]
    check_on = opts.get("check", True)
    model = opts.get("model") or "base.en"
    force = not opts.get("reuse", True)
    pron = merged_pron(text)
    per_item, tot = [], {"new": 0, "reuse": 0, "recheck": 0, "pron": 0}
    for it in items:
        c = {"id": it["id"], "new": 0, "reuse": 0, "recheck": 0, "pron": 0}
        for ln in it["lines"]:
            voice = L.voice_for(it, ln["spk"], avail)
            spoken = L.apply_pron(ln["text"], pron, it["id"])
            k = L.classify_line(CACHE_DIR, spoken, voice, it["rate"], check_on and ln["spk"] != "KO", model, force)
            c[k] += 1
            tot[k] += 1
            if spoken != ln["text"]:
                c["pron"] += 1
                tot["pron"] += 1
        per_item.append(c)
    return {"items": per_item, "total": tot}


def read_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_state(data):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


def clear_cache():
    n = 0
    if os.path.isdir(CACHE_DIR):
        for name in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, name)
            if os.path.isfile(p) and name.endswith((".mp3", ".json", ".part")):
                os.remove(p)
                n += 1
    return n


def run_job(job, text, cfg_items, opts):
    os.makedirs(job.out, exist_ok=True)
    try:
        with contextlib.redirect_stdout(job):
            items = apply_cfg(text, cfg_items)
            job.total = sum(len(it["lines"]) for it in items)
            args = make_args(job.out, opts)
            args.pron = merged_pron(text)

            def hook(event, payload):
                if event == "line":
                    job.done += 1
                    if str(payload.get("출처", "")).startswith("재사용"):
                        job.reused += 1
            L.HOOK = hook
            job.result = asyncio.run(L.produce(items, args))
        ov = (job.result or {}).get("overall", "error")
        job.status = {"error": "error", "partial": "partial"}.get(ov, "done")
        if ov == "error":
            job.error = "; ".join("문항 %s: %s" % (f["id"], f["why"]) for f in job.result["failed"][:3])
    except SystemExit:
        job.status = "error"
        job.error = "필요한 프로그램이 설치되어 있지 않습니다. 1_처음한번_설치.bat 을 먼저 실행해 주세요."
    except Exception as e:
        job.status = "error"
        job.error = "%s" % e
        job.log.append(traceback.format_exc())


def job_files(job):
    files = []
    if os.path.isdir(job.out):
        for n in sorted(os.listdir(job.out)):
            p = os.path.join(job.out, n)
            if os.path.isfile(p) and not n.startswith("_"):
                files.append({"name": n, "size": os.path.getsize(p)})
    return files


def job_report(job):
    summary, problems = "", []
    sp = os.path.join(job.out, "요약.txt")
    if os.path.exists(sp):
        with open(sp, "r", encoding="utf-8-sig") as f:
            summary = f.read()
    cp = os.path.join(job.out, "검수보고서.csv")
    rows = []
    if os.path.exists(cp):
        with open(cp, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    if job.result is None:
        rp = os.path.join(job.out, "_결과정보.json")
        if os.path.exists(rp):
            with open(rp, "r", encoding="utf-8") as f:
                job.result = json.load(f)
    if job.result and job.result.get("rows") is not None:
        rows = job.result["rows"]
    problems = [r for r in rows if r.get("상태") in ("확인필요", "검수실패", "교사확인")]
    ep = os.path.join(job.out, "오류기록.txt")
    err = ""
    if os.path.exists(ep):
        with open(ep, "r", encoding="utf-8-sig") as f:
            err = f.read()
    return summary, problems, err, len(rows)


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------
class Server(ThreadingHTTPServer):
    # 윈도우에서는 이미 쓰이는 포트에도 겹쳐 열려 버리므로, 재사용 허용을 끄고 빈 번호를 직접 찾는다
    allow_reuse_address = False
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 5 * 1024 * 1024:
            raise ValueError("요청이 너무 큽니다")
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def do_GET(self):
        u = urlparse(self.path)
        path = unquote(u.path)
        try:
            if path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/voices":
                return self._send(200, voice_list_for_ui())
            if path.startswith("/api/job/"):
                jid = path.split("/")[3]
                job = JOBS.get(jid)
                if not job:
                    return self._send(404, {"error": "없는 작업"})
                since = int(parse_qs(u.query).get("since", ["0"])[0])
                body = {"status": job.status, "log": job.log[since:], "logLen": len(job.log),
                        "total": job.total, "done": job.done, "reused": job.reused, "error": job.error,
                        "elapsed": int(time.time() - job.started)}
                if job.status != "running":
                    summary, problems, err, nrows = job_report(job)
                    body.update({"files": job_files(job), "summary": summary, "result": job.result,
                                 "problems": problems, "errorLog": err, "rows": nrows})
                return self._send(200, body)
            if path == "/api/state":
                return self._send(200, read_state())
            if path == "/api/cache":
                return self._send(200, L.cache_info(CACHE_DIR))
            if path == "/api/ai/config":
                return self._send(200, ai_public())
            if path == "/api/pron":
                return self._send(200, {"entries": read_pron()})
            if path.startswith("/api/line/audio/"):
                key = path.rsplit("/", 1)[-1]
                fp = os.path.join(CACHE_DIR, key + ".mp3")
                if not re.fullmatch(r"[0-9a-f]{8,40}", key) or not os.path.isfile(fp):
                    return self._send(404, {"error": "저장된 음성이 없습니다"})
                return self._send_file(fp, key + ".mp3")
            if path.startswith("/api/variant/"):
                vid = path.rsplit("/", 1)[-1]
                fp = os.path.join(CACHE_DIR, "_후보", vid + ".mp3")
                if not re.fullmatch(r"[0-9a-f]{8,40}_[1-9]", vid) or not os.path.isfile(fp):
                    return self._send(404, {"error": "후보 음성이 없습니다"})
                return self._send_file(fp, vid + ".mp3")
            if path.startswith("/api/export/"):
                eid = path.rsplit("/", 1)[-1]
                if eid not in EXPORTS or not os.path.isfile(EXPORTS[eid][0]):
                    return self._send(404, {"error": "내보낸 파일이 없습니다. 다시 만들어 주세요."})
                with open(EXPORTS[eid][0], "rb") as f:
                    data = f.read()
                from urllib.parse import quote
                return self._send(200, data, "application/zip",
                                  {"Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(EXPORTS[eid][1])})
            if path.startswith("/api/zip/"):
                jid = path.split("/")[3]
                job = JOBS.get(jid)
                if not job:
                    return self._send(404, {"error": "없는 작업"})
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    for fi in job_files(job):
                        z.write(os.path.join(job.out, fi["name"]), fi["name"])
                return self._send(200, buf.getvalue(), "application/zip",
                                  {"Content-Disposition": "attachment; filename*=UTF-8''%s" % "listening_audio.zip"})
            if path.startswith("/files/"):
                parts = path.split("/")
                jid, name = parts[2], os.path.basename("/".join(parts[3:]))
                job = JOBS.get(jid)
                fp = os.path.join(job.out, name) if job else ""
                if not job or not os.path.isfile(fp):
                    return self._send(404, {"error": "파일 없음"})
                return self._send_file(fp, name)
            return self._send(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _send_file(self, fp, name):
        size = os.path.getsize(fp)
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        if fp.endswith(".csv") or fp.endswith(".txt"):
            ctype = "text/plain; charset=utf-8" if fp.endswith(".txt") else "text/csv; charset=utf-8"
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else 0
            end = int(b) if b else size - 1
            end = min(end, size - 1)
            code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        with open(fp, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self._json_body()
            if path == "/api/parse":
                return self._send(200, parse_for_ui(data.get("text", "")))
            if path == "/api/plan":
                return self._send(200, plan(data.get("text", ""), data.get("items", []), data.get("options", {})))
            if path == "/api/state":
                write_state(data)
                return self._send(200, {"ok": True, "savedAt": time.strftime("%H:%M:%S")})
            if path == "/api/pron":
                return self._send(200, {"entries": write_pron(data.get("entries") or [])})
            if path == "/api/ai/config":
                d = read_ai()
                if data.get("clear"):
                    d["keys"] = []
                if data.get("key"):
                    new = split_keys(data["key"])
                    d["keys"] = new + [k for k in d["keys"] if k not in new] if data.get("append") else new
                if data.get("model"):
                    d["model"] = data["model"].strip()
                write_ai(d)
                return self._send(200, ai_public())
            if path in ("/api/ai/prompt", "/api/ai/parse_answer"):
                items, _ = L.parse_input(data.get("text", ""))
                items = [it for it in items if any(ln["spk"] != "KO" for ln in it["lines"])]
                if path.endswith("prompt"):
                    ids = data.get("ids")
                    if ids:
                        items = [it for it in items if it["id"] in ids]
                    if not items:
                        return self._send(400, {"error": "요청문을 만들 문항이 없습니다."})
                    return self._send(200, {"prompt": AI.web_prompt(items), "ids": [it["id"] for it in items]})
                try:
                    qs, missing = AI.parse_answer(data.get("answer", ""), items)
                except AI.AIError as e:
                    return self._send(400, {"error": str(e).split("|", 1)[-1]})
                asked = data.get("ids") or []
                missing = [m for m in missing if m in asked] if asked else []
                return self._send(200, {"questions": qs, "missing": missing, "hashes": {it["id"]: item_hash(it) for it in items}})
            if path == "/api/ai/test":
                d = read_ai()
                if not d["keys"]:
                    return self._send(400, {"error": "먼저 API 키를 저장해 주세요."})
                res = AI.test_keys(d["keys"], d.get("model"))
                if data.get("prune"):
                    ok = [k for k, r in zip(d["keys"], res) if r["ok"]]
                    if ok:
                        d["keys"] = ok
                        write_ai(d)
                return self._send(200, {"results": res, "config": ai_public()})
            if path == "/api/ai/models":
                d = read_ai()
                if not d["keys"]:
                    return self._send(400, {"error": "먼저 API 키를 저장해 주세요."})
                try:
                    return self._send(200, {"models": AI.list_models(d["keys"][0])})
                except AI.AIError as e:
                    return self._send(400, {"error": str(e).split("|", 1)[-1]})
            if path == "/api/ai/review":
                d = read_ai()
                if not d["keys"]:
                    return self._send(400, {"error": "먼저 Gemini API 키를 저장해 주세요."})
                items, _ = L.parse_input(data.get("text", ""))
                ids = data.get("ids")
                if ids:
                    items = [it for it in items if it["id"] in ids]
                items = [it for it in items if any(ln["spk"] != "KO" for ln in it["lines"])]
                if not items:
                    return self._send(400, {"error": "점검할 문항이 없습니다."})
                try:
                    qs, used, errs = AI.review(d["keys"], data.get("model") or d.get("model") or AI.DEFAULT_MODEL, items)
                except AI.AIError as e:
                    return self._send(400, {"error": str(e).split("|", 1)[-1]})
                if used != d.get("model"):
                    d["model"] = used
                    write_ai(d)
                hashes = {it["id"]: item_hash(it) for it in items}
                return self._send(200, {"questions": qs, "model": used, "errors": errs, "hashes": hashes})
            if path in ("/api/line/approve", "/api/line/retry", "/api/line/variants", "/api/line/choose"):
                job = JOBS.get(data.get("job", ""))
                if not job or job.status == "running" or not job.result:
                    return self._send(409, {"error": "만든 결과를 찾지 못했거나 아직 만드는 중입니다."})
                if any_running():
                    return self._send(409, {"error": "다른 작업이 진행 중입니다."})
                try:
                    if path.endswith("approve"):
                        approve_line(job, data.get("key", ""), data.get("approved", True))
                        return self._send(200, {"ok": True})
                    if path.endswith("variants"):
                        with JOB_LOCK:
                            return self._send(200, {"variants": make_variants(job, data.get("key", ""), data.get("model") or "base.en")})
                    if path.endswith("choose"):
                        return self._send(200, choose_variant(job, data.get("key", ""), data.get("n", 1)))
                    with JOB_LOCK:
                        r = retry_line(job, data.get("key", ""), data.get("model") or "base.en")
                    return self._send(200, r)
                except Exception as e:
                    return self._send(400, {"error": str(e)})
            if path == "/api/export":
                return self._send(200, do_export(data))
            if path == "/api/cache/clear":
                if any_running():
                    return self._send(409, {"error": "만드는 중에는 비울 수 없습니다."})
                n = clear_cache()
                return self._send(200, dict(L.cache_info(CACHE_DIR), removed=n))
            if path == "/api/generate":
                with JOB_LOCK:
                    if any_running():
                        return self._send(409, {"error": "이미 만드는 중인 작업이 있습니다. 끝난 뒤 다시 눌러 주세요."})
                    jid = uuid.uuid4().hex[:10]
                    job = Job(jid)
                    JOBS[jid] = job
                t = threading.Thread(target=run_job, args=(job, data.get("text", ""), data.get("items", []), data.get("options", {})), daemon=True)
                t.start()
                return self._send(200, {"job": jid})
            if path == "/api/preview":
                voice, text = data.get("voice", ""), (data.get("text") or "").strip()[:300]
                if not voice or not text:
                    return self._send(400, {"error": "목소리와 문장이 필요합니다"})
                rate = L.norm_rate(data.get("rate", 0))
                if not data.get("raw"):
                    text = L.apply_pron(text, merged_pron(data.get("script", "")), data.get("item"))
                tmp = os.path.join(tempfile.gettempdir(), "lm_prev_%s.mp3" % uuid.uuid4().hex[:8])
                try:
                    asyncio.run(L.synth(text, voice, rate, tmp))
                    with open(tmp, "rb") as f:
                        return self._send(200, f.read(), "audio/mpeg")
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            return self._send(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self._send(500, {"error": str(e)})


def main():
    os.makedirs(OUT_ROOT, exist_ok=True)
    srv, port = None, PORT
    for p in range(PORT, PORT + 10):   # 다른 프로그램이 쓰는 주소면 다음 번호를 시도
        try:
            srv = Server(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        print("사용 가능한 주소를 찾지 못했습니다. 이미 켜져 있는 웹앱이 있으면 닫고 다시 실행해 주세요.")
        return
    url = "http://127.0.0.1:%d" % port
    print("=" * 50)
    print(" 듣기 음원 만들기 웹앱")
    print(" 주소: " + url)
    print(" 이 검은 창을 닫으면 웹앱이 종료됩니다.")
    print("=" * 50)
    threading.Thread(target=load_voices, daemon=True).start()
    if not os.environ.get("LM_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
