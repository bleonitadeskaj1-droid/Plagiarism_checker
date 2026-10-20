"""Shared DB-grounded replies for admin and professor AI agents."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy.orm import Session

from document_processor import DocumentProcessor
from models import PlagiarismResult, Review, SubmissionWorkflowStatus, Thesis, ThesisStatus
from plagiarism_agent import PlagiarismAgent
from services.thesis_text import get_thesis_text

AUTO_PASS_THRESHOLD = 20.0
_compare_agent = PlagiarismAgent()
_compare_processor = DocumentProcessor()


def normalize_message(msg: str) -> str:
    text = msg.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text


def enum_value(value: Any) -> str:
    if value is None:
        return ""
    return value.value if hasattr(value, "value") else str(value)


def student_name(thesis: Thesis) -> str:
    if thesis.student and thesis.student.full_name:
        return thesis.student.full_name
    if thesis.submitted_by_user:
        return thesis.submitted_by_user.full_name or thesis.submitted_by_user.username or "Student"
    return "Student i panjohur"


def latest_result(thesis: Thesis) -> PlagiarismResult | None:
    completed = [r for r in thesis.results if enum_value(r.status) == "completed"]
    if not completed:
        return None
    return sorted(completed, key=lambda r: (r.analyzed_at or r.id), reverse=True)[0]


def plagiarism_pct(thesis: Thesis, review: Review | None, result: PlagiarismResult | None) -> float | None:
    if review and review.plagiarism_percentage is not None:
        return float(review.plagiarism_percentage)
    if result and result.overall_score is not None:
        return float(result.overall_score)
    return None


def thesis_item(thesis: Thesis) -> dict[str, Any]:
    review = thesis.reviews[0] if thesis.reviews else None
    result = latest_result(thesis)
    pct = plagiarism_pct(thesis, review, result)
    return {
        "id": thesis.id,
        "title": thesis.title,
        "student": student_name(thesis),
        "workflow": enum_value(thesis.workflow_status),
        "analysis": enum_value(thesis.status),
        "pct": pct,
        "has_result": result is not None,
        "review_status": enum_value(review.status) if review else None,
        "department": thesis.department or "—",
    }


def build_thesis_stats(db: Session, professor_id: int | None = None) -> dict[str, Any]:
    query = db.query(Thesis).filter(Thesis.is_archive.is_(False))
    if professor_id is not None:
        query = query.filter(Thesis.assigned_professor_id == professor_id)
    theses = query.order_by(Thesis.created_at.desc()).all()

    student_keys: set[tuple[str, int]] = set()
    pending: list[dict] = []
    in_process: list[dict] = []
    approved: list[dict] = []
    rejected: list[dict] = []
    needs_revision: list[dict] = []
    with_result: list[dict] = []
    passed: list[dict] = []
    flagged: list[dict] = []
    checked: list[dict] = []
    analyzing: list[dict] = []

    for thesis in theses:
        item = thesis_item(thesis)
        wf = item["workflow"]
        analysis = item["analysis"]
        pct = item["pct"]

        if thesis.student_id:
            student_keys.add(("student", thesis.student_id))
        elif thesis.submitted_by_user_id:
            student_keys.add(("user", thesis.submitted_by_user_id))

        if wf == SubmissionWorkflowStatus.in_process.value or analysis in (
            ThesisStatus.pending.value,
            ThesisStatus.analyzing.value,
        ):
            in_process.append(item)
            pending.append(item)

        if wf == SubmissionWorkflowStatus.approved.value:
            approved.append(item)

        if wf == SubmissionWorkflowStatus.rejected.value:
            rejected.append(item)

        if wf == SubmissionWorkflowStatus.needs_revision.value:
            needs_revision.append(item)

        if analysis == ThesisStatus.analyzing.value:
            analyzing.append(item)

        if item["has_result"] or thesis.reviews:
            with_result.append(item)

        if item["has_result"]:
            checked.append(item)

        if wf == SubmissionWorkflowStatus.approved.value:
            passed.append(item)

        if analysis == ThesisStatus.flagged.value or (pct is not None and pct > 30):
            flagged.append(item)

    return {
        "total_theses": len(theses),
        "total_students": len(student_keys),
        "pending_count": len(pending),
        "in_process_count": len(in_process),
        "approved_count": len(approved),
        "rejected_count": len(rejected),
        "needs_revision_count": len(needs_revision),
        "with_result_count": len(with_result),
        "passed_count": len(passed),
        "flagged_count": len(flagged),
        "checked_count": len(checked),
        "analyzing_count": len(analyzing),
        "lists": {
            "pending": pending,
            "in_process": in_process,
            "approved": approved,
            "rejected": rejected,
            "needs_revision": needs_revision,
            "with_result": with_result,
            "passed": passed,
            "flagged": flagged,
            "checked": checked,
            "all": [thesis_item(t) for t in theses],
        },
    }


def has_keyword(msg: str, *keywords: str) -> bool:
    return any(k in msg for k in keywords)


def _extract_thesis_ids(msg: str) -> list[int]:
    return [int(x) for x in re.findall(r"\b(\d{1,6})\b", msg)][:2]


def _find_thesis_by_hint(db: Session, hint: str, professor_id: int | None = None) -> Thesis | None:
    hint = hint.strip().lower()
    if not hint:
        return None
    query = db.query(Thesis)
    if professor_id is not None:
        query = query.filter(Thesis.assigned_professor_id == professor_id)
    for thesis in query.order_by(Thesis.is_archive.desc(), Thesis.id.desc()).limit(200).all():
        title = (thesis.title or "").lower()
        if hint in title or title in hint:
            return thesis
    return None


def try_thesis_compare_reply(db: Session, raw_message: str, professor_id: int | None = None) -> str | None:
    """Compare two theses locally when user asks (krahaso / ngjashmëri / similarity)."""
    msg = normalize_message(raw_message)
    if not has_keyword(msg, "krahaso", "krahasim", "ngjashmeri", "ngjashm", "similarity", "compare", "perqind"):
        return None

    ids = _extract_thesis_ids(msg)
    thesis_a: Thesis | None = None
    thesis_b: Thesis | None = None

    if len(ids) >= 2:
        thesis_a = db.query(Thesis).filter(Thesis.id == ids[0]).first()
        thesis_b = db.query(Thesis).filter(Thesis.id == ids[1]).first()
    elif len(ids) == 1 and has_keyword(msg, "arkiv"):
        thesis_a = db.query(Thesis).filter(Thesis.id == ids[0]).first()
        thesis_b = (
            db.query(Thesis)
            .filter(Thesis.is_archive.is_(True))
            .order_by(Thesis.id.desc())
            .first()
        )
    elif has_keyword(msg, "arkiv"):
        thesis_a = (
            db.query(Thesis)
            .filter(Thesis.is_archive.is_(False))
            .order_by(Thesis.id.desc())
            .first()
        )
        thesis_b = (
            db.query(Thesis)
            .filter(Thesis.is_archive.is_(True))
            .order_by(Thesis.id.desc())
            .first()
        )

    if not thesis_a or not thesis_b:
        return (
            "Për të krahasuar dy tema, shkruaj p.sh.:\n"
            "• «Krahaso temën 12 me 5»\n"
            "• «Krahaso temën 12 me arkivin»\n"
            "• «Sa % ngjashmëri ka tema 8 me tema 3?»"
        )

    if professor_id is not None and thesis_a.assigned_professor_id not in (None, professor_id):
        return "Mund të krahasosh vetëm temat e caktuara për ty."

    text_a = get_thesis_text(thesis_a, _compare_processor)
    text_b = get_thesis_text(thesis_b, _compare_processor)
    if not text_a.strip() or not text_b.strip():
        return "Njëra ose të dyja temat nuk kanë tekst të lexueshëm për krahasim."

    result = _compare_agent.compare_two_texts(text_a, text_b, thesis_a.title, thesis_b.title)
    score = float(result.get("overall_score", 0))
    archive_note = ""
    if getattr(thesis_b, "is_archive", False):
        archive_note = " (tema B është nga arkivi Temat/)"
    elif getattr(thesis_a, "is_archive", False):
        archive_note = " (tema A është nga arkivi Temat/)"

    return (
        f"Rezultati i krahasimit{archive_note}:\n\n"
        f"• Tema A (#{thesis_a.id}): «{thesis_a.title}»\n"
        f"• Tema B (#{thesis_b.id}): «{thesis_b.title}»\n\n"
        f"• Ngjashmëria totale: {score:.1f}%\n"
        f"• TF-IDF: {result.get('tfidf_score', 0):.1f}%\n"
        f"• Jaccard: {result.get('jaccard_score', 0):.1f}%\n"
        f"• SequenceMatcher: {result.get('sequence_score', 0):.1f}%\n"
        f"• Niveli i rrezikut: {result.get('risk_level', '—')}\n\n"
        f"{result.get('recommendations', '')}"
    )


def format_thesis_list(items: list[dict], limit: int = 12) -> str:
    if not items:
        return "Asnje teme ne kete kategori."
    lines = []
    for i, item in enumerate(items[:limit], 1):
        pct = item.get("pct")
        pct_txt = f" — {pct:.1f}% plagjiature" if pct is not None else ""
        lines.append(f"{i}. «{item['title']}» — {item['student']}{pct_txt}")
    if len(items) > limit:
        lines.append(f"... dhe {len(items) - limit} te tjera.")
    return "\n".join(lines)


def summary_reply(
    stats: dict,
    audience: str,
    name: str,
    department: str | None,
    extras: dict[str, Any] | None = None,
) -> str:
    extras = extras or {}
    dept = department or "Pa departament"
    student_label = (
        "Studentë me tema të regjistruara në sistem"
        if audience == "admin"
        else "Studentë me tema të caktuara"
    )
    header = (
        f"Përmbledhje e sistemit AAB:"
        if audience == "admin"
        else f"Përmbledhje për profesorin {name} ({dept}):"
    )
    lines = [
        header,
        "",
        f"• {student_label}: {stats['total_students']}",
        f"• Tema gjithsej: {stats['total_theses']}",
        f"• Në pritje / në proces: {stats['pending_count']}",
        f"• Duke u analizuar: {stats['analyzing_count']}",
        f"• Të kontrolluara (analizë e përfunduar): {stats['checked_count']}",
        f"• Me rezultat të kthyer: {stats['with_result_count']}",
        f"• Kaluar plagjiaturën (<={AUTO_PASS_THRESHOLD:.0f}% / aprovuar): {stats['passed_count']}",
        f"• Tema të refuzuara: {stats['rejected_count']}",
        f"• Kërkojnë rishikim: {stats['needs_revision_count']}",
        f"• Flamëruar / rrezik i lartë (>30%): {stats['flagged_count']}",
    ]
    if audience == "admin":
        if "total_users" in extras:
            lines.append(f"• Përdorues në sistem: {extras['total_users']} (studentë: {extras.get('students', 0)}, profesorë: {extras.get('professors', 0)})")
        if "conf_docs" in extras:
            lines.append(f"• Dokumente konfidenciale: {extras['conf_docs']}")
        if extras.get("departments"):
            lines.append("\nSipas departamentit:")
            lines.extend(extras["departments"])
    return "\n".join(lines)


def build_agent_reply(
    raw_message: str,
    stats: dict,
    *,
    audience: str,
    name: str,
    department: str | None = None,
    extras: dict[str, Any] | None = None,
    db: Session | None = None,
    professor_id: int | None = None,
) -> str:
    if db is not None:
        compare_reply = try_thesis_compare_reply(db, raw_message, professor_id=professor_id)
        if compare_reply:
            return compare_reply

    msg = normalize_message(raw_message)
    lists = stats["lists"]
    wants_list = has_keyword(msg, "cilat", "cila", "lista", "listo", "emrat", "titull", "temat", "cili", "kush")
    scope_you = audience == "professor"

    if has_keyword(msg, "student", "studente", "studenta", "nxenes", "nxenesi", "insertuar", "regjistruar", "ngarkuar"):
        if has_keyword(msg, "pritje", "proces", "pending", "presin"):
            reply = (
                f"{'Ke' if scope_you else 'Kemi'} {stats['pending_count']} tema në pritje "
                f"(nga {stats['total_students']} studentë me tema {'të caktuara për ju' if scope_you else 'në sistem'})."
            )
            if wants_list or has_keyword(msg, "cilat", "cila", "lista"):
                reply += "\n\nTemat në pritje:\n" + format_thesis_list(lists["pending"])
            return reply
        if has_keyword(msg, "kryer", "perfunduar", "aprovuar", "kaluar", "kompletuar"):
            reply = f"Tema të kryera / aprovuara: {stats['approved_count']}."
            if wants_list:
                reply += "\n\n" + format_thesis_list(lists["approved"])
            return reply
        reply = (
            f"{'Ke' if scope_you else 'Ne sistem kemi'} {stats['total_students']} studentë "
            f"me tema {'të caktuara për ju' if scope_you else 'të regjistruara për kontroll plagjiature'}, "
            f"me {stats['total_theses']} tema gjithsej."
        )
        if wants_list:
            reply += "\n\nTë gjitha temat:\n" + format_thesis_list(lists["all"])
        return reply

    if has_keyword(msg, "pritje", "ne pritje", "pending", "ne proces", "in_process", "presin", "pres"):
        reply = f"Tema në pritje / në proces: {stats['pending_count']}."
        if wants_list or has_keyword(msg, "sa", "numri", "numrin"):
            reply += "\n\n" + format_thesis_list(lists["pending"])
        return reply

    if has_keyword(msg, "refuzuar", "rejected", "refuzim", "refuzo"):
        reply = f"Tema të refuzuara: {stats['rejected_count']}."
        if wants_list:
            reply += "\n\n" + format_thesis_list(lists["rejected"])
        return reply

    if has_keyword(msg, "rishikim", "needs_revision", "revision", "revizion"):
        reply = f"Tema që kërkojnë rishikim: {stats['needs_revision_count']}."
        if wants_list:
            reply += "\n\n" + format_thesis_list(lists["needs_revision"])
        return reply

    if has_keyword(msg, "kaluar", "sukses", "aprovuar", "approved", "kaloi", "plagjiatur", "plagjiature", "kaluan"):
        reply = (
            f"Tema që kanë kaluar plagjiaturën (<={AUTO_PASS_THRESHOLD:.0f}% ose aprovuar): "
            f"{stats['passed_count']}."
        )
        if wants_list:
            reply += "\n\n" + format_thesis_list(lists["passed"])
        return reply

    if has_keyword(msg, "rezultat", "kthyer", "kthimi", "kthyer", "analizuar", "analiza", "raport"):
        reply = f"Tema me rezultat të kthyer: {stats['with_result_count']}."
        if wants_list:
            reply += "\n\n" + format_thesis_list(lists["with_result"])
        return reply

    if has_keyword(msg, "kontrolluar", "kontrollu", "kontroll", "skanuar", "skenuar", "kontrolluara"):
        reply = f"Tema të kontrolluara (analizë e përfunduar): {stats['checked_count']}."
        if wants_list:
            reply += "\n\n" + format_thesis_list(lists["checked"])
        return reply

    if has_keyword(msg, "kryer", "perfunduar", "kompletuar", "mbyllur"):
        reply = f"Tema të aprovuara / të kryera: {stats['approved_count']}."
        if wants_list:
            reply += "\n\n" + format_thesis_list(lists["approved"])
        return reply

    if has_keyword(msg, "flameruar", "flagged", "rrezik", "larte", "30", "problematik", "probleme"):
        reply = f"Tema flamëruara / me rrezik të lartë (>30%): {stats['flagged_count']}."
        if wants_list:
            reply += "\n\n" + format_thesis_list(lists["flagged"])
        return reply

    if has_keyword(msg, "duke u analizuar", "analizohet", "analyzing", "po analizo"):
        reply = f"Tema që po analizohen: {stats['analyzing_count']}."
        if wants_list:
            reply += "\n\n" + format_thesis_list(lists["analyzing"])
        return reply

    if has_keyword(msg, "departament", "departamente"):
        dept_lines = extras.get("departments") if extras else None
        if dept_lines:
            return "Temat sipas departamentit:\n\n" + "\n".join(dept_lines)
        return f"Ka {stats['total_theses']} tema në total. Nuk ka ndarje të detajuar sipas departamentit."

    if has_keyword(msg, "databaz", "gjendj", "sistem", "server", "database"):
        ok = (extras or {}).get("db_ok", True)
        return (
            f"Gjendja e sistemit:\n\n"
            f"• Databaza: {'aktive' if ok else 'jo e disponueshme'}\n"
            f"• Tema: {stats['total_theses']}\n"
            f"• Studentë me tema: {stats['total_students']}\n"
            f"• Analiza të përfunduara: {stats['checked_count']}\n"
            f"• Rezultate të kthyer: {stats['with_result_count']}"
        )

    if has_keyword(
        msg,
        "statistik",
        "permbledhje",
        "raport",
        "gjithsej",
        "numri",
        "numrin",
        "sa ",
        "sa?",
        "info",
        "informacion",
    ) or msg.startswith("sa"):
        return summary_reply(stats, audience, name, department, extras)

    if wants_list and stats["total_theses"]:
        title = "Temat e caktuara për ju:" if scope_you else "Të gjitha temat në sistem:"
        return title + "\n\n" + format_thesis_list(lists["all"])

    if has_keyword(msg, "tema", "tezat", "teza", "diplom"):
        reply = f"Ka {stats['total_theses']} tema në total."
        if wants_list:
            reply += "\n\n" + format_thesis_list(lists["all"])
        return reply

    intro = (
        f"Përshëndetje {name}! "
        if scope_you
        else "Përshëndetje! Jam asistenti i panelit AAB. "
    )
    return (
        f"{intro}Aktualisht ka {stats['total_students']} studentë me tema dhe "
        f"{stats['total_theses']} tema në databazë.\n\n"
        "Pyetni p.sh.:\n"
        "• Sa studentë kemi në pritje?\n"
        "• Sa tema kanë kaluar plagjiaturën?\n"
        "• Krahaso temën 5 me 12\n"
        "• Sa % ngjashmëri ka tema 3 me arkivin?\n"
        "• Jep statistikat e plota"
    )
