from django.urls import path
from management_system.views import *
from management_system.forms import UserLoginForm

urlpatterns = [
    path(
        'login/',
        LoginView.as_view(
            template_name="login.html",
            authentication_form=UserLoginForm
            ),
        name='user_login'
    ),       
    path('', index, name="home"),
    path('profile/', ProfileDetail.as_view(), name="view-profile"),
    path('profile/<int:user_id>/', ProfileDetail.as_view(), name="check-profile"),
    path('profile/<int:user_id>/update/', ProfileUpdate.as_view(), name="update-profile"),
    path('courses/', view_courses, name="courses"),
    path('courses/<int:course_id>/', view_course_details, name="course-details"),
    path('courses/<int:course_id>/quiz/<int:quiz_id>/', take_exam, name="quiz-details"),
    path('courses/<int:course_id>/lesson/<int:lesson_id>/', view_lesson_details, name="lesson-details"),
    path('api/courses/<int:course_id>/quiz-status/', api_quiz_status, name="api-quiz-status"),
    path('lesson/<int:lesson_id>/<int:file_index>/', stream_lesson, name="lesson-stream"),
    path('lesson/<int:lesson_id>/audio/download/', generate_audio_download, name="audio-download"),
    
    # Admin Routes
    path('dashboard/', admin_panel, name="admin-panel"),
    
    # Users Dashboard
    path('dashboard/users/', user_dashboard, name="user-dashboard"),
    path('dashboard/users/create/', CreateUser.as_view(), name="user-create"),
    path('dashboard/users/bulk-create/', user_bulk_create, name="user-bulk-create"),
    path('dashboard/users/export-csv/', export_users_csv, name="export-users-csv"),
    path('dashboard/users/<int:user_id>/', index, name="user-profile"),
    path('dashboard/users/<int:user_id>/delete/', DeleteUser.as_view(), name="user-delete"),
    path('dashboard/users/<int:user_id>/update/', UpdateUser.as_view(), name="user-update"),
    
    # Courses Dashboard
    path('dashboard/courses/', course_dashboard, name="course-dashboard"),
    path('dashboard/courses/create/', CreateCourse.as_view(), name="course-create"),
    path('dashboard/courses/<int:course_id>/', index, name="course-view"),
    path('dashboard/courses/<int:course_id>/delete/', DeleteCourse.as_view(), name="course-delete"),
    path('dashboard/courses/<int:course_id>/update/', UpdateCourse.as_view(), name="course-update"),
    path('dashboard/courses/<int:course_id>/duplicate/', duplicate_course, name="course-duplicate"),
    
    # Lesson Dashboard
    path('dashboard/lessons/', lesson_dashboard, name="lesson-dashboard"),
    path('dashboard/lessons/<int:lesson_id>/', index, name="lesson-view"),
    path('dashboard/lessons/create/', create_lesson, name="lesson-create"),
    path('dashboard/lessons/<int:lesson_id>/update/', update_lesson, name="lesson-update"),
    path('dashboard/lessons/<int:lesson_id>/delete/', DeleteLesson.as_view(), name="lesson-delete"),
    path('dashboard/lessons/<int:lesson_id>/duplicate/', duplicate_lesson, name="lesson-duplicate"),
    path('dashboard/folder/<str:folder_id>/', navigate_folder, name="navigate-folder"),

    # Quiz Dashboard
    path('dashboard/quizzez/', quiz_dashboard, name="quiz-dashboard"),
    path('dashboard/quizzez/<int:quiz_id>/', index, name="quiz-view"),
    path('dashboard/quizzez/create/', create_quiz, name="quiz-create"),
    path('dashboard/quizzez/<int:quiz_id>/update/', update_quiz, name="quiz-update"),
    path('dashboard/quizzez/<int:quiz_id>/delete/', DeleteQuiz.as_view(), name="quiz-delete"),
    path('dashboard/quizzez/<int:quiz_id>/duplicate/', duplicate_quiz, name="quiz-duplicate"),

    # Submission Dashboard
    path('dashboard/quizzez/<int:quiz_id>/view-submission/', submission_dashboard, name="submission-dashboard"),
    path('dashboard/quizzez/<int:quiz_id>/view-submission/<int:user_id>/', submission_user, name="submission-user"),
    path('dashboard/quizzez/<int:quiz_id>/export-csv/', export_quiz_submissions_csv, name="export-quiz-submissions-csv"),
    path('dashboard/quizzez/<int:quiz_id>/summary-csv/', export_quiz_summary_csv, name="export-quiz-summary-csv"),
    path('dashboard/grade/<int:grade_id>/export-csv/', export_submission_csv, name="export-submission-csv"),
    path('dashboard/reports/transcript/', yearly_transcript_dashboard, name="yearly-transcript-dashboard"),
    path('dashboard/reports/transcript/export-csv/', export_yearly_transcript_csv, name="export-yearly-transcript-csv"),

    # Upload Videos
    path('dashboard/upload-files/', upload_file, name="upload-files"),
    path('api/get-presigned-url/', upload_link, name="upload-link"),
    
    # R2 File Management
    path('dashboard/r2-management/', r2_management_dashboard, name="r2-management-dashboard"),
    path('api/r2/files/list/', api_list_files, name="api-list-files"),
    path('api/r2/files/delete/', api_delete_file, name="api-delete-file"),
    path('api/r2/files/delete-m3u8/', api_delete_m3u8_file, name="api-delete-m3u8-file"),
    path('api/r2/files/delete-batch/', api_delete_files_batch, name="api-delete-files-batch"),
    path('api/r2/files/rename/', api_rename_file, name="api-rename-file"),
    path('api/r2/files/move/', api_move_file, name="api-move-file"),
    path('api/r2/files/metadata/', api_get_file_metadata, name="api-get-file-metadata"),
    path('api/r2/files/download/', api_download_file, name="api-download-file"),
    path('api/r2/files/search/', api_search_files, name="api-search-files"),
    path('api/r2/folder/create/', api_create_folder, name="api-create-folder"),
    path('api/r2/folder/delete/', api_delete_folder, name="api-delete-folder"),
    path('api/r2/stats/', api_get_storage_stats, name="api-storage-stats"),
    
    # Bulk Operations
    path('api/bulk-delete/users/', bulk_delete_users, name="bulk-delete-users"),
    path('api/bulk-delete/courses/', bulk_delete_courses, name="bulk-delete-courses"),
    path('api/bulk-delete/lessons/', bulk_delete_lessons, name="bulk-delete-lessons"),
    path('api/bulk-delete/quizzes/', bulk_delete_quizzes, name="bulk-delete-quizzes"),

    # Design Theme Showcase (pre-refactor visual review)
    path('showcase/', theme_catalog, name="theme-catalog"),
    path('showcase/<str:theme_id>/<str:page_id>/', theme_showcase, name="theme-showcase-page"),

    path('dashboard/course-offerings/<int:offering_id>/copy/', copy_course_offering, name="copy-course-offering"),

    # Phase 3 — Student Applications
    path('signup/', signup, name="signup"),
    path('dashboard/applications/', applications_dashboard, name="applications-dashboard"),
    path('dashboard/applications/<int:user_id>/', application_review, name="application-review"),
    path('dashboard/applications/<int:user_id>/<str:decision>/', application_decision, name="application-decision"),
    path('api/bulk-application-decision/', bulk_application_decision, name="bulk-application-decision"),

    # Phase 5 — Attendance Calendar, QR, and Scanning
    path('dashboard/calendar/', calendar_management, name="calendar-management"),
    path('dashboard/calendar/holiday/add/', add_holiday, name="add-holiday"),
    path('dashboard/calendar/holiday/<int:holiday_id>/delete/', delete_holiday, name="delete-holiday"),
    path('my-calendar/', student_calendar, name="student-calendar"),
    path('my-qr/', download_qr, name="download-qr"),
    path('dashboard/users/<int:user_id>/regenerate-qr/', regenerate_qr, name="regenerate-qr"),
    path('scanner/', scanner, name="scanner"),
    path('scan/<str:token>/', scan_preview, name="scan-preview"),
    path('scan/<str:token>/<str:action>/', record_attendance, name="record-attendance"),
    path('dashboard/attendance/', attendance_management, name="attendance-management"),
    path('dashboard/attendance/<int:record_id>/correct/', attendance_correction, name="attendance-correction"),
    path('dashboard/attendance/<int:record_id>/delete/', delete_attendance, name="attendance-delete"),

    # Phase 6 — Online Lecture Progress Tracking
    path('api/lesson/<int:lesson_id>/start-session/<str:part_id>/', start_viewing_session, name="start-viewing-session"),
    path('lesson/<int:lesson_id>/manifest/<int:file_index>/', lesson_manifest, name="lesson-manifest"),
    path('api/worker/receipt/', worker_receipt, name="worker-receipt"),
    path('api/progress/heartbeat/', progress_heartbeat, name="progress-heartbeat"),
    path('dashboard/progress/', progress_dashboard, name="progress-dashboard"),

    # Phase 7 — Reports and Exports
    path('dashboard/reports/', report_dashboard, name="report-dashboard"),
    path('dashboard/reports/export-csv/', export_report_csv, name="export-report-csv"),
    path('dashboard/reports/export-xlsx/', export_report_xlsx, name="export-report-xlsx"),
]