from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import get_current_user, require_admin_or_professor
from database import get_db
from models import Thesis, User, UserRole
from services.plagiarism_service import get_thesis_results, run_plagiarism_analysis

router = APIRouter()


@router.post("/thesis/{thesis_id}/analyze")
def analyze_thesis(
    thesis_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_professor),
):
    thesis = db.query(Thesis).filter(Thesis.id == thesis_id).first()
    if not thesis:
        raise HTTPException(status_code=404, detail="Thesis not found")

    try:
        result = run_plagiarism_analysis(thesis_id=thesis_id, db=db)
        return {"success": True, "result": result}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected analysis error: {exc}")


@router.get("/thesis/{thesis_id}/results")
def thesis_results(
    thesis_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return get_thesis_results(thesis_id=thesis_id, db=db, current_user=current_user)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to fetch plagiarism results: {exc}")
