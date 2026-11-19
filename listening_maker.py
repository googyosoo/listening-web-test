# -*- coding: utf-8 -*-
"""
듣기 음원 자동 제작 + 받아쓰기 검수 (edge-tts + faster-whisper)   v2

  python listening_maker.py make input.txt          대본 -> 음원 제작 -> 자동 검수
  python listening_maker.py verify 음원.wav 대본.txt   이미 만든 음원(예: Gemini TTS)만 검수
  python listening_maker.py make input.txt --dry-run   인터넷 없이 대본만 점검

v2 변경: 음원 읽기를 av 라이브러리로 직접 처리(faster-whisper 내부 읽기 함수가
최신 av와 충돌하던 문제 해결), 한 줄이 실패해도 전체가 멈추지 않음,
실패해도 요약.txt / 검수보고서.csv / 오류기록.txt 는 항상 저장.
"""
import argparse
import asyncio
import csv
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import time
import traceback
import wave

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SR = 24000  # 최종 음원 샘플레이트

ACCENTS = {
    "US": ("en-US", "en-US-GuyNeural", "en-US-JennyNeural"),
    "UK": ("en-GB", "en-GB-RyanNeural", "en-GB-SoniaNeural"),
    "AU": ("en-AU", "en-AU-WilliamNeural", "en-AU-NatashaNeural"),
    "IN": ("en-IN", "en-IN-PrabhatNeural", "en-IN-NeerjaNeural"),
    "IE": ("en-IE", "en-IE-ConnorNeural", "en-IE-EmilyNeural"),
    "CA": ("en-CA", "en-CA-LiamNeural", "en-CA-ClaraNeural"),
    "NZ": ("en-NZ", "en-NZ-MitchellNeural", "en-NZ-MollyNeural"),
    "ZA": ("en-ZA", "en-ZA-LukeNeural", "en-ZA-LeahNeural"),
}
KO_VOICE = ("ko-KR", "ko-KR-InJoonNeural", "ko-KR-SunHiNeural")

SPEAKER_ALIASES = {
    "M": "M", "남": "M", "W": "W", "여": "W",
    "N": "N", "해설": "N", "KO": "KO", "안내": "KO",
}
VOICE_KEYS = {"목소리M": "M", "목소리W": "W", "목소리N": "N", "목소리KO": "KO",
              "남자목소리": "M", "여자목소리": "W", "담화목소리": "N", "안내목소리": "KO"}
LINE_RE = re.compile(r"^(M|W|N|KO|남|여|해설|안내)\s*[:：]\s*(.+)$", re.IGNORECASE)


def spk_of(token):
    return SPEAKER_ALIASES[token.upper() if token.isascii() else token]


# ----------------------------------------------------------------------
# 1. 대본 읽기
# ----------------------------------------------------------------------
def norm_rate(value):
    s = str(value).strip().replace(" ", "")
    m = re.match(r"^([+-]?)(\d+)%?$", s)
    if not m:
        return "+0%"
    sign = m.group(1) or "+"
    return "%s%s%%" % (sign, m.group(2))


def rate_pct(rate):
    return int(rate.replace("%", ""))


def safe_name(text):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_") or "item"


def parse_input(text):
    defaults = {"악센트": "US", "속도": "+0%", "대사간격": "0.7", "반복": "1", "반복간격": "3"}
    items, cur, warnings = [], None, []

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue
        if line.startswith("#") and not line.startswith("##"):
            continue
        if line.startswith("@설정"):
            for k, v in re.findall(r"(\S+?)=(\S+)", line[3:]):
                defaults[k] = v
            continue
        if line.startswith("@"):   # @발음 등 다른 설정 줄은 따로 읽는다
            continue
        if line.startswith("##"):
            parts = [p.strip() for p in line[2:].split("|")]
            opts = dict(defaults)
            title = ""
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    opts[k.strip()] = v.strip()
                elif not title:
                    title = p
            cur = {"id": parts[0].lstrip("#").strip() or str(len(items) + 1), "title": title, "opts": opts, "lines": [], "lineno": lineno}
            items.append(cur)
            continue
        if cur is None:
            warnings.append("%d줄: '## 번호 | 제목' 줄보다 먼저 나온 내용이라 무시했습니다." % lineno)
            continue
        m = LINE_RE.match(line)
        if m:
            cur["lines"].append({"spk": spk_of(m.group(1)), "text": m.group(2).strip(), "lineno": lineno})
        elif cur["lines"]:
            cur["lines"][-1]["text"] += " " + line
            warnings.append("%d줄: 화자 표시(M:, W:, N:, KO:)가 없어 앞 줄에 이어 붙였습니다." % lineno)
        else:
            cur["lines"].append({"spk": "N", "text": line, "lineno": lineno})
            warnings.append("%d줄: 화자 표시가 없어 해설(N)로 처리했습니다." % lineno)

    for it in items:
        o = it["opts"]
        accents = [a.strip().upper() for a in o["악센트"].split("+") if a.strip()]
        for a in accents:
            if a not in ACCENTS:
                warnings.append("문항 %s: 알 수 없는 악센트 '%s' (사용 가능: %s)" % (it["id"], a, ", ".join(ACCENTS)))
        it["accents"] = [a for a in accents if a in ACCENTS] or ["US"]
        # 대본에 직접 적은 목소리 (예: 목소리N=en-US-AriaNeural) - 화면 설정보다 우선
        sv = {}
        for k, role in VOICE_KEYS.items():
            v = (o.get(k) or "").strip()
            if v:
                if re.fullmatch(r"[a-z]{2,3}-[A-Za-z]{2,4}(-[A-Za-z]+)?-[A-Za-z]+", v) and not v.endswith("Neural"):
                    v += "Neural"
                sv[role] = v
        it["script_voices"] = sv
        it["voices"] = dict(sv)
        it["rate"] = norm_rate(o["속도"])
        it["gap"] = float(o["대사간격"])
        it["repeat"] = max(1, int(float(o["반복"])))
        it["repeat_gap"] = float(o["반복간격"])
        if not it["lines"]:
            warnings.append("문항 %s: 대사가 하나도 없습니다." % it["id"])
    return items, warnings


# ----------------------------------------------------------------------
# 2. 검수 로직 (순수 계산)
# ----------------------------------------------------------------------
ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen").split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def n2w(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    if n < 1000:
        return ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + n2w(n % 100))
    if n < 1000000:
        return n2w(n // 1000) + " thousand" + ("" if n % 1000 == 0 else " " + n2w(n % 1000))
    return str(n)


def normalize(text):
    t = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    t = re.sub(r"(\d+)", lambda m: " " + n2w(int(m.group(1))) + " ", t)
    t = re.sub(r"[^a-z' ]", " ", t)
    return t.split()


# ----------------------------------------------------------------------
# 발음 사전: 음성을 만들 때만 철자를 바꿔 읽힌다 (대본·문제지·자막은 그대로)
# ----------------------------------------------------------------------
PRON_RE = re.compile(r"^@발음(?:\[\s*(\w+)\s*\])?\s+(.+?)\s*=\s*(.+?)\s*$")


def parse_pron(text):
    """대본의 '@발음 단어 = 읽는 방식' / '@발음[03] read = red' 줄을 읽는다."""
    out = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        m = PRON_RE.match(line) if line.startswith("@발음") else None
        if m:
            out.append({"word": m.group(2), "say": m.group(3), "only": m.group(1) or "", "source": "대본"})
    return out


def apply_pron(text, entries, item_id=None):
    """사전에 있는 단어를 읽는 방식으로 바꾼 문장(음성용)을 돌려준다. 단어 단위, 대소문자 구분, 한 번에 바꾼다."""
    m = {}
    for e in entries or []:
        w, s = (e.get("word") or "").strip(), (e.get("say") or "").strip()
        only = str(e.get("only") or "").strip()
        if not w or not s or w == s:
            continue
        if only and (item_id is None or only.lstrip("0") != str(item_id).strip().lstrip("0")):
            continue
        m.setdefault(w, s)
    if not m:
        return text
    pats = []
    for w in sorted(m, key=len, reverse=True):
        pats.append(("(?<![A-Za-z0-9])" if w[0].isalnum() else "") + re.escape(w) + ("(?![A-Za-z0-9])" if w[-1].isalnum() else ""))
    return re.sub("|".join(pats), lambda mm: m.get(mm.group(0), mm.group(0)), text)


def evaluate2(text, spoken, heard, seconds, rate):
    """원래 대본과 읽힌 문장 중 받아쓰기와 더 잘 맞는 쪽으로 판정 (발음 사전을 쓴 줄)"""
    r = evaluate(text, heard, seconds, rate)
    if spoken and spoken != text:
        r2 = evaluate(spoken, heard, seconds, rate)
        if (r2[0] and not r[0]) or (r2[0] == r[0] and r2[1] > r[1]):
            return r2
    return r


def evaluate(expected, heard, seconds, rate):
    """(통과여부, 유사도, 길이비율, 사유목록)"""
    a, b = normalize(expected), normalize(heard)
    sim = difflib.SequenceMatcher(None, a, b).ratio() if (a or b) else 1.0
    wps = 2.5 * (1 + rate_pct(rate) / 100.0)
    exp_sec = len(a) / wps if a else 0
    dur = (seconds / exp_sec) if exp_sec else 1.0
    reasons = []
    if sim < 0.88:
        reasons.append("대본과 다르게 들림(유사도 %.0f%%)" % (sim * 100))
    if a and len(b) < len(a) * 0.8:
        reasons.append("끝부분이 잘렸을 수 있음")
    if len(a) >= 6 and dur < 0.5:
        reasons.append("음원이 너무 짧음(예상의 %.0f%%)" % (dur * 100))
    if len(a) >= 6 and dur > 2.2:
        reasons.append("음원이 너무 김(예상의 %.0f%%)" % (dur * 100))
    return (not reasons), sim, dur, reasons


# ----------------------------------------------------------------------
# 3. 음성 합성 / 오디오 처리
# ----------------------------------------------------------------------
async def load_available_voices():
    try:
        import edge_tts
        return await edge_tts.list_voices()
    except Exception as e:
        print("  (목소리 목록을 못 불러와 기본 목소리 이름을 그대로 씁니다: %s)" % e)
        return []


def pick_voice(available, spec, gender):
    locale, male, female = spec
    preferred = male if gender == "M" else female
    if not available:
        return preferred
    names = {v["ShortName"] for v in available}
    if preferred in names:
        return preferred
    want = "Male" if gender == "M" else "Female"
    cands = [v["ShortName"] for v in available if v["Locale"] == locale and v["Gender"] == want]
    # 'Multilingual' 목소리는 악센트가 불확실하니 일반 목소리를 먼저 쓴다
    cands.sort(key=lambda n: ("Multilingual" in n, n))
    return cands[0] if cands else preferred


def HOOK(event, payload):
    """웹앱이 진행 상황을 받아 볼 때 덮어쓰는 자리(기본은 아무 동작 안 함)."""
    return None


def voice_for(item, spk, available):
    forced = (item.get("voices") or {}).get(spk)
    if forced:
        return forced
    if spk == "KO":
        return pick_voice(available, KO_VOICE, "F")
    accents = item["accents"]
    if spk == "W":
        return pick_voice(available, ACCENTS[accents[-1]], "W")
    if spk == "N":
        # 한 사람이 말하는 문항은 안내(KO)의 '여자/남자'에 맞춰 목소리 성별을 고른다
        ko = " ".join(ln["text"] for ln in item.get("lines", []) if ln["spk"] == "KO")
        if "여자" in ko and "남자" not in ko:
            return pick_voice(available, ACCENTS[accents[0]], "W")
    return pick_voice(available, ACCENTS[accents[0]], "M")


async def synth(text, voice, rate, path, pitch="+0Hz"):
    import edge_tts
    last = None
    for t in range(4):
        try:
            await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(path)
            if os.path.exists(path) and os.path.getsize(path) > 800:
                return
            last = "빈 음원이 돌아옴"
        except Exception as e:
            last = e
        await asyncio.sleep(2 * (t + 1))
    raise RuntimeError("음성 생성 실패(%s): %s" % (voice, last))


def load_audio(path, sr=SR):
    """mp3/wav 파일을 mono float32 배열로 읽는다. av만 사용(faster-whisper 내부 함수는 쓰지 않음)."""
    import av
    import numpy as np
    chunks = []

    def take(frames):
        if frames is None:
            return
        if not isinstance(frames, (list, tuple)):
            frames = [frames]
        for f in frames:
            chunks.append(f.to_ndarray().reshape(-1))

    container = av.open(path)
    try:
        stream = container.streams.audio[0]
        rs = av.AudioResampler(format="s16", layout="mono", rate=sr)
        for frame in container.decode(stream):
            take(rs.resample(frame))
        take(rs.resample(None))
    finally:
        container.close()
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


_MODEL = {}


def get_model(name):
    if name not in _MODEL:
        from faster_whisper import WhisperModel
        print("  받아쓰기 모델 준비 중: %s (처음 한 번은 내려받느라 몇 분 걸립니다)" % name)
        _MODEL[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _MODEL[name]


def transcribe_samples(samples16k, model_name):
    model = get_model(model_name)
    segs, _ = model.transcribe(samples16k, language="en", beam_size=5, condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segs).strip()


def transcribe(path, model_name):
    return transcribe_samples(load_audio(path, 16000), model_name)


def write_wav(path, samples):
    import numpy as np
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def write_mp3(path, samples):
    import av
    import numpy as np
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    out = av.open(path, "w")
    try:
        stream = out.add_stream("mp3", rate=SR)
        stream.layout = "mono"
        pos = 0
        step = 1152 * 20
        for i in range(0, len(pcm), step):
            chunk = pcm[i:i + step]
            frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = SR
            frame.pts = pos
            pos += len(chunk)
            for p in stream.encode(frame):
                out.mux(p)
        for p in stream.encode(None):
            out.mux(p)
    finally:
        out.close()


def save_final(base_path, samples, fmt):
    """mp3 저장 후 다시 읽어 길이를 확인한다. 이상하면 wav로 저장."""
    if fmt == "mp3":
        try:
            write_mp3(base_path + ".mp3", samples)
            back = load_audio(base_path + ".mp3", SR)
            if abs(len(back) - len(samples)) < SR * 0.5:
                return base_path + ".mp3"
            print("  (mp3 길이가 맞지 않아 wav로 저장합니다: %d초 -> %d초)" % (len(samples) // SR, len(back) // SR))
        except Exception as e:
            print("  (mp3 저장이 안 되어 wav로 저장합니다: %s)" % e)
        try:
            os.remove(base_path + ".mp3")
        except OSError:
            pass
    write_wav(base_path + ".wav", samples)
    return base_path + ".wav"


# ----------------------------------------------------------------------
# 4. make: 제작 + 검수
# ----------------------------------------------------------------------
# ---- 줄 단위 음성 저장소(캐시): 바뀌지 않은 줄은 다시 만들지 않는다 ----
CACHE_VERSION = "1"


def default_cache_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cache")


def line_cache_key(text, voice, rate):
    """대본 문장 + 목소리 + 속도가 같으면 같은 키. 하나라도 바뀌면 새로 만든다."""
    s = "|".join([CACHE_VERSION, voice, norm_rate(rate), re.sub(r"\s+", " ", text.strip())])
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:24]


def cache_get(cache_dir, key):
    if not cache_dir:
        return None
    mp3 = os.path.join(cache_dir, key + ".mp3")
    meta_p = os.path.join(cache_dir, key + ".json")
    if not (os.path.isfile(mp3) and os.path.isfile(meta_p)):
        return None
    try:
        with open(meta_p, "r", encoding="utf-8") as f:
            return json.load(f), mp3
    except Exception:
        return None


def cache_put(cache_dir, key, src, meta):
    os.makedirs(cache_dir, exist_ok=True)
    dst = os.path.join(cache_dir, key + ".mp3")
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copyfile(src, dst + ".part")
        os.replace(dst + ".part", dst)
    meta = dict(meta, saved=time.strftime("%Y-%m-%d %H:%M:%S"))
    with open(os.path.join(cache_dir, key + ".json.part"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(os.path.join(cache_dir, key + ".json.part"), os.path.join(cache_dir, key + ".json"))


def classify_line(cache_dir, text, voice, rate, do_check, model, force_new=False):
    """이번 제작에서 이 줄을 어떻게 처리할지: new(새로 만듦) / reuse(재사용) / recheck(재사용 + 검수만 다시)"""
    hit = None if force_new else cache_get(cache_dir, line_cache_key(text, voice, rate))
    if not hit:
        return "new"
    meta = hit[0]
    if not do_check or (meta.get("checked") and meta.get("model") == model):
        return "reuse"
    return "recheck"


def cache_info(cache_dir):
    n, size = 0, 0
    if cache_dir and os.path.isdir(cache_dir):
        for name in os.listdir(cache_dir):
            p = os.path.join(cache_dir, name)
            if os.path.isfile(p):
                size += os.path.getsize(p)
                if name.endswith(".mp3"):
                    n += 1
    return {"count": n, "bytes": size}


def preflight(args):
    """필요한 프로그램이 모두 있는지 먼저 확인. (검수 사용 가능 여부, 안내 문구)를 돌려준다."""
    missing = []
    for mod in ("edge_tts", "numpy", "av"):
        try:
            __import__(mod)
        except Exception as e:
            missing.append("%s (%s)" % (mod, e))
    if missing:
        print("\n[설치 문제] 필요한 프로그램이 없습니다: " + ", ".join(missing))
        print("1_처음한번_설치.bat 을 먼저 실행해 주세요.")
        sys.exit(1)
    if args.skip_check:
        return False, "받아쓰기 검수를 끄고 만들었습니다."
    try:
        import faster_whisper  # noqa: F401
        get_model(args.model)
        return True, ""
    except Exception as e:
        print("\n[주의] 받아쓰기 모델을 준비하지 못했습니다: %s" % e)
        print("       이번에는 검수 없이 음원만 만듭니다. (인터넷 연결과 설치를 확인해 주세요)")
        return False, "받아쓰기 모델을 준비하지 못해 검수하지 못했습니다: %s" % e


STATUSES = ("통과", "확인필요", "검수실패", "검수안함", "한국어안내", "교사확인")


class Recorder:
    def __init__(self):
        self.rows, self.errors, self.saved = [], [], []
        self.combined = []  # (문항, 완성 오디오)
        self.item_status = []  # 문항별 결과
        self.combined_file = ""
        self.combined_offsets = {}  # 문항 id -> 합본에서의 시작 시각(초)

    def error(self, where, exc=None):
        tb = traceback.format_exc() if exc else ""
        msg = "[%s] %s\n%s" % (where, exc, tb)
        self.errors.append(msg)
        print("  [오류] %s: %s" % (where, exc))


async def make_item(it, args, available, check_on, rec, tmp_dir, out_dir):
    import numpy as np
    seg_audio = []
    cache_dir = getattr(args, "cache_dir", None)
    force_new = getattr(args, "force_new", False)
    st = {"id": it["id"], "title": it["title"], "ok": False, "file": "", "seconds": 0,
          "lines": len(it["lines"]), "new": 0, "reused": 0, "error": ""}
    st.update({s: 0 for s in STATUSES})
    rec.item_status.append(st)
    print("\n=== 문항 %s %s ===" % (it["id"], it["title"]))

    def check(path, samples, rate, cand):
        """받아쓰기 검수 결과를 cand 에 채운다. 실패하면 check_error 를 남긴다."""
        try:
            heard = transcribe_samples(load_audio(path, 16000), args.model)
            ok, sim, dur, reasons = evaluate2(text, spoken, heard, len(samples) / float(SR), rate)
            cand.update(ok=ok, checked=True, sim=sim, dur=dur, heard=heard, reasons=reasons)
        except Exception as e:
            rec.error("받아쓰기 문항 %s %d번째 줄" % (it["id"], idx), e)
            cand.update(ok=None, checked=False, check_error=str(e), reasons=["받아쓰기 실패: %s" % e])
        return cand

    def better(c, b):
        if b is None:
            return True
        cg, bg = bool(c.get("checked") and c["ok"]), bool(b.get("checked") and b["ok"])
        if cg != bg:
            return cg
        return (c.get("sim") or 0) > (b.get("sim") or 0)

    for idx, ln in enumerate(it["lines"], 1):
        spk, text = ln["spk"], ln["text"]
        spoken = apply_pron(text, getattr(args, "pron", None), it["id"])   # 음성으로 읽힐 문장
        voice = voice_for(it, spk, available)
        do_check = check_on and spk != "KO"
        key = line_cache_key(spoken, voice, it["rate"])
        best, source, dirty = None, "새로 만듦", False
        deltas = [0, -3, 3] if do_check else [0]
        hit = None if (force_new or not cache_dir) else cache_get(cache_dir, key)
        cpath, approved = None, False
        if hit:
            meta, cpath = hit
            approved = bool(meta.get("approved"))
            samples = load_audio(cpath, SR)
            if len(samples) >= SR * 0.2:
                c = {"ok": meta.get("ok"), "checked": bool(meta.get("checked")), "sim": meta.get("sim"),
                     "dur": meta.get("dur"), "heard": meta.get("heard", ""), "reasons": meta.get("reasons", []),
                     "samples": samples, "n": meta.get("attempt", 1), "file": cpath,
                     "rate": meta.get("used_rate", it["rate"]), "model": meta.get("model")}
                if not do_check or (c["checked"] and meta.get("model") == args.model):
                    best, source, deltas = c, "재사용", []
                    if not do_check and c["checked"] and spk != "KO":
                        source = "재사용(이전 검수 결과)"
                else:
                    best, source, dirty = check(cpath, samples, c["rate"], c), "재사용(검수만 새로)", True
                    if best.get("checked") and not best["ok"]:
                        deltas = [d for d in (0, -3, 3) if norm_rate(rate_pct(it["rate"]) + d) != best["rate"]]
                        print("  %02d번째 줄: 저장된 음성이 검수를 통과하지 못해 다시 만듭니다" % idx)
                    else:
                        deltas = []
        for n, delta in enumerate(deltas, 1):
            rate = norm_rate(rate_pct(it["rate"]) + delta)
            p = os.path.join(tmp_dir, "%s_%02d_%d.mp3" % (safe_name(it["id"]), idx, n))
            await synth(spoken, voice, rate, p)
            samples = load_audio(p, SR)
            if len(samples) < SR * 0.2:
                raise RuntimeError("만들어진 음원이 비어 있습니다 (문항 %s %d번째 줄)" % (it["id"], idx))
            cand = {"ok": True, "checked": False, "sim": None, "dur": None, "heard": "", "reasons": [],
                    "samples": samples, "n": n, "file": p, "rate": rate}
            if do_check:
                check(p, samples, rate, cand)
            if better(cand, best) or (cand.get("check_error") and best is None):
                best, source, dirty = cand, "새로 만듦", True
            if not do_check or cand.get("check_error") or (cand.get("checked") and cand["ok"]):
                break
            print("  %02d번째 줄 다시 만드는 중 (%d회차: %s)" % (idx, n, "; ".join(cand["reasons"])))
        approved = approved and cpath is not None and best["file"] == cpath   # 음성이 그대로일 때만 교사 확인 유지
        if dirty and cache_dir:
            try:
                cache_put(cache_dir, key, best["file"], {"approved": approved, "spoken": spoken,
                    "text": text, "voice": voice, "base_rate": it["rate"], "used_rate": best["rate"],
                    "checked": bool(best.get("checked")), "model": args.model if best.get("checked") else None,
                    "ok": best.get("ok"), "sim": best.get("sim"), "dur": best.get("dur"),
                    "heard": best.get("heard", ""), "reasons": best.get("reasons", []), "attempt": best["n"]})
            except Exception as e:
                rec.error("음성 저장소에 저장 문항 %s %d번째 줄" % (it["id"], idx), e)
        if spk == "KO":
            status = "한국어안내"
        elif best.get("checked"):
            status = "통과" if best["ok"] else "확인필요"
        elif best.get("check_error"):
            status = "검수실패"
        else:
            status = "검수안함"
        if approved and status in ("확인필요", "검수실패", "검수안함"):
            status = "교사확인"
        st[status] += 1
        st["reused" if source.startswith("재사용") else "new"] += 1
        mark = {"통과": "OK  ", "확인필요": "확인!", "검수안함": "--  ", "한국어안내": "KO  ", "검수실패": "??  ", "교사확인": "교사"}[status]
        print("  [%s] %02d %-2s %s%s" % (mark, idx, spk, text[:60], "  (재사용)" if source.startswith("재사용") else ""))
        sim, dur = best.get("sim"), best.get("dur")
        rec.rows.append({
            "문항": it["id"], "제목": it["title"], "줄": idx, "화자": spk, "목소리": voice,
            "상태": status, "출처": source, "시도횟수": best["n"],
            "유사도(%)": "" if sim is None else round(sim * 100),
            "길이비율(%)": "" if dur is None else round(dur * 100),
            "사유": "; ".join(best.get("reasons") or []), "대본": text, "받아쓰기": best.get("heard", ""),
            "_key": key, "_rate": it["rate"], "_lineno": ln.get("lineno"),
            "_spoken": spoken if spoken != text else "",
        })
        seg_audio.append((spk, best["samples"], text, idx))
        HOOK("line", rec.rows[-1])

    def zeros(sec):
        return np.zeros(int(sec * SR), dtype=np.float32)

    k = 0
    while k < len(seg_audio) and seg_audio[k][0] == "KO":
        k += 1
    intro, body = seg_audio[:k], seg_audio[k:]

    # 이어 붙이면서 줄별 시작·끝 시각을 기록 (자막·구간 재생용)
    pieces, segments, pos = [], [], [0.0]

    def add(arr):
        pieces.append(arr)
        pos[0] += len(arr) / float(SR)

    def add_lines(segs, rep):
        for j, (spk_, s, text_, idx_) in enumerate(segs):
            if j:
                add(zeros(it["gap"]))
            segments.append({"line": idx_, "spk": spk_, "text": text_, "rep": rep,
                             "start": round(pos[0], 3), "end": round(pos[0] + len(s) / float(SR), 3)})
            add(s)

    add(zeros(0.5))
    if intro:
        add_lines(intro, 0)
        add(zeros(1.0))
    for r in range(it["repeat"]):
        if r:
            add(zeros(it["repeat_gap"]))
        add_lines(body, r + 1)
    add(zeros(1.0))
    final = np.concatenate(pieces).astype(np.float32)
    st["segments"] = segments

    rec.combined.append((it, final))
    st["seconds"] = round(len(final) / float(SR), 1)
    if args.combine_only:
        st["ok"], st["file"] = True, "(합본에만 포함)"
        print("  -> 합본에 넣을 준비 완료 (%.0f초)" % (len(final) / float(SR)))
        return
    name = "%s_%s" % (safe_name(it["id"]), safe_name(it["title"])) if it["title"] else safe_name(it["id"])
    saved = save_final(os.path.join(out_dir, name), final, args.format)
    rec.saved.append(saved)
    st["ok"], st["file"] = True, os.path.basename(saved)
    print("  -> 저장: %s (%.0f초)" % (os.path.basename(saved), len(final) / float(SR)))


def fmt_time(sec):
    return "%d:%02d" % (int(sec) // 60, int(sec) % 60)


def save_combined(rec, args, out_dir):
    """모든 문항을 하나의 파일로 이어 붙이고, 문항별 시작 시각 목차를 만든다."""
    import numpy as np
    gap = np.zeros(int(args.item_gap * SR), dtype=np.float32)
    parts, toc, t = [], [], 0.0
    for i, (it, audio) in enumerate(rec.combined):
        if i:
            parts.append(gap)
            t += args.item_gap
        rec.combined_offsets[it["id"]] = round(t, 3)
        toc.append("%s  문항 %s %s" % (fmt_time(t), it["id"], it["title"]))
        parts.append(audio)
        t += len(audio) / float(SR)
    allaudio = np.concatenate(parts).astype(np.float32)
    saved = save_final(os.path.join(out_dir, "00_전체합본"), allaudio, args.format)
    with open(os.path.join(out_dir, "00_전체합본_목차.txt"), "w", encoding="utf-8-sig") as f:
        f.write("\n".join(toc) + "\n\n총 길이 " + fmt_time(t) + "\n")
    rec.saved.append(saved)
    rec.combined_file = os.path.basename(saved)
    print("\n=== 전체 합본 ===\n  -> 저장: %s (총 %s, 문항 사이 %.0f초 간격)" % (os.path.basename(saved), fmt_time(t), args.item_gap))


def build_result(rec, items, args, check_on, check_note, failed_items, t0):
    """화면과 요약에 쓸 '정확한 상태' 정보를 만든다."""
    failed_ids = [fid for fid, _ in failed_items]
    ok_items = [s for s in rec.item_status if s["ok"]]
    result = {
        "items_total": len(items), "items_ok": len(ok_items),
        "failed": [{"id": fid, "why": why} for fid, why in failed_items],
        "items": rec.item_status, "rows": rec.rows,
        "combined_offsets": rec.combined_offsets,
        "new": sum(s["new"] for s in rec.item_status), "reused": sum(s["reused"] for s in rec.item_status),
        "check_requested": not args.skip_check, "check_on": check_on, "check_note": check_note,
        "combine": bool(args.combine), "combined_file": rec.combined_file,
        "combined_missing": failed_ids if args.combine else [],
        "saved": [os.path.basename(s) for s in rec.saved],
        "seconds": int(time.time() - t0), "errors": len(rec.errors),
    }
    return recompute(result)


def recompute(result):
    """줄 상태(rows)를 다시 세어 전체 판정을 매긴다. (교사 확인 표시 뒤에도 사용)"""
    rows = result.get("rows", [])
    counts = {s: sum(1 for r in rows if r["상태"] == s) for s in STATUSES}
    for s in result.get("items", []):
        for k in STATUSES:
            s[k] = sum(1 for r in rows if r["문항"] == s["id"] and r["상태"] == k)
    if result["failed"] and not result["items_ok"]:
        overall = "error"          # 하나도 못 만듦
    elif result["failed"]:
        overall = "partial"        # 일부 문항 실패
    elif counts["확인필요"] or counts["검수실패"]:
        overall = "review"         # 음원은 다 만듦, 확인할 줄 있음
    elif counts["검수안함"]:
        overall = "unchecked"      # 음원은 다 만듦, 검수 안 한 줄 있음
    else:
        overall = "passed"         # 검수 대상 전부 통과(또는 교사 확인)
    result["counts"], result["overall"] = counts, overall
    return result


OVERALL_TEXT = {
    "passed": "검수 대상 줄이 모두 통과했습니다. (그래도 한 번쯤은 직접 들어보세요)",
    "review": "음원은 모두 만들었지만, 직접 들어봐야 할 줄이 있습니다.",
    "unchecked": "음원은 모두 만들었지만, 받아쓰기 검수를 하지 않은 줄이 있습니다.",
    "partial": "일부 문항을 만들지 못했습니다.",
    "error": "음원을 하나도 만들지 못했습니다.",
}


def write_reports(out_dir, result, errors=None):
    rows = result.get("rows", [])
    if rows:
        fields = [k for k in rows[0].keys() if not k.startswith("_")]
        with open(os.path.join(out_dir, "검수보고서.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    c = result["counts"]
    lines = ["듣기 음원 제작 요약",
             "결과: " + OVERALL_TEXT[result["overall"]],
             "문항 %d개 중 완성 %d개, 저장된 음원 파일: %s" % (result["items_total"], result["items_ok"], ", ".join(result["saved"]) or "없음"),
             "줄 상태: 통과 %d / 교사 확인 %d / 확인필요 %d / 검수실패 %d / 검수 안 함 %d / 한국어 안내(검수 대상 아님) %d"
             % (c["통과"], c["교사확인"], c["확인필요"], c["검수실패"], c["검수안함"], c["한국어안내"]),
             "새로 만든 줄 %d / 이전 음성 재사용 %d" % (result["new"], result["reused"]),
             "걸린 시간 약 %d초" % result["seconds"], ""]
    if result["check_note"]:
        lines += ["[검수] " + result["check_note"], ""]
    if result["failed"]:
        lines.append("만들지 못한 문항 (오류기록.txt 참고)")
        for f_ in result["failed"]:
            lines.append("- 문항 %s: %s" % (f_["id"], f_["why"]))
        if result["combine"]:
            lines.append("  ※ 전체 합본에서도 위 문항이 빠졌습니다: " + ", ".join(result["combined_missing"]))
        lines.append("")
    problems = [r for r in rows if r["상태"] in ("확인필요", "검수실패")]
    if problems:
        lines.append("직접 들어보셔야 할 줄")
        for r in problems:
            lines.append("- 문항 %s %d번째 줄 (%s, %s): %s" % (r["문항"], r["줄"], r["화자"], r["상태"], r["사유"]))
            lines.append("    대본    : " + r["대본"])
            lines.append("    받아쓰기: " + r["받아쓰기"])
    with open(os.path.join(out_dir, "요약.txt"), "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines))
    with open(os.path.join(out_dir, "_결과정보.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    if errors:
        with open(os.path.join(out_dir, "오류기록.txt"), "w", encoding="utf-8-sig") as f:
            f.write("\n\n".join(errors))
    return lines


# ----------------------------------------------------------------------
# 자막(SRT)
# ----------------------------------------------------------------------
SPK_LABEL = {"M": "(남) ", "W": "(여) ", "N": "", "KO": ""}


def srt_time(t):
    ms = int(round(t * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def make_srt(segments, offset=0.0, start_no=1):
    out, n = [], start_no
    for s in segments:
        out.append("%d\n%s --> %s\n%s%s\n" % (n, srt_time(s["start"] + offset), srt_time(s["end"] + offset),
                                            SPK_LABEL.get(s["spk"], ""), s["text"]))
        n += 1
    return "\n".join(out), n


# ----------------------------------------------------------------------
# 대본 규칙 점검 (인터넷·AI 없이)
# ----------------------------------------------------------------------
HANGUL = re.compile(r"[\uac00-\ud7a3]")


def rule_check(text):
    """제작 전에 기계적으로 확인할 수 있는 문제를 찾는다. [{id, level(오류/주의), message}]"""
    items, warnings = parse_input(text)
    out = [{"id": "", "level": "주의", "message": w} for w in warnings]
    for lineno, raw in enumerate(text.splitlines(), 1):
        s = raw.strip().lstrip("\ufeff")
        if s.startswith("@발음") and not PRON_RE.match(s):
            out.append({"id": "", "level": "주의", "message": "%d줄: 발음 사전 형식이 맞지 않습니다. 예: @발음 Vance = Vans  (특정 문항만: @발음[03] read = red)" % lineno})
        elif s.startswith("@") and not s.startswith(("@설정", "@발음")):
            out.append({"id": "", "level": "주의", "message": "%d줄: '@'로 시작하는 줄은 @설정 또는 @발음만 쓸 수 있어 무시했습니다." % lineno})
    seen = {}
    for it in items:
        iid = it["id"]
        if iid in seen:
            out.append({"id": iid, "level": "오류", "message": "문항 번호 %s가 겹칩니다. (저장 설정·파일 이름이 섞일 수 있습니다)" % iid})
        seen[iid] = True
        lines = it["lines"]
        ko = [ln for ln in lines if ln["spk"] == "KO"]
        en = [ln for ln in lines if ln["spk"] != "KO"]
        spk = set(ln["spk"] for ln in en)
        if not en:
            out.append({"id": iid, "level": "오류", "message": "영어 대사(M/W/N)가 없습니다."})
        if not ko:
            out.append({"id": iid, "level": "주의", "message": "한국어 안내(KO)가 없습니다. 번호·발문 없이 영어만 나옵니다."})
        kot = " ".join(ln["text"] for ln in ko)
        m = re.match(r"\s*(\d+)\s*번", kot)
        if m and iid.isdigit() and int(m.group(1)) != int(iid):
            out.append({"id": iid, "level": "주의", "message": "문항 번호는 %s인데 안내는 '%s번'으로 읽힙니다." % (iid, m.group(1))})
        # 해설(N) 문항은 목소리 성별과 안내가 맞는지 화면에서 따로 확인한다 (고른 목소리에 따라 달라지므로)
        if "여자" in kot and not (spk & {"W", "N"}):
            out.append({"id": iid, "level": "오류", "message": "안내에 '여자'가 나오지만 여자(W) 대사가 없습니다."})
        if "남자" in kot and not (spk & {"M", "N"}):
            out.append({"id": iid, "level": "오류", "message": "안내에 '남자'가 나오지만 남자(M) 대사가 없습니다."})
        if "대화를" in kot and len(spk & {"M", "W"}) < 2:
            out.append({"id": iid, "level": "주의", "message": "안내는 '대화를 듣고'인데 두 사람(M, W)의 대화가 아닙니다."})
        if "다음을 듣고" in kot and {"M", "W"} <= spk:
            out.append({"id": iid, "level": "주의", "message": "안내는 '다음을 듣고'(한 사람의 담화)인데 남녀 대화입니다. 발문을 확인해 주세요."})
        for ln in en:
            if HANGUL.search(ln["text"]):
                out.append({"id": iid, "level": "주의", "message": "%d줄(%s): 영어 대사에 한글이 있습니다. KO 표시를 빠뜨렸는지 확인해 주세요." % (ln["lineno"], ln["spk"])})
            if len(ln["text"]) > 900:
                out.append({"id": iid, "level": "주의", "message": "%d줄: 한 줄이 매우 깁니다(%d자). 검수에서 끊김이 보이면 두세 줄로 나눠 주세요." % (ln["lineno"], len(ln["text"]))})
        for ln in ko:
            if not HANGUL.search(ln["text"]):
                out.append({"id": iid, "level": "주의", "message": "%d줄: KO(한국어 안내)인데 한글이 없습니다." % ln["lineno"]})
        if int(float(it["opts"].get("반복", "1"))) > 2:
            out.append({"id": iid, "level": "주의", "message": "반복은 1 또는 2만 쓸 수 있습니다. (웹앱에서는 2회로 만듭니다)"})
    for e in parse_pron(text):
        used = any(apply_pron(ln["text"], [e], it["id"]) != ln["text"] for it in items for ln in it["lines"])
        if not used:
            out.append({"id": e["only"], "level": "주의", "message": "발음 사전의 '%s'가 대본%s에 그대로 나오지 않습니다. 대본에 적힌 철자(대소문자·마침표)와 같게 써 주세요." % (e["word"], (" " + e["only"] + "번 문항") if e["only"] else "")})
    return out


async def make(args):
    with open(args.input, "r", encoding="utf-8-sig") as f:
        script_text = f.read()
    items, warnings = parse_input(script_text)
    args.pron = parse_pron(script_text)

    print("대본에서 %d개 문항을 찾았습니다." % len(items))
    for w in warnings:
        print("  [주의] " + w)
    if args.only:
        wanted = set(x.strip() for x in args.only.split(","))
        items = [it for it in items if it["id"] in wanted]
        print("  --only 적용: %d개 문항만 만듭니다." % len(items))
    if args.repeat:
        for it in items:
            it["repeat"] = args.repeat
        print("  본문 반복: 모든 문항 %d회로 통일합니다. (대본의 반복= 설정은 무시)" % args.repeat)
    if args.combine_only:
        args.combine = True

    if args.dry_run:
        for it in items:
            print("- %s | %s | 악센트 %s | 속도 %s | 반복 %d회 | 대사 %d줄"
                  % (it["id"], it["title"], "+".join(it["accents"]), it["rate"], it["repeat"], len(it["lines"])))
            for ln in it["lines"]:
                print("    %-2s %s" % (ln["spk"], ln["text"][:70]))
        print("\n(점검만 했습니다. 음원은 만들지 않았습니다.)")
        return

    args.cache_dir = None if args.no_cache else default_cache_dir()
    args.force_new = args.fresh
    await produce(items, args)


async def produce(items, args):
    """파싱된 문항 목록을 받아 음원 제작 + 검수 + 보고서 작성 (명령창과 웹앱이 공통 사용)."""
    if args.combine_only:
        args.combine = True
    check_on, check_note = preflight(args)
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    tmp_dir = os.path.join(out_dir, "_임시")
    os.makedirs(tmp_dir, exist_ok=True)

    available = await load_available_voices()
    rec = Recorder()
    failed_items = []
    t0 = time.time()
    result = None
    try:
        for it in items:
            try:
                await make_item(it, args, available, check_on, rec, tmp_dir, out_dir)
            except Exception as e:
                rec.error("문항 %s 제작" % it["id"], e)
                failed_items.append((it["id"], str(e)))
                for s in rec.item_status:
                    if s["id"] == it["id"] and not s["ok"]:
                        s["error"] = str(e)
        if args.combine and rec.combined:
            try:
                save_combined(rec, args, out_dir)
            except Exception as e:
                rec.error("합본 만들기", e)
                failed_items.append(("전체합본", str(e)))
    finally:
        result = build_result(rec, items, args, check_on, check_note, failed_items, t0)
        lines = write_reports(out_dir, result, rec.errors)
        print("\n" + "\n".join(lines))
        print("\n결과 폴더: " + os.path.abspath(out_dir))
        if not args.keep_temp:
            shutil.rmtree(tmp_dir, ignore_errors=True)   # 줄별 음성은 저장소(_cache)에 남아 있음
    return result


# ----------------------------------------------------------------------
# 5. verify: 이미 만든 음원만 검수
# ----------------------------------------------------------------------
def script_to_plain(text):
    out = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or line.startswith("@"):
            continue
        m = LINE_RE.match(line)
        if m:
            if spk_of(m.group(1)) == "KO":
                continue
            out.append(m.group(2).strip())
        else:
            out.append(line)
    return " ".join(out)


def verify(args):
    with open(args.script, "r", encoding="utf-8-sig") as f:
        expected = script_to_plain(f.read())
    audio = load_audio(args.audio, 16000)
    rep = max(1, args.repeat)
    sec = len(audio) / 16000.0
    heard = transcribe_samples(audio, args.model)
    expected = (expected + " ") * rep   # 음원이 본문을 rep번 반복하면 대본도 rep번 비교
    ok, sim, dur, reasons = evaluate(expected, heard, sec, "+0%")
    a, b = normalize(expected), normalize(heard)
    tail = a[-4:]
    if len(a) >= 8 and not all(w in b[-len(tail) - 6:] for w in tail):
        ok = False
        reasons.append("대본의 마지막 부분이 음원 끝에서 들리지 않음")
    print("음원 길이: %.1f초 (본문 %d회 반복 기준)" % (sec, rep))
    print("유사도: %.0f%% / 길이비율: %.0f%%" % (sim * 100, dur * 100))
    print("판정: %s" % ("통과" if ok else "확인필요 - " + "; ".join(reasons)))
    print("\n[대본]\n" + expected + "\n\n[받아쓰기]\n" + heard)


def main():
    ap = argparse.ArgumentParser(description="듣기 음원 자동 제작 + 받아쓰기 검수")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make", help="대본으로 음원 제작 + 검수")
    m.add_argument("input")
    m.add_argument("--out", default="output")
    m.add_argument("--model", default="base.en", help="받아쓰기 모델 (base.en 기본, 더 정확하게는 small.en)")
    m.add_argument("--format", choices=["mp3", "wav"], default="mp3")
    m.add_argument("--skip-check", action="store_true", help="받아쓰기 검수 생략")
    m.add_argument("--only", help="일부 문항만 (예: 01,03)")
    m.add_argument("--dry-run", action="store_true", help="대본만 점검")
    m.add_argument("--keep-temp", action="store_true", help="줄별 임시 음원을 지우지 않음")
    m.add_argument("--repeat", type=int, choices=[1, 2], help="모든 문항의 본문 재생 횟수 (1 또는 2). 없으면 대본의 반복= 설정을 따름")
    m.add_argument("--combine", action="store_true", help="문항별 파일과 함께 전체 합본 파일도 만든다")
    m.add_argument("--combine-only", action="store_true", help="전체 합본 파일만 만든다")
    m.add_argument("--item-gap", type=float, default=5.0, help="합본에서 문항 사이 무음(초), 기본 5")
    m.add_argument("--fresh", action="store_true", help="저장된 음성을 쓰지 않고 모든 줄을 새로 만듦")
    m.add_argument("--no-cache", action="store_true", help="음성 저장소(_cache)를 아예 쓰지 않음")

    v = sub.add_parser("verify", help="이미 만든 음원 검수")
    v.add_argument("audio")
    v.add_argument("script")
    v.add_argument("--model", default="base.en")
    v.add_argument("--repeat", type=int, default=1, help="음원이 같은 내용을 몇 번 반복하는지")

    args = ap.parse_args()
    if args.cmd == "make":
        asyncio.run(make(args))
    else:
        verify(args)


if __name__ == "__main__":
    main()
