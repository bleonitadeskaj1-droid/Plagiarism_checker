from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from database import get_db
from models import Student

router = APIRouter()


class StudentCreate(BaseModel):
    university_id: Optional[int] = None
    full_name: str
    student_id: str
    email: Optional[str] = None


class StudentUpdate(BaseModel):
    university_id: Optional[int] = None
    full_name: Optional[str] = None
    student_id: Optional[str] = None
    email: Optional[str] = None


@router.post("/")
def create_student(data: StudentCreate, db: Session = Depends(get_db)):
    existing = db.query(Student).filter(Student.student_id == data.student_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Student with this student_id already exists")
    s = Student(
        university_id=data.university_id,
        full_name=data.full_name,
        student_id=data.student_id,
        email=data.email
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": s.id, "student_id": s.student_id}


@router.get("/")
def list_students(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    q = db.query(Student).offset(skip).limit(limit).all()
    return [{"id": s.id, "full_name": s.full_name, "student_id": s.student_id, "email": s.email} for s in q]


@router.get("/{student_id}")
def get_student(student_id: int, db: Session = Depends(get_db)):
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Student not found")
    return {"id": s.id, "full_name": s.full_name, "student_id": s.student_id, "email": s.email}


@router.put("/{student_id}")
def update_student(student_id: int, data: StudentUpdate, db: Session = Depends(get_db)):
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Student not found")
    for k, v in data.dict(exclude_unset=True).items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return {"id": s.id}


@router.delete("/{student_id}")
def delete_student(student_id: int, db: Session = Depends(get_db)):
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Student not found")
    db.delete(s)
    db.commit()
    return {"message": "deleted"}
