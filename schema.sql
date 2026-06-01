-- ============================================================
-- DATABASE: plagiarism_checker
-- Universiteti AAB — Sistemi i Analizës së Plagjiaturës
-- ============================================================

CREATE DATABASE IF NOT EXISTS plagiarism_checker
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE plagiarism_checker;

-- ============================================================
-- 1. UNIVERSITIES
-- ============================================================
CREATE TABLE IF NOT EXISTS universities (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  name       VARCHAR(255) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO universities (name) VALUES
  ('Universiteti AAB'),
  ('Universiteti i Prishtinës'),
  ('Universiteti i Tiranës');

-- ============================================================
-- 2. USERS (Admin + Profesorë + Studentë)
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  username      VARCHAR(100) NOT NULL UNIQUE,
  email         VARCHAR(255) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  full_name     VARCHAR(255),
  role          ENUM('admin','professor','student') NOT NULL DEFAULT 'student',
  department    VARCHAR(255),
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_login    TIMESTAMP NULL,
  INDEX idx_users_role       (role),
  INDEX idx_users_department (department),
  INDEX idx_users_active     (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Admin default  (fjalëkalimi: Admin123)
INSERT INTO users (username, email, password_hash, full_name, role, department) VALUES
  ('admin',
   'admin@aab.edu.al',
  '$2b$12$t99B./KLmiGKD.vBTlCJe.34m/xkRjLqG7FZtB7.mYbkwVmhkhUmK',
  'Administrator AAB',
   'admin',
   NULL),
-- Profesor demo (fjalëkalimi: Prof@2024)
  ('prof.demo',
   'prof.demo@aab.edu.al',
  '$2b$12$34ybYNwkti8AUEAMxPJadOc0jA9.BnUJ13R0vH7UXNRNI8ew7X7..',
   'Prof. Demo Demoviqi',
   'professor',
   'Informatikë');

-- ============================================================
-- 3. PROFESSORS
-- ============================================================
CREATE TABLE IF NOT EXISTS professors (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  user_id    INT NOT NULL UNIQUE,
  full_name  VARCHAR(255) NOT NULL,
  department VARCHAR(255),
  is_active  BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  INDEX idx_professors_department (department),
  INDEX idx_professors_active     (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO professors (user_id, full_name, department)
SELECT u.id, COALESCE(u.full_name, u.username), u.department
FROM users u
WHERE u.role = 'professor'
  AND NOT EXISTS (
    SELECT 1 FROM professors p WHERE p.user_id = u.id
  );

-- ============================================================
-- 4. STUDENTS
-- ============================================================
CREATE TABLE IF NOT EXISTS students (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  user_id       INT NULL UNIQUE,
  university_id INT,
  full_name     VARCHAR(255) NOT NULL,
  student_id    VARCHAR(50)  NOT NULL UNIQUE,
  email         VARCHAR(255),
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  FOREIGN KEY (university_id) REFERENCES universities(id) ON DELETE SET NULL,
  INDEX idx_students_univ (university_id),
  INDEX idx_students_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 5. THESES (Temat e Diplomave — për analizë)
-- ============================================================
CREATE TABLE IF NOT EXISTS theses (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  student_id INT,
  submitted_by_user_id INT,
  assigned_professor_id INT,
  title      VARCHAR(500) NOT NULL,
  abstract   TEXT,
  content    LONGTEXT,
  file_path  VARCHAR(500),
  file_type  ENUM('pdf','docx','txt') DEFAULT 'pdf',
  year       INT,
  department VARCHAR(255),
  supervisor VARCHAR(255),
  workflow_status ENUM('in_process','approved','rejected','needs_revision') NOT NULL DEFAULT 'in_process',
  status     ENUM('pending','analyzing','completed','flagged') DEFAULT 'pending',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE SET NULL,
  FOREIGN KEY (submitted_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
  FOREIGN KEY (assigned_professor_id) REFERENCES professors(id) ON DELETE SET NULL,
  INDEX idx_theses_status     (status),
  INDEX idx_theses_department (department),
  INDEX idx_theses_year       (year),
  INDEX idx_theses_assigned_professor (assigned_professor_id),
  INDEX idx_theses_submitted_by (submitted_by_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 6. UPLOADED_FILES (Skedarët e ngarkuar)
-- ============================================================
CREATE TABLE IF NOT EXISTS uploaded_files (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  thesis_id   INT NOT NULL UNIQUE,
  file_name   VARCHAR(500) NOT NULL,
  file_path   VARCHAR(500) NOT NULL,
  file_type   VARCHAR(20),
  file_size   INT,
  uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (thesis_id) REFERENCES theses(id) ON DELETE CASCADE,
  INDEX idx_uploaded_files_thesis (thesis_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 7. NOTIFICATIONS (Njoftimet për caktim teme)
-- ============================================================
CREATE TABLE IF NOT EXISTS notifications (
  id                     INT AUTO_INCREMENT PRIMARY KEY,
  recipient_professor_id INT NOT NULL,
  thesis_id              INT NOT NULL,
  title                  VARCHAR(255) NOT NULL,
  message                TEXT NOT NULL,
  is_read                BOOLEAN NOT NULL DEFAULT FALSE,
  created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (recipient_professor_id) REFERENCES professors(id) ON DELETE CASCADE,
  FOREIGN KEY (thesis_id) REFERENCES theses(id) ON DELETE CASCADE,
  INDEX idx_notifications_professor (recipient_professor_id),
  INDEX idx_notifications_thesis    (thesis_id),
  INDEX idx_notifications_is_read   (is_read)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 8. THESIS_MESSAGES (Mesazhe student-profesor-admin)
-- ============================================================
CREATE TABLE IF NOT EXISTS thesis_messages (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  thesis_id      INT NOT NULL,
  sender_user_id INT NOT NULL,
  message_text   TEXT NOT NULL,
  created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (thesis_id) REFERENCES theses(id) ON DELETE CASCADE,
  FOREIGN KEY (sender_user_id) REFERENCES users(id) ON DELETE CASCADE,
  INDEX idx_thesis_messages_thesis (thesis_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 9. THESIS_FEEDBACK (Feedback i profesorit)
-- ============================================================
CREATE TABLE IF NOT EXISTS thesis_feedback (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  thesis_id     INT NOT NULL,
  professor_id  INT NOT NULL,
  feedback_text TEXT NOT NULL,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (thesis_id) REFERENCES theses(id) ON DELETE CASCADE,
  FOREIGN KEY (professor_id) REFERENCES professors(id) ON DELETE CASCADE,
  INDEX idx_thesis_feedback_thesis (thesis_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 10. THESIS_EVALUATIONS (Vlerësimi final)
-- ============================================================
CREATE TABLE IF NOT EXISTS thesis_evaluations (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  thesis_id       INT NOT NULL UNIQUE,
  professor_id    INT NOT NULL,
  grade           VARCHAR(20) NOT NULL,
  evaluation_text TEXT NOT NULL,
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (thesis_id) REFERENCES theses(id) ON DELETE CASCADE,
  FOREIGN KEY (professor_id) REFERENCES professors(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 11. REVIEWS (Vlerësimet e profesorëve)
-- ============================================================
CREATE TABLE IF NOT EXISTS reviews (
  id                    INT AUTO_INCREMENT PRIMARY KEY,
  thesis_id             INT NOT NULL UNIQUE,
  professor_id          INT NOT NULL,
  status                ENUM('in_process','approved','rejected','needs_revision') NOT NULL,
  comments              TEXT,
  plagiarism_percentage  DECIMAL(5,2) DEFAULT 0.00,
  reviewed_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (thesis_id) REFERENCES theses(id) ON DELETE CASCADE,
  FOREIGN KEY (professor_id) REFERENCES professors(id) ON DELETE CASCADE,
  INDEX idx_reviews_thesis (thesis_id),
  INDEX idx_reviews_professor (professor_id),
  INDEX idx_reviews_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 12. TOPIC_REQUESTS (Kërkesat për tema)
-- ============================================================
CREATE TABLE IF NOT EXISTS topic_requests (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  student_id    INT NOT NULL,
  professor_id  INT NOT NULL,
  thesis_id     INT NOT NULL,
  note          TEXT,
  status        ENUM('pending','approved','rejected') DEFAULT 'pending',
  requested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
  FOREIGN KEY (professor_id) REFERENCES professors(id) ON DELETE CASCADE,
  FOREIGN KEY (thesis_id) REFERENCES theses(id) ON DELETE CASCADE,
  INDEX idx_topic_requests_student  (student_id),
  INDEX idx_topic_requests_professor (professor_id),
  INDEX idx_topic_requests_thesis    (thesis_id),
  INDEX idx_topic_requests_status    (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 13. CONFIDENTIAL_DOCUMENTS (Dokumentet konfidenciale)
-- ============================================================
CREATE TABLE IF NOT EXISTS confidential_documents (
  id                INT AUTO_INCREMENT PRIMARY KEY,
  title             VARCHAR(500) NOT NULL,
  author_name       VARCHAR(255),
  department        VARCHAR(255) NOT NULL,
  year              INT,
  doc_type          VARCHAR(100) DEFAULT 'tema_diplome',
  encrypted_content LONGBLOB     NOT NULL,
  content_hash      VARCHAR(64)  UNIQUE,
  content_length    INT,
  uploaded_by       INT,
  status            ENUM('active','archived') DEFAULT 'active',
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_conf_dept   (department),
  INDEX idx_conf_status (status),
  INDEX idx_conf_year   (year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 14. PLAGIARISM_RESULTS (Rezultatet e analizës)
-- ============================================================
CREATE TABLE IF NOT EXISTS plagiarism_results (
  id                 INT AUTO_INCREMENT PRIMARY KEY,
  thesis_id          INT NOT NULL,
  overall_score      DECIMAL(5,2) DEFAULT 0.00,
  internal_score     DECIMAL(5,2) DEFAULT 0.00,
  confidential_score DECIMAL(5,2) DEFAULT 0.00,
  web_score          DECIMAL(5,2) DEFAULT 0.00,
  ai_analysis        LONGTEXT,
  status             ENUM('pending','completed','error') DEFAULT 'pending',
  analyzed_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (thesis_id) REFERENCES theses(id) ON DELETE CASCADE,
  INDEX idx_results_thesis  (thesis_id),
  INDEX idx_results_status  (status),
  INDEX idx_results_overall (overall_score)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 15. PLAGIARISM_MATCHES (Ndeshjet e gjetura)
-- ============================================================
CREATE TABLE IF NOT EXISTS plagiarism_matches (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  result_id        INT NOT NULL,
  source_type      ENUM('confidential','internal','web') NOT NULL,
  conf_source_id   INT NULL,
  source_url       VARCHAR(1000),
  source_title     VARCHAR(500),
  original_text    TEXT,
  similarity_score DECIMAL(5,2),
  paragraph_index  INT DEFAULT 0,
  created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (result_id)      REFERENCES plagiarism_results(id)     ON DELETE CASCADE,
  FOREIGN KEY (conf_source_id) REFERENCES confidential_documents(id) ON DELETE SET NULL,
  INDEX idx_matches_result (result_id),
  INDEX idx_matches_type   (source_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- 16. REPORTS (Raportet e gjeneruara)
-- ============================================================
CREATE TABLE IF NOT EXISTS reports (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  thesis_id    INT NOT NULL,
  result_id    INT NOT NULL,
  report_text  LONGTEXT,
  report_path  VARCHAR(500),
  generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (thesis_id) REFERENCES theses(id)            ON DELETE CASCADE,
  FOREIGN KEY (result_id) REFERENCES plagiarism_results(id) ON DELETE CASCADE,
  INDEX idx_reports_thesis (thesis_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================
-- KONFIRMO
-- ============================================================
SELECT 'plagiarism_checker u krijua me sukses!' AS rezultati;