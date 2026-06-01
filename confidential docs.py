# ============================================
# routers/confidential_docs.py
# Upload dokumentesh konfidenciale nga Admin/Profesor
# RREGULL: permbajtja KURRE nuk kthehet ne frontend
# ============================================

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.orm import Session
from typing import Optional
import os, shutil

from database import get_db
from models import ConfidentialDocument, User, UserRole, DocStatus
from auth import require_admin_or_professor, require_admin
from confidential import encrypt_content, hash_content
from routers.theses import extract_text   # ripërdorim funksionin ekzistues

router = APIRouter()

UPLOAD_DIR = "uploads/confidential"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ── NGARKO DOKUMENT KONFIDENCIAL ──
@router.post("/upload")
async def upload_confidential(
    file:        UploadFile = File(...),
    title:       str        = Form(...),
    author_name: str        = Form(...),
    department:  str        = Form(...),
    year:        int        = Form(...),
    doc_type:    str        = Form("tema_diplome"),
    db:          Session    = Depends(get_db),
    current_user: User      = Depends(require_admin_or_professor)
):
    """
    Admin ose Profesor ngarkon dokument konfidencial.
    Profesori mund të ngarkojë vetëm për departamentin e tij.
    """
    # Profesori vetëm për departamentin e tij
    if current_user.role == UserRole.professor:
        if current_user.department and current_user.department != department:
            raise HTTPException(
                status_code=403,
                detail=f"Mund të ngarkoni vetëm për departamentin: {current_user.department}"
            )

    # Kontrollo llojin
    ext = file.filename.split(".")[-1].lower()
    if ext not in ["pdf", "docx", "txt"]:
        raise HTTPException(status_code=400, detail="Vetëm PDF, DOCX, TXT pranohen")

    # Ruaj përkohësisht për ekstraktim
    tmp_path = os.path.join(UPLOAD_DIR, f"tmp_{file.filename}")
    with open(tmp_path, "wb") as buf:
        shutil.copyfileobj(file.file, buf)

    # Ekstrakto tekstin
    plain_text = extract_text(tmp_path, ext)
    os.remove(tmp_path)   # fshi skedarin e paenkriptuar menjëherë

    if not plain_text or len(plain_text.strip()) < 100:
        raise HTTPException(status_code=400, detail="Dokumenti është bosh ose shumë i shkurtër")

    # Kontrollo duplikate me hash
    content_hash = hash_content(plain_text)
    existing = db.query(ConfidentialDocument).filter(
        ConfidentialDocument.content_hash == content_hash
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Ky dokument ekziston tashmë në sistem")

    # Enkipto dhe ruaj — permbajtja e qartë nuk ruhet askund
    encrypted = encrypt_content(plain_text)

    doc = ConfidentialDocument(
        title             = title,
        author_name       = author_name,
        department        = department,
        year              = year,
        doc_type          = doc_type,
        encrypted_content = encrypted,
        content_hash      = content_hash,
        content_length    = len(plain_text),
        uploaded_by       = current_user.id,
        status            = DocStatus.active
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    return {
        "message":        "Dokumenti u ngarkua dhe enkriptua me sukses",
        "id":             doc.id,
        "title":          doc.title,
        "department":     doc.department,
        "year":           doc.year,
        "content_length": doc.content_length,
        # NOTE: encrypted_content dhe teksti i qartë KURRE nuk kthehen
    }


# ── LISTA E DOKUMENTEVE (vetëm metadata) ──
@router.get("/")
def list_confidential_docs(
    department: Optional[str] = Query(None),
    year:       Optional[int] = Query(None),
    doc_type:   Optional[str] = Query(None),
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_professor)
):
    """
    Kthen vetëm METADATA — titulli, departamenti, viti.
    Permbajtja dhe enkriptimi KURRE nuk kthehen.
    """
    query = db.query(ConfidentialDocument).filter(
        ConfidentialDocument.status == DocStatus.active
    )

    # Profesori sheh vetëm departamentin e tij
    if current_user.role == UserRole.professor and current_user.department:
        query = query.filter(ConfidentialDocument.department == current_user.department)
    elif department:
        query = query.filter(ConfidentialDocument.department == department)

    if year:
        query = query.filter(ConfidentialDocument.year == year)
    if doc_type:
        query = query.filter(ConfidentialDocument.doc_type == doc_type)

    total = query.count()
    docs  = query.order_by(ConfidentialDocument.created_at.desc()).offset(skip).limit(limit).all()

    return {
        "total": total,
        "items": [_safe_metadata(d) for d in docs]
    }


# ── DETAJE (vetëm metadata) ──
@router.get("/{doc_id}")
def get_confidential_doc(
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_professor)
):
    doc = _get_doc_or_404(doc_id, db)
    _check_department_access(doc, current_user)
    return _safe_metadata(doc)


# ── ARKIVO (jo fshij - për audit trail) ──
@router.patch("/{doc_id}/archive")
def archive_document(
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)   # Vetëm admini
):
    doc = _get_doc_or_404(doc_id, db)
    doc.status = DocStatus.archived
    db.commit()
    return {"message": f"Dokumenti '{doc.title}' u arkivua"}


# ── STATISTIKA ──
@router.get("/stats/summary")
def confidential_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_professor)
):
    query = db.query(ConfidentialDocument).filter(ConfidentialDocument.status == DocStatus.active)

    if current_user.role == UserRole.professor and current_user.department:
        query = query.filter(ConfidentialDocument.department == current_user.department)

    docs = query.all()
    by_dept = {}
    for d in docs:
        by_dept[d.department] = by_dept.get(d.department, 0) + 1

    return {
        "total_documents": len(docs),
        "by_department":   by_dept,
        "total_characters": sum(d.content_length or 0 for d in docs)
    }


# ════════════════════════════════════════
# FUNKSION INTERN — perdoret nga AI agjenti
# KURRE nuk ekspozohet si endpoint
# ════════════════════════════════════════
def get_decrypted_for_analysis(doc_id: int, db: Session) -> str:
    """
    Kthen tekstin e dekriptuar VETEM per perdorim intern nga AI agjenti.
    Ky funksion NUK eshte endpoint - nuk thirret nga frontend.
    """
    from confidential import decrypt_content
    doc = db.query(ConfidentialDocument).filter(
        ConfidentialDocument.id == doc_id,
        ConfidentialDocument.status == DocStatus.active
    ).first()
    if not doc:
        return ""
    return decrypt_content(doc.encrypted_content)


def get_all_active_for_analysis(db: Session, department: str = None) -> list[dict]:
    """
    Kthen të gjitha dokumentet e dekriptuara për krahasim nga AI.
    KURRE nuk thirret nga endpoints publike.
    """
    from confidential import decrypt_content
    query = db.query(ConfidentialDocument).filter(ConfidentialDocument.status == DocStatus.active)
    if department:
        query = query.filter(ConfidentialDocument.department == department)

    result = []
    for doc in query.all():
        try:
            plain = decrypt_content(doc.encrypted_content)
            result.append({
                "id":         doc.id,
                "title":      doc.title,
                "department": doc.department,
                "year":       doc.year,
                "content":    plain[:3000]   # max 3000 karaktere per krahasim
            })
        except Exception:
            continue   # Nëse dekriptimi dështon, kalon
    return result


# ── HELPERS ──
def _safe_metadata(doc: ConfidentialDocument) -> dict:
    """Kthen vetëm metadata - asnjë permbajtje."""
    return {
        "id":             doc.id,
        "title":          doc.title,
        "author_name":    doc.author_name,
        "department":     doc.department,
        "year":           doc.year,
        "doc_type":       doc.doc_type,
        "content_length": doc.content_length,
        "status":         doc.status,
        "uploaded_by":    doc.uploaded_by,
        "created_at":     doc.created_at.isoformat() if doc.created_at else None,
        # encrypted_content: KURRE
        # content:           KURRE
    }

def _get_doc_or_404(doc_id: int, db: Session) -> ConfidentialDocument:
    doc = db.query(ConfidentialDocument).filter(ConfidentialDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Dokumenti nuk u gjet")
    return doc

def _check_department_access(doc: ConfidentialDocument, user: User):
    if user.role == UserRole.professor and user.department:
        if doc.department != user.department:
            raise HTTPException(status_code=403, detail="Nuk keni akses në këtë departament")