# ============================================
# models.py - SQLAlchemy Modelet (i perditesuar)
# ============================================

from sqlalchemy import (
    Column, Integer, String, Text, LargeBinary, Boolean,
    DECIMAL, Enum, ForeignKey, TIMESTAMP, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
import enum


class UserRole(str, enum.Enum):
    admin      = "admin"
    professor  = "professor"
    student    = "student"

class ThesisStatus(str, enum.Enum):
    pending   = "pending"
    analyzing = "analyzing"
    completed = "completed"
    flagged   = "flagged"


class SubmissionWorkflowStatus(str, enum.Enum):
    in_process     = "in_process"
    approved       = "approved"
    rejected       = "rejected"
    needs_revision = "needs_revision"

class DocStatus(str, enum.Enum):
    active   = "active"
    archived = "archived"


class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String(100), unique=True, nullable=False)
    email         = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name     = Column(String(255))
    role          = Column(Enum(UserRole), default=UserRole.student, nullable=False)
    department    = Column(String(255))
    is_active     = Column(Boolean, default=True)
    created_at    = Column(TIMESTAMP, server_default=func.now())
    last_login    = Column(TIMESTAMP, nullable=True)
    professor_profile = relationship("Professor", back_populates="user", uselist=False)
    uploaded_conf_docs = relationship("ConfidentialDocument", back_populates="uploaded_by_user")
    submitted_theses = relationship("Thesis", back_populates="submitted_by_user", foreign_keys="Thesis.submitted_by_user_id")


class ConfidentialDocument(Base):
    __tablename__ = "confidential_documents"
    id                = Column(Integer, primary_key=True, index=True)
    title             = Column(String(500), nullable=False)
    author_name       = Column(String(255))
    department        = Column(String(255), nullable=False)
    year              = Column(Integer)
    doc_type          = Column(String(100))
    encrypted_content = Column(LargeBinary, nullable=False)
    content_hash      = Column(String(64), unique=True)
    content_length    = Column(Integer)
    uploaded_by       = Column(Integer, ForeignKey("users.id"))
    uploaded_by_user  = relationship("User", back_populates="uploaded_conf_docs")
    status            = Column(Enum(DocStatus), default=DocStatus.active)
    created_at        = Column(TIMESTAMP, server_default=func.now())
    matches_as_source = relationship("PlagiarismMatch", back_populates="conf_source_doc",
                                     foreign_keys="PlagiarismMatch.conf_source_id")


class Student(Base):
    __tablename__ = "students"
    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=True, unique=True)
    full_name     = Column(String(255), nullable=False)
    student_id    = Column(String(50), unique=True, nullable=False)
    email         = Column(String(255))
    created_at    = Column(TIMESTAMP, server_default=func.now())
    theses        = relationship("Thesis", back_populates="student")


class Thesis(Base):
    __tablename__ = "theses"
    id         = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=True)
    submitted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_professor_id = Column(Integer, ForeignKey("professors.id"), nullable=True)
    title      = Column(String(500), nullable=False)
    abstract   = Column(Text)
    content    = Column(Text)
    file_path  = Column(String(500))
    file_type  = Column(Enum("pdf","docx","txt"), default="pdf")
    year       = Column(Integer)
    department = Column(String(255))
    supervisor = Column(String(255))
    workflow_status = Column(Enum(SubmissionWorkflowStatus), default=SubmissionWorkflowStatus.in_process, nullable=False)
    status     = Column(Enum(ThesisStatus), default=ThesisStatus.pending)
    is_archive = Column(Boolean, default=False, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
    student    = relationship("Student", back_populates="theses")
    submitted_by_user = relationship("User", back_populates="submitted_theses", foreign_keys=[submitted_by_user_id])
    assigned_professor = relationship("Professor", back_populates="assigned_theses")
    uploaded_files = relationship("UploadedFile", back_populates="thesis", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="thesis", cascade="all, delete-orphan")
    results    = relationship("PlagiarismResult", back_populates="thesis")
    notifications = relationship("Notification", back_populates="thesis")


class Professor(Base):
    __tablename__ = "professors"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    full_name  = Column(String(255), nullable=False)
    department = Column(String(255))
    is_active  = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP, server_default=func.now())

    user = relationship("User", back_populates="professor_profile")
    assigned_theses = relationship("Thesis", back_populates="assigned_professor")
    notifications = relationship("Notification", back_populates="recipient_professor")


class UploadedFile(Base):
    __tablename__ = "uploaded_files"
    id            = Column(Integer, primary_key=True, index=True)
    thesis_id     = Column(Integer, ForeignKey("theses.id"), nullable=False, unique=True)
    file_name     = Column(String(500), nullable=False)
    file_path     = Column(String(500), nullable=False)
    file_type     = Column(String(20))
    file_size     = Column(Integer)
    uploaded_at   = Column(TIMESTAMP, server_default=func.now())

    thesis = relationship("Thesis", back_populates="uploaded_files")


class Review(Base):
    __tablename__ = "reviews"
    id                    = Column(Integer, primary_key=True, index=True)
    thesis_id             = Column(Integer, ForeignKey("theses.id"), nullable=False, unique=True)
    professor_id          = Column(Integer, ForeignKey("professors.id"), nullable=False)
    status                = Column(Enum(SubmissionWorkflowStatus), nullable=False)
    comments              = Column(Text)
    plagiarism_percentage  = Column(DECIMAL(5,2), default=0.00)
    reviewed_at           = Column(TIMESTAMP, server_default=func.now())

    thesis = relationship("Thesis", back_populates="reviews")
    professor = relationship("Professor")


class Notification(Base):
    __tablename__ = "notifications"
    id                     = Column(Integer, primary_key=True, index=True)
    recipient_professor_id = Column(Integer, ForeignKey("professors.id"), nullable=False)
    thesis_id              = Column(Integer, ForeignKey("theses.id"), nullable=False)
    title                  = Column(String(255), nullable=False)
    message                = Column(Text, nullable=False)
    is_read                = Column(Boolean, default=False)
    created_at             = Column(TIMESTAMP, server_default=func.now())

    recipient_professor = relationship("Professor", back_populates="notifications")
    thesis = relationship("Thesis", back_populates="notifications")


class PlagiarismResult(Base):
    __tablename__ = "plagiarism_results"
    id                  = Column(Integer, primary_key=True, index=True)
    thesis_id           = Column(Integer, ForeignKey("theses.id"), nullable=False)
    overall_score       = Column(DECIMAL(5,2), default=0.00)
    internal_score      = Column(DECIMAL(5,2), default=0.00)
    confidential_score  = Column(DECIMAL(5,2), default=0.00)
    web_score           = Column(DECIMAL(5,2), default=0.00)
    ai_analysis         = Column(Text)
    risk_level          = Column(String(20))
    status              = Column(Enum("pending","completed","error"), default="pending")
    analyzed_at         = Column(TIMESTAMP, server_default=func.now())
    thesis              = relationship("Thesis", back_populates="results")
    matches             = relationship("PlagiarismMatch", back_populates="result")


class PlagiarismMatch(Base):
    __tablename__ = "plagiarism_matches"
    id               = Column(Integer, primary_key=True, index=True)
    result_id        = Column(Integer, ForeignKey("plagiarism_results.id"), nullable=False)
    source_type      = Column(Enum("confidential","internal","web"), nullable=False)
    conf_source_id   = Column(Integer, ForeignKey("confidential_documents.id"), nullable=True)
    conf_source_doc  = relationship("ConfidentialDocument", back_populates="matches_as_source",
                                    foreign_keys=[conf_source_id])
    source_url       = Column(String(1000), nullable=True)
    source_title     = Column(String(500))
    original_text    = Column(Text)
    similarity_score = Column(DECIMAL(5,2))
    paragraph_index  = Column(Integer)
    created_at       = Column(TIMESTAMP, server_default=func.now())
    result           = relationship("PlagiarismResult", back_populates="matches")


class SystemSetting(Base):
    __tablename__ = "system_settings"
    id = Column(Integer, primary_key=True, index=True)
    group_name = Column(String(100), nullable=False, index=True)
    setting_key = Column(String(100), nullable=False)
    setting_value = Column(Text)
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("group_name", "setting_key", name="uq_system_settings_group_key"),
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(TIMESTAMP, nullable=False)
    used_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    user = relationship("User")


class AuditLog(Base):
    """Immutable-ish audit trail for admin/professor actions."""
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    username = Column(String(100))
    role = Column(String(50), index=True)
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(100))
    resource_id = Column(String(64))
    method = Column(String(10))
    path = Column(String(500))
    status_code = Column(Integer)
    ip_address = Column(String(64))
    details = Column(Text)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    user = relationship("User")