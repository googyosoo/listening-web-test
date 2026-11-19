# -*- coding: utf-8 -*-
"""
평가 자료 한 번에 내보내기: 음원 + 학생용 문제지 + 교사용 정답·해설 + 대본·검수 보고서 + 목차 + 자막(SRT)
문제지는 워드(.docx, 한글 프로그램에서도 열림)와 인쇄용 웹 문서(.html) 두 가지로 만듭니다.
추가 설치 없이 파이썬 기본 기능만 사용합니다.
"""
import html
import io
import os
import re
import time
import zipfile

import listening_maker as L
import mobile_exam_generator as MEG

CIRCLED = "①②③④⑤"
SPK_KO = {"M": "남", "W": "여", "N": "담화", "KO": "안내"}


def qnum(item_id, i):
    s = str(item_id).strip()
    return str(int(s)) if s.isdigit() else (s or str(i + 1))


def table_rows(q):
    rows = [r for r in (q.get("table") or "").splitlines() if r.strip()]
    return [[c.strip() for c in r.split("|")] for r in rows]


# ----------------------------------------------------------------------
# 아주 작은 워드(.docx) 작성기
# ----------------------------------------------------------------------
def _x(s):
    return html.escape(str(s), quote=False)


def _run(text, bold=False, size=None, italic=False, color=None):
    rpr = '<w:rFonts w:ascii="Malgun Gothic" w:hAnsi="Malgun Gothic" w:eastAsia="맑은 고딕" w:cs="Malgun Gothic"/>'
    if bold:
        rpr += "<w:b/>"
    if italic:
        rpr += "<w:i/>"
    if color:
        rpr += '<w:color w:val="%s"/>' % color
    if size:
        rpr += '<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (size * 2, size * 2)
    parts = str(text).split("\n")
    body = '<w:br/>'.join('<w:t xml:space="preserve">%s</w:t>' % _x(p) for p in parts)
    return "<w:r><w:rPr>%s</w:rPr>%s</w:r>" % (rpr, body)


def _p(runs, align=None, after=100, keep=False, indent=0):
    ppr = '<w:spacing w:after="%d" w:line="300" w:lineRule="auto"/>' % after
    if align:
        ppr += '<w:jc w:val="%s"/>' % align
    if keep:
        ppr += "<w:keepNext/>"
    if indent:
        ppr += '<w:ind w:left="%d"/>' % indent
    if isinstance(runs, str) and runs and not runs.startswith("<w:r>"):
        runs = _run(runs)   # 글자만 넘겨준 경우 (이미 만든 글자 덩어리는 그대로)
    return "<w:p><w:pPr>%s</w:pPr>%s</w:p>" % (ppr, runs)


def _table(rows, header=True):
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    w = int(9000 / ncol)
    bd = '<w:tblBorders>' + "".join('<w:%s w:val="single" w:sz="6" w:space="0" w:color="888888"/>' % s
                                     for s in ("top", "left", "bottom", "right", "insideH", "insideV")) + '</w:tblBorders>'
    out = ['<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>%s</w:tblPr><w:tblGrid>%s</w:tblGrid>'
           % (bd, "".join('<w:gridCol w:w="%d"/>' % w for _ in range(ncol)))]
    for ri, r in enumerate(rows):
        out.append("<w:tr>")
        for ci in range(ncol):
            cell = r[ci] if ci < len(r) else ""
            shade = '<w:shd w:val="clear" w:color="auto" w:fill="EEF1F7"/>' if (header and ri == 0) else ""
            out.append('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>%s</w:tcPr>%s</w:tc>'
                       % (w, shade, _p(_run(cell, bold=(header and ri == 0), size=10), align="center", after=0)))
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out) + _p("", after=60)


def _pagebreak():
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def write_docx(path_or_buf, body_xml):
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>%s'
           '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
           'w:header="567" w:footer="567" w:gutter="0"/></w:sectPr></w:body></w:document>') % body_xml
    ctypes = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
              '<Default Extension="xml" ContentType="application/xml"/>'
              '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
              '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ctypes)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


# ----------------------------------------------------------------------
# 문제지 내용 (워드 / 웹 문서 공통)
# ----------------------------------------------------------------------
def student_docx(title, entries):
    b = [_p(_run(title, bold=True, size=16), align="center", after=80),
         _p(_run("영어 듣기평가 문제지", size=11, color="555555"), align="center", after=200),
         _p(_run("학번: ________   이름: ______________", size=11), align="right", after=300)]
    for n, item, q in entries:
        b.append(_p(_run("%s. " % n, bold=True, size=11) + _run(q["stem"], size=11), keep=True, after=80))
        if q.get("figure_note"):
            b.append(_p(_run("[그림 자리: 이 곳에 그림을 넣어 주세요]", italic=True, size=10, color="888888"), after=80))
        rows = table_rows(q)
        if rows:
            b.append(_table(rows))
        for i, c in enumerate(q["choices"]):
            b.append(_p(_run("%s %s" % (CIRCLED[i], c), size=11), indent=360, after=40))
        b.append(_p("", after=160))
    return "".join(b)


def teacher_docx(title, entries, items_by_id):
    b = [_p(_run(title + " - 정답과 해설", bold=True, size=16), align="center", after=240),
         _p(_run("정답표", bold=True, size=12), after=80)]
    nums = [e[0] for e in entries]
    ans = [CIRCLED[e[2]["answer"] - 1] if e[2]["answer"] in (1, 2, 3, 4, 5) else "?" for e in entries]
    for s in range(0, len(nums), 10):
        b.append(_table([nums[s:s + 10], ans[s:s + 10]]))
    for n, item, q in entries:
        a = q["answer"]
        b.append(_p(_run("%s. " % n, bold=True, size=12) + _run(q["stem"], bold=True, size=11), keep=True, after=60))
        ans_txt = "%s %s" % (CIRCLED[a - 1], q["choices"][a - 1]) if a in (1, 2, 3, 4, 5) else "미정"
        b.append(_p(_run("정답: ", bold=True, size=10) + _run(ans_txt, size=10), after=40))
        if q.get("explanation"):
            b.append(_p(_run("해설: ", bold=True, size=10) + _run(q["explanation"], size=10), after=40))
        for ev in q.get("evidence") or []:
            mark = "" if ev.get("found", True) else "  (대본에서 그대로 찾지 못함: 확인 필요)"
            b.append(_p(_run("근거: ", bold=True, size=10) + _run('"%s"%s' % (ev["text"], mark), italic=True, size=10), after=40))
        if q.get("figure_note"):
            b.append(_p(_run("그림 설명: ", bold=True, size=10) + _run(q["figure_note"], size=10), after=40))
        rows = table_rows(q)
        if rows:
            b.append(_table(rows))
        b.append(_p(_run("대본", bold=True, size=10, color="3B5BDB"), after=20))
        for ln in item["lines"]:
            b.append(_p(_run("%s: " % SPK_KO.get(ln["spk"], ln["spk"]), bold=True, size=9) + _run(ln["text"], size=9), indent=240, after=10))
        b.append(_p("", after=200))
    return "".join(b)


CSS = """body{font-family:'Malgun Gothic',sans-serif;max-width:800px;margin:30px auto;line-height:1.6;color:#111}
h1{text-align:center;font-size:22px;margin:0}.sub{text-align:center;color:#555;margin:4px 0 16px}
.name{text-align:right;margin-bottom:24px}.q{margin:0 0 22px;break-inside:avoid}.stem{font-weight:bold}
.ch{margin-left:22px}table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #888;padding:4px 10px;text-align:center}
th{background:#eef1f7}.fig{color:#888;font-style:italic;border:1px dashed #aaa;padding:30px;text-align:center;margin:6px 0}
.ans{margin:3px 0}.ev{font-style:italic;color:#333}.warn{color:#b54708}.script{font-size:13px;color:#333;margin-left:16px}
@media print{body{margin:0}.noprint{display:none}}"""


def _html_table(rows):
    if not rows:
        return ""
    h = "<table><tr>" + "".join("<th>%s</th>" % html.escape(c) for c in rows[0]) + "</tr>"
    for r in rows[1:]:
        h += "<tr>" + "".join("<td>%s</td>" % html.escape(c) for c in r) + "</tr>"
    return h + "</table>"


def student_html(title, entries):
    out = ["<!doctype html><html lang='ko'><meta charset='utf-8'><title>%s</title><style>%s</style><body>" % (html.escape(title), CSS),
           "<p class='noprint'>인쇄: Ctrl+P (PDF로 저장도 가능)</p><h1>%s</h1><div class='sub'>영어 듣기평가 문제지</div>" % html.escape(title),
           "<div class='name'>학번: ________ &nbsp; 이름: ______________</div>"]
    for n, item, q in entries:
        out.append("<div class='q'><div class='stem'>%s. %s</div>" % (n, html.escape(q["stem"])))
        if q.get("figure_note"):
            out.append("<div class='fig'>[그림 자리]</div>")
        out.append(_html_table(table_rows(q)))
        out += ["<div class='ch'>%s %s</div>" % (CIRCLED[i], html.escape(c)) for i, c in enumerate(q["choices"])]
        out.append("</div>")
    return "".join(out) + "</body></html>"


def teacher_html(title, entries):
    out = ["<!doctype html><html lang='ko'><meta charset='utf-8'><title>%s 정답과 해설</title><style>%s</style><body>" % (html.escape(title), CSS),
           "<h1>%s - 정답과 해설</h1><div class='sub'>교사용</div>" % html.escape(title),
           _html_table([[e[0] for e in entries], [CIRCLED[e[2]["answer"] - 1] if e[2]["answer"] in (1, 2, 3, 4, 5) else "?" for e in entries]])]
    for n, item, q in entries:
        a = q["answer"]
        out.append("<div class='q'><div class='stem'>%s. %s</div>" % (n, html.escape(q["stem"])))
        out.append("<div class='ans'><b>정답:</b> %s</div>" % (html.escape("%s %s" % (CIRCLED[a - 1], q["choices"][a - 1])) if a in (1, 2, 3, 4, 5) else "미정"))
        if q.get("explanation"):
            out.append("<div class='ans'><b>해설:</b> %s</div>" % html.escape(q["explanation"]))
        for ev in q.get("evidence") or []:
            out.append("<div class='ans ev'><b>근거:</b> \"%s\"%s</div>" % (html.escape(ev["text"]), "" if ev.get("found", True) else " <span class='warn'>(대본에서 그대로 찾지 못함)</span>"))
        if q.get("figure_note"):
            out.append("<div class='ans'><b>그림 설명:</b> %s</div>" % html.escape(q["figure_note"]))
        out.append(_html_table(table_rows(q)))
        out.append("<div class='script'>" + "<br>".join("<b>%s:</b> %s" % (SPK_KO.get(ln["spk"], ln["spk"]), html.escape(ln["text"])) for ln in item["lines"]) + "</div></div>")
    return "".join(out) + "</body></html>"


# ----------------------------------------------------------------------
# 묶어서 zip 만들기
# ----------------------------------------------------------------------
def build(zip_path, title, items, questions, script_text, job_out=None, result=None, include_srt=True):
    """items: 파싱된 문항 목록, questions: id -> 교사가 승인한 문항. 반환: 안내 메시지 목록"""
    title = (title or "영어 듣기평가").strip()
    notes, counts = [], {"audio": 0, "srt": 0}
    items_by_id = {it["id"]: it for it in items}
    entries = []
    for i, it in enumerate(items):
        q = questions.get(it["id"])
        if q and q.get("approved"):
            entries.append((qnum(it["id"], i), it, q))
    missing = [it["id"] for it in items if not (questions.get(it["id"]) or {}).get("approved")]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        def put(name, data):
            zi = zipfile.ZipInfo(name, time.localtime()[:6])
            zi.flag_bits |= 0x800
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data if isinstance(data, bytes) else data.encode("utf-8-sig"))

        # 1) 음원
        if job_out and os.path.isdir(job_out):
            for n in sorted(os.listdir(job_out)):
                if n.lower().endswith((".mp3", ".wav")) and not n.startswith("_"):
                    with open(os.path.join(job_out, n), "rb") as f:
                        put("01_음원/" + n, f.read())
                    counts["audio"] += 1
        if not counts["audio"]:
            notes.append("음원이 들어 있지 않습니다. (아직 '음원 만들기'를 하지 않았거나 결과가 없습니다)")
        # 2) 문제지
        if entries:
            put("02_문제지/학생용_문제지.docx", write_docx(None, student_docx(title, entries)))
            put("02_문제지/교사용_정답과_해설.docx", write_docx(None, teacher_docx(title, entries, items_by_id)))
            put("02_문제지/학생용_문제지(인쇄용).html", student_html(title, entries))
            put("02_문제지/교사용_정답과_해설(인쇄용).html", teacher_html(title, entries))

            # 모바일 웹 응시 페이지 생성 데이터 구축
            mobile_qs_local = []
            mobile_qs_standalone = []
            for n, it, q in entries:
                matched_fname = ""
                if job_out and os.path.isdir(job_out):
                    for fname in sorted(os.listdir(job_out)):
                        if fname.lower().endswith((".mp3", ".wav")) and not fname.startswith("_") and "전체합본" not in fname:
                            if fname.startswith(f"{it['id']}_") or fname.startswith(f"{int(it['id']):02d}_") if it['id'].isdigit() else False:
                                matched_fname = fname
                                break
                            if it['id'] in fname:
                                matched_fname = fname
                                break
                
                base_q = {
                    "id": it["id"],
                    "num": n,
                    "title": it.get("title", f"{n}번 문항"),
                    "stem": q.get("stem", ""),
                    "choices": q.get("choices", []),
                    "answer": q.get("answer", 0),
                    "explanation": q.get("explanation", ""),
                    "evidence": q.get("evidence", []),
                    "figure_note": q.get("figure_note", ""),
                    "table": q.get("table", ""),
                    "script": it.get("lines", [])
                }

                q_local = dict(base_q, audio_file=f"../01_음원/{matched_fname}" if matched_fname else "")
                q_standalone = dict(base_q, audio_file=f"audio/{matched_fname}" if matched_fname else "")
                mobile_qs_local.append(q_local)
                mobile_qs_standalone.append(q_standalone)

                # 단독 배포 폴더용 오디오 복사
                if matched_fname and job_out:
                    src_af = os.path.join(job_out, matched_fname)
                    if os.path.isfile(src_af):
                        with open(src_af, "rb") as af:
                            put(f"04_모바일_웹시험지/audio/{matched_fname}", af.read())

            # 모바일 HTML 주입
            html_local = MEG.build_mobile_exam_html(title, mobile_qs_local)
            html_standalone = MEG.build_mobile_exam_html(title, mobile_qs_standalone)
            put("02_문제지/학생용_모바일_웹시험지.html", html_local)
            put("04_모바일_웹시험지/index.html", html_standalone)

            if missing:
                notes.append("승인하지 않은 문항은 문제지에서 뺐습니다: " + ", ".join(missing))
            bad = [e[0] for e in entries if e[2]["answer"] not in (1, 2, 3, 4, 5)]
            if bad:
                notes.append("정답이 정해지지 않은 문항이 있습니다: " + ", ".join(bad))
        # 문제지가 없으면 폴더 번호를 당긴다 (01_음원, 02_대본과_검수, 03_자막)
        D_SCRIPT = "03_대본과_검수" if entries else "02_대본과_검수"
        D_SRT = "04_자막" if entries else "03_자막"
        # 3) 대본과 검수
        put(D_SCRIPT + "/대본.txt", script_text)
        if job_out and os.path.isdir(job_out):
            for n in ("검수보고서.csv", "요약.txt", "00_전체합본_목차.txt", "오류기록.txt"):
                p = os.path.join(job_out, n)
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        put(D_SCRIPT + "/" + n, f.read())
        # 4) 자막
        if include_srt and result:
            offs = result.get("combined_offsets") or {}
            comb, no = [], 1
            for s in result.get("items", []):
                segs = s.get("segments") or []
                if not segs or not s.get("ok"):
                    continue
                if s.get("file") and not s["file"].startswith("("):
                    srt, _ = L.make_srt(segs)
                    put(D_SRT + "/" + os.path.splitext(s["file"])[0] + ".srt", srt)
                    counts["srt"] += 1
                if s["id"] in offs:
                    part, no = L.make_srt(segs, offs[s["id"]], no)
                    comb.append(part)
            if comb and result.get("combined_file"):
                put(D_SRT + "/" + os.path.splitext(result["combined_file"])[0] + ".srt", "\n".join(comb))
                counts["srt"] += 1
        elif include_srt:
            notes.append("자막은 음원을 만든 뒤에 함께 내보낼 수 있습니다.")
        guide = [title, "만든 날짜: " + time.strftime("%Y-%m-%d %H:%M"), "",
                 "01_음원          문항별 음원과 전체 합본 (%d개)" % counts["audio"]]
        if entries:
            guide.append("02_문제지        학생용 문제지, 교사용 정답과 해설 (워드 .docx / 인쇄용 .html / 모바일 웹시험지 .html) - 문항 %d개" % len(entries))
            guide.append("04_모바일_웹시험지  스마트폰 응시용 단독 웹 시험지 (index.html + audio/ 폴더, GitHub Pages 배포 가능)")
        guide += ["%s   대본, 검수 보고서, 요약, 합본 목차" % D_SCRIPT,
                  "%s          음원에 맞춘 자막 파일(.srt, %d개) - 동영상 편집 프로그램이나 플레이어에서 불러올 수 있습니다." % (D_SRT, counts["srt"])]
        if notes or entries:
            guide += ["", "알아두세요"] + ["- " + n for n in notes]
        if entries:
            guide += ["- 문항은 교사가 확인·승인한 것입니다. 시험에 쓰기 전에 한 번 더 검토해 주세요.",
                      "- 그림 문항은 문제지의 [그림 자리]에 그림을 직접 넣어 주세요."]
        put("00_안내.txt", "\n".join(guide))
    return {"notes": notes, "questions": len(entries), "audio": counts["audio"], "srt": counts["srt"]}
