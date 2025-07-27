
# ✅ LMS Feature Checklist (Text Version)

## 👤 Authentication & User Roles
- [ ] User registration (email/password)
- [ ] Login/Logout
- [ ] Role-based access (Student / Content Manager / Admin)
- [ ] Password reset via email
- [ ] User profile management

## 👨‍🎓 Student (Learner) Features
- [ ] Student dashboard (with current year courses/modules)
- [ ] View enrolled courses
- [ ] Access course detail page
- [ ] Lecture access (video embed via Google Drive)
- [ ] Audio lectures (protected)
- [ ] E-book / PDF viewer (secured)
- [ ] Quiz list per course
- [ ] Attempt quiz (with timer)
- [ ] Auto-submit on timeout
- [ ] Submit quiz manually
- [ ] Review submitted quiz answers
- [ ] Show correct/wrong answers (auto-graded)
- [ ] Manual-graded questions marked as “Pending Review”
- [ ] Final grade view (auto + manual grades)
- [ ] Student progress tracking
- [ ] Profile page with quiz results, badges, etc.

## 👨‍🏫 Content Manager / Teacher Features
- [ ] Login to dashboard
- [ ] Create / Edit / Delete course modules
- [ ] Assign modules to student levels
- [ ] Add lectures (Video, Audio, PDF)
- [ ] Google Drive embed for videos
- [ ] Upload protected PDFs and audio to Cloudflare R2
- [ ] Create quizzes with:
  - [ ] MCQs
  - [ ] Matching
  - [ ] Short Answer
  - [ ] Long Answer
- [ ] Edit / Delete quizzes
- [ ] Publish / Unpublish quizzes
- [ ] Manual grading interface
- [ ] View student submissions by course/quiz
- [ ] Grading report per quiz or per student
- [ ] Promote students to next academic year
- [ ] Export results (CSV or PDF)

## ⚙️ Admin Dashboard
- [ ] Dashboard landing page (stats/metrics)
- [ ] View all users & roles
- [ ] Add / Edit / Delete student accounts
- [ ] Assign users to levels
- [ ] View course/module stats
- [ ] View quiz performance summaries
- [ ] Logging system (action logs, user logs)
- [ ] Success/error messages on actions
- [ ] Confirmation popups on delete

## 💡 UX / Frontend / HTMX Enhancements
- [ ] Pagination UI
- [ ] Dynamic pagination with query parameters
- [ ] HTMX include/inherit filters & search input
- [ ] Modal forms for create/update
- [ ] Toast messages for form feedback
- [ ] Navigation bar for student views
- [ ] Breadcrumb navigation for admin

## 🔒 Security & Access Control
- [ ] Secure URLs based on user role
- [ ] Prevent download of PDF/audio (JS or server-side)
- [ ] File access tokens (if implemented)
- [ ] Cloudflare R2 permissions configured
- [ ] Limit quiz access (time-based, start/end date)

## 🧪 Testing
- [ ] Unit tests for models
- [ ] Tests for quiz logic
- [ ] Tests for student grading pipeline
- [ ] Tests for access permissions
- [ ] JS/HTMX behavior tests (if needed)

## 📦 Deployment / DevOps
- [ ] .env & settings split (local/prod)
- [ ] Static/media files configured
- [ ] Cloudflare R2 integration
- [ ] Database backup system
- [ ] Dockerfile / deployment script
