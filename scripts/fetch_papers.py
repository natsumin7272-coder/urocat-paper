#!/usr/bin/env python3
"""UroCat Paper: fetch recent PubMed papers, rank relevance, optionally summarize with OpenAI, and notify Slack."""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
PAPERS_PATH = DATA_DIR / "papers.json"
STATE_PATH = DATA_DIR / "state.json"

APP_NAME = "UroCat_Paper"
NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "3"))
RETMAX = int(os.getenv("RETMAX", "80"))
SLACK_MIN_SCORE = int(os.getenv("SLACK_MIN_SCORE", "4"))
SLACK_MAX_PAPERS = int(os.getenv("SLACK_MAX_PAPERS", "6"))
OPENAI_MIN_SCORE = int(os.getenv("OPENAI_MIN_SCORE", "4"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6")

# Strict PubMed entry gate.
# Clinical searches require indwelling/Foley urinary catheter wording in Title/Abstract.
# MeSH-only matches are deliberately NOT used at the retrieval stage because they created
# many papers where catheterization was only a background variable.
INDWELLING_ANCHOR = (
    '"indwelling urinary catheter"[tiab] OR "indwelling urinary catheters"[tiab] OR '
    '"indwelling urethral catheter"[tiab] OR "indwelling urethral catheters"[tiab] OR '
    '"Foley catheter"[tiab] OR "Foley catheters"[tiab] OR '
    '("urinary catheter"[tiab] AND (indwelling[tiab] OR long-term[tiab] OR chronic[tiab])) OR '
    '("urethral catheter"[tiab] AND (indwelling[tiab] OR long-term[tiab] OR chronic[tiab]))'
)

TECH_ANCHOR = (
    '"urinary catheter"[tiab] OR "urinary catheters"[tiab] OR '
    '"urethral catheter"[tiab] OR "urethral catheters"[tiab] OR '
    '"Foley catheter"[tiab] OR "Foley catheters"[tiab]'
)

SEARCHES = [
    {
        "category": "閉塞・結晶・ストルバイト",
        "query": f'(({INDWELLING_ANCHOR}) AND (block*[tiab] OR obstruct*[tiab] OR occlus*[tiab] OR encrust*[tiab] OR "crystalline biofilm"[tiab] OR struvite[tiab] OR urease[tiab] OR "Proteus mirabilis"[tiab] OR crystall*[tiab]))'
    },
    {
        "category": "Biofilm・微生物叢",
        "query": f'(({INDWELLING_ANCHOR}) AND (biofilm*[tiab] OR microbiom*[tiab] OR microbiota[tiab] OR urobiom*[tiab] OR "16S rRNA"[tiab] OR metagenom*[tiab] OR urease[tiab] OR "Proteus mirabilis"[tiab] OR colonization[tiab] OR colonisation[tiab]))'
    },
    {
        "category": "長期管理・閉塞予防",
        "query": f'(({INDWELLING_ANCHOR}) AND ("long-term"[tiab] OR chronic[tiab]) AND (washout*[tiab] OR irrigation[tiab] OR "catheter change"[tiab] OR "catheter replacement"[tiab] OR maintenance[tiab] OR blockage[tiab] OR obstruction[tiab] OR encrustation[tiab] OR bypassing[tiab] OR leakage[tiab]))'
    },
    {
        "category": "新素材・コーティング",
        "query": f'(({TECH_ANCHOR}) AND (coating*[tiab] OR "surface modification"[tiab] OR hydrogel*[tiab] OR nanocoat*[tiab] OR nanoparticle*[tiab] OR nanostructur*[tiab] OR antibiofilm[tiab] OR "anti-biofilm"[tiab] OR antimicrobial[tiab] OR antifouling[tiab] OR "anti-fouling"[tiab] OR sensor*[tiab] OR "smart catheter"[tiab] OR lubricant*[tiab]))'
    },
]

DIRECT_TERMS = {
    "blockage": 3, "obstruction": 3, "obstructed": 3, "occlusion": 3,
    "encrustation": 3, "encrusted": 3, "crystalline biofilm": 3,
    "struvite": 3, "urease": 2, "proteus mirabilis": 2,
    "catheter blockage": 3, "crystal": 1,
}
RESEARCH_TERMS = {
    "biofilm": 2, "microbiome": 2, "microbiota": 1, "urobiome": 2,
    "16s rrna": 1, "metagenom": 1, "metabolom": 1, "picrust": 1,
    "urinary ph": 1, "alkaline urine": 1, "colonization": 1, "colonisation": 1,
}
TECH_TERMS = {
    "coating": 2, "hydrogel": 1, "nanoparticle": 1, "nanostruct": 1,
    "nanocoat": 1, "antibiofilm": 2, "anti-biofilm": 2, "antifouling": 1,
    "anti-fouling": 1, "surface modification": 1, "smart catheter": 1,
    "sensor": 1, "lubric": 1,
}
NEGATIVE_TERMS = {
    "central venous": -5, "central line": -5, "vascular catheter": -5,
    "dialysis catheter": -5, "peripheral intravenous": -5, "picc": -5,
    "cardiac catheter": -5, "pulmonary artery catheter": -5,
    "epidural catheter": -5, "intrathecal catheter": -5,
}

INTERMITTENT_TERMS = [
    "intermittent catheterization", "intermittent catheterisation",
    "intermittent urethral catheterization", "intermittent urethral catheterisation",
    "clean intermittent catheterization", "clean intermittent catheterisation",
    "self-catheterization", "self catheterization", "self-catheterisation",
]
PERIOPERATIVE_TERMS = [
    "holep", "holmium laser enucleation", "turp", "transurethral resection",
    "radical prostatectomy", "robot-assisted prostatectomy", "postoperative urinary retention",
    "perioperative", "postoperative catheterization", "catheter-free trial",
]
GENERIC_INFECTION_TERMS = [
    "antimicrobial stewardship", "infection prevention program", "infection prevention programme",
    "surveillance study", "quality improvement", "bundle compliance",
]


def has_indwelling_anchor(text: str) -> bool:
    t = text.lower()
    patterns = [
        r"\bindwelling (?:urinary|urethral) catheters?\b",
        r"\bfoley catheters?\b",
        r"\b(?:urinary|urethral) catheters?\b.{0,80}\b(?:indwelling|long[- ]term|chronic)\b",
        r"\b(?:indwelling|long[- ]term|chronic)\b.{0,80}\b(?:urinary|urethral) catheters?\b",
    ]
    return any(re.search(p, t, flags=re.S) for p in patterns)


def has_urinary_catheter_anchor(text: str) -> bool:
    t = text.lower()
    return bool(re.search(r"\b(?:urinary|urethral|foley) catheters?\b", t))


def hard_eligibility(p: Dict[str, Any]) -> tuple[bool, str]:
    """High-precision eligibility gate before a paper enters the app."""
    title = str(p.get("title", ""))
    abstract = str(p.get("abstract", ""))
    t = f"{title} {abstract}".lower()
    cats = set(p.get("search_categories", []))

    if not t.strip():
        return False, "タイトル・Abstractなし"

    tech = "新素材・コーティング" in cats or any(x in t for x in TECH_TERMS)
    direct = any(x in t for x in DIRECT_TERMS)
    research = any(x in t for x in ["biofilm", "microbiom", "microbiota", "urobiom", "urease", "proteus mirabilis", "16s rrna", "metagenom"])

    # Intermittent catheterization is outside the clinical scope. Keep only a genuine material/device study.
    if any(x in t for x in INTERMITTENT_TERMS) and not tech:
        return False, "間欠導尿"

    # Perioperative catheter status is not enough; retain only if the catheter itself is studied mechanistically/technically.
    if any(x in t for x in PERIOPERATIVE_TERMS) and not (direct or research or tech):
        return False, "周術期カテーテルのみ"

    # Generic stewardship/infection-prevention papers are excluded unless catheter biofilm/long-term mechanisms are explicit.
    if any(x in t for x in GENERIC_INFECTION_TERMS) and not (direct or research or tech):
        return False, "一般的感染対策"

    if tech:
        if not has_urinary_catheter_anchor(t):
            return False, "尿道/尿路カテーテル技術ではない"
        return True, "尿道/尿路カテーテル技術"

    # Clinical/mechanistic tracks require explicit indwelling/Foley context in title/abstract.
    if not has_indwelling_anchor(t):
        return False, "留置尿道カテーテルの明示なし"

    if "閉塞・結晶・ストルバイト" in cats and direct:
        return True, "留置カテーテル×閉塞/結晶"
    if "Biofilm・微生物叢" in cats and research:
        return True, "留置カテーテル×biofilm/微生物"
    if "長期管理・閉塞予防" in cats and any(x in t for x in ["washout", "irrigation", "catheter change", "catheter replacement", "maintenance", "blockage", "obstruction", "encrustation", "bypassing", "leakage"]):
        return True, "長期留置カテーテル管理"

    return False, "主題が対象外"


def http_get(url: str, params: Dict[str, Any], timeout: int = 30) -> bytes:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": f"{APP_NAME}/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def ncbi_common() -> Dict[str, str]:
    params = {"tool": APP_NAME}
    email = os.getenv("NCBI_EMAIL", "").strip()
    api_key = os.getenv("NCBI_API_KEY", "").strip()
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    return params


def esearch(query: str) -> List[str]:
    params: Dict[str, Any] = {
        "db": "pubmed", "term": query, "retmode": "json", "retmax": RETMAX,
        "sort": "pub date", "datetype": "edat", "reldate": LOOKBACK_DAYS,
        **ncbi_common(),
    }
    raw = http_get(f"{NCBI_BASE}/esearch.fcgi", params)
    return json.loads(raw.decode("utf-8"))["esearchresult"].get("idlist", [])


def efetch(pmids: Iterable[str]) -> List[Dict[str, Any]]:
    ids = list(dict.fromkeys(pmids))
    if not ids:
        return []
    params: Dict[str, Any] = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml", **ncbi_common()}
    raw = http_get(f"{NCBI_BASE}/efetch.fcgi", params)
    root = ET.fromstring(raw)
    return [parse_article(a) for a in root.findall(".//PubmedArticle")]


def text_of(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def parse_date(article: ET.Element) -> str:
    # Prefer PubMed Entrez date, then article publication date.
    for d in article.findall(".//PubMedPubDate"):
        if d.attrib.get("PubStatus") in {"entrez", "pubmed", "medline"}:
            y = text_of(d.find("Year")); m = text_of(d.find("Month")); day = text_of(d.find("Day"))
            if y:
                return f"{y}-{m.zfill(2) if m.isdigit() else '01'}-{day.zfill(2) if day.isdigit() else '01'}"
    pd = article.find(".//Article/Journal/JournalIssue/PubDate")
    y = text_of(pd.find("Year")) if pd is not None else ""
    m = text_of(pd.find("Month")) if pd is not None else ""
    day = text_of(pd.find("Day")) if pd is not None else ""
    return f"{y or '0000'}-{m.zfill(2) if m.isdigit() else '01'}-{day.zfill(2) if day.isdigit() else '01'}"


def parse_article(article: ET.Element) -> Dict[str, Any]:
    med = article.find("MedlineCitation")
    art = article.find(".//Article")
    pmid = text_of(med.find("PMID") if med is not None else None)
    title = text_of(art.find("ArticleTitle") if art is not None else None)
    abstract_parts = []
    if art is not None:
        for a in art.findall("Abstract/AbstractText"):
            label = a.attrib.get("Label") or a.attrib.get("NlmCategory") or ""
            t = text_of(a)
            abstract_parts.append(f"{label}: {t}" if label and t else t)
    abstract = "\n".join(x for x in abstract_parts if x)
    journal = text_of(art.find("Journal/Title") if art is not None else None)
    journal_abbr = text_of(med.find("MedlineJournalInfo/MedlineTA") if med is not None else None)
    authors = []
    if art is not None:
        for au in art.findall("AuthorList/Author"):
            collective = text_of(au.find("CollectiveName"))
            if collective:
                authors.append(collective); continue
            last = text_of(au.find("LastName")); fore = text_of(au.find("ForeName"))
            name = " ".join(x for x in [fore, last] if x)
            if name: authors.append(name)
    doi = ""
    for aid in article.findall(".//ArticleId"):
        if aid.attrib.get("IdType") == "doi":
            doi = text_of(aid); break
    publication_types = [text_of(x) for x in article.findall(".//PublicationType") if text_of(x)]
    mesh = [text_of(x) for x in article.findall(".//MeshHeading/DescriptorName") if text_of(x)]
    return {
        "pmid": pmid,
        "title": html.unescape(title),
        "abstract": html.unescape(abstract),
        "journal": journal,
        "journal_abbr": journal_abbr,
        "authors": authors,
        "doi": doi,
        "publication_date": parse_date(article),
        "publication_types": publication_types,
        "mesh": mesh,
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        "doi_url": f"https://doi.org/{doi}" if doi else "",
    }


def score_paper(p: Dict[str, Any]) -> Dict[str, Any]:
    text = f"{p.get('title','')} {p.get('abstract','')} {' '.join(p.get('mesh', []))}".lower()
    score = 0
    reasons: List[str] = []

    urinary_anchor = has_indwelling_anchor(f"{p.get('title','')} {p.get('abstract','')}")
    tech_anchor = has_urinary_catheter_anchor(f"{p.get('title','')} {p.get('abstract','')}")
    if urinary_anchor:
        score += 3; reasons.append("留置尿道/尿路カテーテル")
    elif tech_anchor and any(term in text for term in TECH_TERMS):
        score += 2; reasons.append("尿道/尿路カテーテル技術")
    else:
        score -= 3

    for term, pts in DIRECT_TERMS.items():
        if term in text:
            score += pts; reasons.append(term)
    for term, pts in RESEARCH_TERMS.items():
        if term in text:
            score += pts; reasons.append(term)
    for term, pts in TECH_TERMS.items():
        if term in text:
            score += pts; reasons.append(term)
    for term, pts in NEGATIVE_TERMS.items():
        if term in text:
            score += pts; reasons.append(f"除外傾向:{term}")

    human_signal = any(x in text for x in ["patient", "patients", "participant", "participants", "human", "clinical", "cohort", "randomized", "retrospective", "prospective"])
    if human_signal and urinary_anchor:
        score += 1; reasons.append("ヒト研究候補")

    direct_signal = any(term in text for term in DIRECT_TERMS)
    if any(t in text for t in GENERIC_INFECTION_TERMS) and not direct_signal and not any(t in text for t in ["biofilm", "microbiom", "proteus", "urease", "long-term"]):
        score -= 3; reasons.append("一般感染対策寄り")

    # Map raw score to 1-5. Direct catheter blockage/encrustation papers should reliably reach 5.
    if score >= 8: stars = 5
    elif score >= 5: stars = 4
    elif score >= 3: stars = 3
    elif score >= 1: stars = 2
    else: stars = 1

    # Topic label
    topic = "周辺知識"
    if any(t in text for t in ["blockage", "obstruction", "encrust", "struvite", "crystalline biofilm"]): topic = "閉塞・結晶"
    elif any(t in text for t in ["biofilm", "microbiom", "microbiota", "urobiome", "urease", "proteus mirabilis"]): topic = "Biofilm・微生物叢"
    elif any(t in text for t in ["coating", "hydrogel", "nanoparticle", "antibiofilm", "antifouling", "sensor"]): topic = "新素材・コーティング"
    elif any(t in text for t in ["long-term", "washout", "irrigation", "catheter change", "nursing"]): topic = "管理・予防"

    p["relevance_score"] = stars
    p["relevance_reasons"] = list(dict.fromkeys(reasons))[:8]
    p["topic"] = topic
    return p


def ai_enrich(p: Dict[str, Any]) -> Dict[str, Any]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key or p.get("relevance_score", 1) < OPENAI_MIN_SCORE:
        return p
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        prompt = f"""You are curating new literature for one Japanese nursing researcher studying long-term indwelling urethral urinary catheter blockage, encrustation, struvite, urease/Proteus, catheter biofilm/microbiome, and prevention/material technologies.
Use ONLY the PubMed title/abstract below. Do not add facts not present in it.
Return ONLY valid JSON with these keys:
JapaneseTitle, OneLineJapanese, Population, StudyDesign, MainFindingJapanese, WhyRelevantJapanese, AiRelevanceScore
AiRelevanceScore must be an integer 1-5. Give 4-5 only when directly useful for this research area; generic CAUTI incidence/surveillance without blockage/biofilm/long-term-catheter relevance should be 1-3.

Title: {p.get('title','')}
Abstract: {p.get('abstract','')}
Publication types: {', '.join(p.get('publication_types', []))}
Rule-based relevance score: {p.get('relevance_score')}/5
"""
        resp = client.responses.create(model=OPENAI_MODEL, input=prompt)
        txt = resp.output_text.strip()
        txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.I | re.S)
        obj = json.loads(txt)
        p["japanese_title"] = str(obj.get("JapaneseTitle", "")).strip()
        p["one_line_ja"] = str(obj.get("OneLineJapanese", "")).strip()
        p["population"] = str(obj.get("Population", "")).strip()
        p["study_design"] = str(obj.get("StudyDesign", "")).strip()
        p["main_finding_ja"] = str(obj.get("MainFindingJapanese", "")).strip()
        p["why_relevant_ja"] = str(obj.get("WhyRelevantJapanese", "")).strip()
        ai_score = int(obj.get("AiRelevanceScore", p["relevance_score"]))
        p["ai_relevance_score"] = max(1, min(5, ai_score))
        # Conservative final score: AI may lower noisy papers; rule+AI must both support an increase.
        p["final_score"] = min(p["relevance_score"], p["ai_relevance_score"]) if p["ai_relevance_score"] < 4 else max(p["relevance_score"], p["ai_relevance_score"])
    except Exception as exc:
        p["ai_error"] = f"{type(exc).__name__}: {exc}"
    return p


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def slack_escape(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_slack(new_papers: List[Dict[str, Any]]) -> None:
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        print("SLACK_WEBHOOK_URL not set; skipping Slack.")
        return
    selected = [p for p in new_papers if int(p.get("final_score", p.get("relevance_score", 1))) >= SLACK_MIN_SCORE]
    selected.sort(key=lambda x: (int(x.get("final_score", x.get("relevance_score", 1))), x.get("publication_date", "")), reverse=True)
    selected = selected[:SLACK_MAX_PAPERS]
    if not selected:
        print("No high-relevance papers to notify.")
        return

    blocks: List[Dict[str, Any]] = [{"type": "header", "text": {"type": "plain_text", "text": f"📚 UroCat Paper｜新着 {len(selected)}報", "emoji": True}}]
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "ヒトの尿道留置カテーテル・閉塞・結晶・biofilmを優先して選別"}]})
    for i, p in enumerate(selected):
        if i:
            blocks.append({"type": "divider"})
        score = int(p.get("final_score", p.get("relevance_score", 1)))
        stars = "★" * score + "☆" * (5 - score)
        title = slack_escape(p.get("title", ""))
        jtitle = slack_escape(p.get("japanese_title") or "日本語タイトル未生成")
        url = p.get("pubmed_url", "")
        meta = "｜".join(x for x in [p.get("journal", ""), p.get("publication_date", ""), f"PMID {p.get('pmid','')}" ] if x)
        summary = p.get("one_line_ja") or p.get("why_relevant_ja") or "Abstractをアプリで確認してください。"
        fields = []
        if p.get("population"): fields.append({"type": "mrkdwn", "text": f"*対象*\n{slack_escape(p['population'])}"})
        if p.get("study_design"): fields.append({"type": "mrkdwn", "text": f"*デザイン*\n{slack_escape(p['study_design'])}"})
        blocks.extend([
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{stars}  {slack_escape(p.get('topic',''))}*\n<{url}|*{title}*>\n{jtitle}"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": slack_escape(meta)}]},
        ])
        if fields:
            blocks.append({"type": "section", "fields": fields[:2]})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*要点*\n{slack_escape(summary)}"}})

    payload = json.dumps({"text": f"UroCat Paper: {len(selected)} new high-relevance papers", "blocks": blocks}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        print("Slack:", resp.status, resp.read().decode("utf-8", errors="replace"))


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    old_papers = load_json(PAPERS_PATH, [])
    if isinstance(old_papers, dict): old_papers = old_papers.get("papers", [])
    old_by_id = {str(p.get("pmid")): p for p in old_papers if p.get("pmid")}

    found: Dict[str, set] = {}
    for s in SEARCHES:
        print("Searching:", s["category"])
        try:
            ids = esearch(s["query"])
        except Exception as exc:
            print("ESearch failed:", exc, file=sys.stderr)
            continue
        for pmid in ids:
            found.setdefault(pmid, set()).add(s["category"])
        time.sleep(0.15)

    ids = list(found.keys())
    print("Unique PMIDs:", len(ids))
    records: List[Dict[str, Any]] = []
    for start in range(0, len(ids), 100):
        batch = efetch(ids[start:start+100])
        records.extend(batch)
        time.sleep(0.15)

    new_papers: List[Dict[str, Any]] = []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    excluded: List[Dict[str, Any]] = []
    for p in records:
        p["search_categories"] = sorted(found.get(p.get("pmid", ""), set()))
        eligible, eligibility_reason = hard_eligibility(p)
        p["eligible"] = eligible
        p["eligibility_reason"] = eligibility_reason
        if not eligible:
            excluded.append({"pmid": p.get("pmid", ""), "title": p.get("title", ""), "reason": eligibility_reason})
            continue
        p = score_paper(p)
        p["final_score"] = p["relevance_score"]
        p["fetched_at"] = now
        if p["pmid"] in old_by_id:
            old = old_by_id[p["pmid"]]
            # Preserve expensive AI fields and user-independent enrichment if already generated.
            for k in ["japanese_title", "one_line_ja", "population", "study_design", "main_finding_ja", "why_relevant_ja", "ai_relevance_score", "final_score"]:
                if old.get(k): p[k] = old[k]
        else:
            p = ai_enrich(p)
            new_papers.append(p)
        old_by_id[p["pmid"]] = p

    # Keep a useful rolling library rather than growing forever.
    # Purge demonstration data and papers that no longer pass the strict eligibility gate.
    merged: List[Dict[str, Any]] = []
    for old in old_by_id.values():
        if str(old.get("pmid", "")).upper().startswith("DEMO"):
            continue
        ok, reason = hard_eligibility(old)
        if not ok:
            continue
        old["eligible"] = True
        old["eligibility_reason"] = reason
        merged.append(old)
    merged.sort(key=lambda x: (x.get("publication_date", ""), int(x.get("final_score", x.get("relevance_score", 1)))), reverse=True)
    merged = merged[:1200]
    save_json(PAPERS_PATH, merged)
    save_json(STATE_PATH, {
        "updated_at": now,
        "lookback_days": LOOKBACK_DAYS,
        "papers_total": len(merged),
        "new_today": len(new_papers),
        "high_relevance_new": sum(1 for p in new_papers if int(p.get("final_score", p.get("relevance_score", 1))) >= SLACK_MIN_SCORE),
        "excluded_this_run": len(excluded),
    })
    if excluded:
        print("Excluded by strict eligibility gate:")
        for x in excluded[:30]:
            print(f"  - PMID {x['pmid']}: {x['reason']} | {x['title'][:120]}")
    send_slack(new_papers)
    print(f"Saved {len(merged)} eligible papers; {len(new_papers)} newly discovered; {len(excluded)} excluded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
