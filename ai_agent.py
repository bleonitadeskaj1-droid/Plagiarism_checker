import anthropic, json, re, os
from dotenv import load_dotenv

load_dotenv()


class PlagiarismAgent:
    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None
        self.model = "claude-sonnet-4-20250514"

    def analyze_with_confidential(self, thesis_text, thesis_title, public_theses, conf_docs, search_web=True):
        text_sample = thesis_text[:7000]
        if not self.client:
            return self._mock_analysis(thesis_text, public_theses, search_web)

        public_ctx = ""
        if public_theses:
            public_ctx = "\n\nTEMAT PUBLIKE NE DATABAZE:\n"
            for t in public_theses[:8]:
                public_ctx += f"\n[PUB-{t['id']}] {t['title']}\n{t.get('content','')[:400]}\n---"
        conf_ctx = ""
        if conf_docs:
            conf_ctx = "\n\nDOKUMENTET KONFIDENCIALE TE FAKULTETIT:\n"
            for d in conf_docs[:15]:
                conf_ctx += f"\n[CONF-{d['id']}] {d['title']} ({d.get('year','')}) - {d.get('department','')}\n{d.get('content','')[:500]}\n---"

        system_prompt = """Jeni ekspert i analizes se plagjiatures akademike.
Keni akses ne: (1) Tema publike [PUB-ID] dhe (2) Dokumente konfidenciale [CONF-ID].

RREGULL KRITIK per CONF: Tregoni vetem source_type="confidential", conf_source_id=<id>, source_title=<titulli>.
KURRE mos citoni tekst nga dokumentet CONF. Tregoni vetem tekstin NGA TEMA (original_text).

Ktheni VETEM JSON:
{
  "overall_score": <0-100>,
  "internal_score": <0-100>,
  "confidential_score": <0-100>,
  "web_score": <0-100>,
  "risk_level": "low|medium|high|critical",
  "summary": "<permbledhje>",
  "flagged_sections": [
    {
      "source_type": "confidential|internal|web",
      "conf_source_id": <id ose null>,
      "source_title": "<titulli i burimit>",
      "source_url": "<url ose null>",
      "original_text": "<teksti NGA TEMA - max 200 karaktere>",
      "similarity": <0-100>,
      "paragraph_index": <0>
    }
  ],
  "recommendations": "<rekomandimet>"
}"""

        user_msg = f"Analizoni:\nTITULLI: {thesis_title}\nTEKSTI:\n{text_sample}{public_ctx}{conf_ctx}"
        tools = [{"type": "web_search_20250305", "name": "web_search"}] if search_web else []

        try:
            kwargs = dict(model=self.model, max_tokens=4000, system=system_prompt,
                          messages=[{"role": "user", "content": user_msg}])
            if tools:
                kwargs["tools"] = tools
            response = self.client.messages.create(**kwargs)
            text = "".join(b.text for b in response.content if b.type == "text")
            return self._parse_json(text)
        except Exception as e:
            return self._error_result(str(e))

    def analyze_plagiarism(self, thesis_text, thesis_title, existing_theses, search_web=True):
        return self.analyze_with_confidential(thesis_text=thesis_text, thesis_title=thesis_title,
            public_theses=existing_theses, conf_docs=[], search_web=search_web)

    def generate_report_summary(self, result, thesis_title):
        prompt = f"""Shkruaj raport akademik ne shqip:
Titulli: {thesis_title}
Score: {result.get('overall_score',0)}% | Konfidencial: {result.get('confidential_score',0)}% | Web: {result.get('web_score',0)}%
Rreziku: {result.get('risk_level','unknown')} | Seksione: {len(result.get('flagged_sections',[]))}
SHENIM: Mos cito permbajtje nga dokumentet konfidenciale."""
        try:
            if not self.client:
                return (
                    f"Raport i shkurtër për {thesis_title}: "
                    f"{result.get('overall_score', 0)}% ngjashmëri totale, "
                    f"{result.get('internal_score', 0)}% nga baza interne dhe "
                    f"{result.get('web_score', 0)}% nga web-i."
                )
            r = self.client.messages.create(model=self.model, max_tokens=1500,
                messages=[{"role":"user","content":prompt}])
            return r.content[0].text
        except Exception as e:
            return f"Gabim: {e}"

    def _parse_json(self, text):
        try:
            text = re.sub(r'```json\s*','', text)
            text = re.sub(r'```\s*','', text).strip()
            m = re.search(r'\{.*\}', text, re.DOTALL)
            return json.loads(m.group() if m else text)
        except:
            return self._error_result("JSON parse failed")

    def _error_result(self, msg):
        return {"overall_score":0,"internal_score":0,"confidential_score":0,
                "web_score":0,"risk_level":"unknown","summary":f"Gabim: {msg}",
                "flagged_sections":[],"recommendations":"Provoni perseri."}

    def _mock_analysis(self, thesis_text, public_theses, search_web=True):
        def tokenize(text):
            return set(re.findall(r"\w{3,}", (text or "").lower()))

        thesis_words = tokenize(thesis_text)
        best_score = 0.0
        flagged = []

        for thesis in (public_theses or [])[:10]:
            other_words = tokenize(thesis.get("content", ""))
            if not thesis_words or not other_words:
                continue
            common = thesis_words & other_words
            denom = min(len(thesis_words), len(other_words)) or 1
            score = (len(common) / denom) * 100.0
            if score > 0:
                flagged.append({
                    "text": "...",
                    "source": thesis.get("title") or f"Thesis {thesis.get('id')}",
                    "source_type": "internal",
                    "similarity": round(score, 2),
                    "reason": "Mock match",
                })
            best_score = max(best_score, score)

        web_score = 5.0 if search_web else 0.0
        overall = max(best_score, web_score)
        return {
            "overall_score": round(overall, 2),
            "internal_score": round(best_score, 2),
            "confidential_score": 0,
            "web_score": round(web_score, 2),
            "risk_level": "high" if overall >= 50 else "medium" if overall >= 25 else "low",
            "summary": "Kjo është një analizë mock për testim.",
            "flagged_sections": flagged,
            "recommendations": "Kontrollo burimet e listuara.",
        }