"""Lista e departamenteve për frontend."""

from typing import Optional

from fastapi import APIRouter, Query

from departments import PROFESSOR_DEPARTMENTS, STUDENT_SPECIALIZATIONS

router = APIRouter()


@router.get("/departments")
def list_departments(for_role: Optional[str] = Query(None, alias="for")):
    """Profesorë: Shkenca Kompjuterike. Studentë: drejtimet / specializimet."""
    if for_role == "professor":
        return {"departments": PROFESSOR_DEPARTMENTS}
    return {"departments": STUDENT_SPECIALIZATIONS}
