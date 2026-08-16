from django.urls import path
from management_system.views import *
from management_system.forms import UserLoginForm
from management_system.telegram_views import (
    telegram_attachment,
    telegram_broadcast_confirm,
    telegram_broadcast_confirm_page,
    telegram_broadcast_cancel,
    telegram_broadcast_create,
    telegram_broadcast_detail,
    telegram_broadcast_retry,
    telegram_broadcasts,
    telegram_config,
    telegram_conversation_detail,
    telegram_conversation_reply,
    telegram_conversations,
    telegram_start_conversation,
    telegram_unlink,
)

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
    path('portal/', portal, name="student-portal"),
    path('about/', about_page, name="about"),
    path('program/', program_page, name="program"),
    path('profile/', ProfileDetail.as_view(), name="view-profile"),
    path('profile/<int:user_id>/', ProfileDetail.as_view(), name="check-profile"),
    path('profile/missing-documents/', profile_missing_documents, name="profile-missing-documents"),
    path('profile/telegram/unlink/', telegram_unlink, name="telegram-unlink"),
    path('courses/', view_courses, name="courses"),
    # path('design/student-ui/', student_ui_proposal, name="student-ui-proposal"),
    # path('design/admin-ui/', admin_ui_proposal, name="admin-ui-proposal"),
    path('offerings/<int:offering_id>/', view_course_details, name="course-details"),
    path('offerings/<int:offering_id>/quiz/<int:quiz_id>/', take_exam, name="quiz-details"),
    path('offerings/<int:offering_id>/lesson/<int:lesson_id>/', view_lesson_details, name="lesson-details"),
    path('api/offerings/<int:offering_id>/quiz-status/', api_quiz_status, name="api-quiz-status"),
    path('offerings/<int:offering_id>/lesson/<int:lesson_id>/<int:file_index>/', stream_lesson, name="lesson-stream"),
    path('offerings/<int:offering_id>/lesson/<int:lesson_id>/audio/download/', generate_audio_download, name="audio-download"),
    
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
    path('dashboard/users/historical-intake/', historical_intake, name="historical-intake"),
    path('dashboard/users/historical-intake/<int:summary_id>/promote/', historical_promote, name="historical-promote"),
    path('dashboard/users/<int:user_id>/exceptional-courses/', exceptional_course_assign, name="exceptional-course-assign"),
    
    # Courses Dashboard
    path('dashboard/courses/', course_dashboard, name="course-dashboard"),
    path('dashboard/courses/create/', CreateCourse.as_view(), name="course-create"),
    path('dashboard/courses/<int:course_id>/', index, name="course-view"),
    path('dashboard/courses/<int:course_id>/delete/', DeleteCourse.as_view(), name="course-delete"),
    path('dashboard/courses/<int:course_id>/update/', UpdateCourse.as_view(), name="course-update"),
    path('dashboard/courses/<int:course_id>/duplicate/', duplicate_course, name="course-duplicate"),
    
    # Lesson Dashboard
    path('dashboard/lessons/', lesson_dashboard, name="lesson-dashboard"),
    path('dashboard/lessons/<int:lesson_id>/', lesson_detail, name="lesson-view"),
    path('dashboard/lessons/create/', create_lesson, name="lesson-create"),
    path('dashboard/lessons/<int:lesson_id>/update/', update_lesson, name="lesson-update"),
    path('dashboard/lessons/<int:lesson_id>/delete/', DeleteLesson.as_view(), name="lesson-delete"),
    path('dashboard/lessons/<int:lesson_id>/duplicate/', duplicate_lesson, name="lesson-duplicate"),
    path('dashboard/folder/', navigate_folder, name="navigate-folder-root"),
    path('dashboard/folder/<path:folder_id>/', navigate_folder, name="navigate-folder"),

    # Quiz Dashboard
    path('dashboard/quizzez/', quiz_dashboard, name="quiz-dashboard"),
    path('dashboard/quizzez/<int:quiz_id>/', quiz_detail, name="quiz-view"),
    path('dashboard/quizzez/<int:quiz_id>/exceptional-opening/', quiz_exceptional_opening, name="quiz-exceptional-opening"),
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
    path('dashboard/reports/grades/', grades_matrix_dashboard, name="grades-matrix-dashboard"),
    path('dashboard/reports/grades/export-xlsx/', export_grades_matrix_xlsx, name="export-grades-matrix-xlsx"),

    # Upload Videos
    path('dashboard/upload-files/', upload_file, name="upload-files"),
    path('api/media/jobs/', media_jobs_collection, name="media-job-create"),
    path('api/media/jobs/<uuid:job_uuid>/source-complete/', media_job_source_complete, name="media-job-source-complete"),
    path('api/media/jobs/<uuid:job_uuid>/', media_job_status, name="media-job-status"),
    path('api/media/jobs/<uuid:job_uuid>/retry/', media_job_retry, name="media-job-retry"),
    path('api/media/jobs/<uuid:job_uuid>/attachment-retry/', media_job_attachment_retry, name="media-job-attachment-retry"),
    path('dashboard/media-processing/', media_processing_status, name="media-processing-status"),
    
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

    path('dashboard/academic/copy/', copy_course_offerings, name="copy-course-offerings"),

    # Academic Setup
    path('dashboard/levels/', levels_dashboard, name="levels-dashboard"),
    path('dashboard/levels/create/', level_create, name="level-create"),
    path('dashboard/levels/<int:level>/edit/', level_edit, name="level-edit"),
    path('dashboard/levels/<int:level>/delete/', level_delete, name="level-delete"),
    path('dashboard/academic/', academic_setup, name="academic-setup"),
    path('dashboard/academic/year/create/', academic_year_create, name="academic-year-create"),
    path('dashboard/academic/year/<int:year_id>/edit/', academic_year_edit, name="academic-year-edit"),
    path('dashboard/academic/year/<int:year_id>/activate/', academic_year_activate, name="academic-year-activate"),
    path('dashboard/academic/year/<int:year_id>/delete/', academic_year_delete, name="academic-year-delete"),
    path('dashboard/academic/year-level/<int:scope_id>/weekdays/', academic_year_level_weekdays, name="academic-year-level-weekdays"),
    path('dashboard/academic/year-level/<int:scope_id>/delete/', academic_year_level_delete, name="academic-year-level-delete"),
    path('dashboard/academic/year-level/<int:scope_id>/offerings/', academic_offerings_by_scope, name="academic-offerings-by-scope"),
    path('dashboard/academic/offering/create/', course_offering_create, name="course-offering-create"),
    path('dashboard/academic/offering/<int:offering_id>/edit/', course_offering_edit, name="course-offering-edit"),
    path('dashboard/academic/offering/<int:offering_id>/delete/', course_offering_delete, name="course-offering-delete"),
    path('dashboard/academic/promotion-formula/', promotion_formula, name="promotion-formula"),
    path('dashboard/academic/promotion-formula/result/<int:result_id>/override/', evaluation_result_override, name="evaluation-result-override"),
    path('dashboard/academic/promotion-formula/result/<int:result_id>/promote/', promotion_result, name="promotion-result"),
    path('dashboard/academic/promotion-formula/promote/', bulk_promotion_results, name="bulk-promotion-results"),
    path('dashboard/academic/promotion-history/', promotion_history, name="promotion-history"),
    path('dashboard/telegram/', telegram_config, name="telegram-config"),
    path('dashboard/telegram/conversations/', telegram_conversations, name="telegram-conversations"),
    path('dashboard/telegram/conversations/<int:conversation_id>/', telegram_conversation_detail, name="telegram-conversation-detail"),
    path('dashboard/telegram/conversations/<int:conversation_id>/reply/', telegram_conversation_reply, name="telegram-conversation-reply"),
    path('dashboard/telegram/conversations/start/<int:user_id>/', telegram_start_conversation, name="telegram-start-conversation"),
    path('dashboard/telegram/attachments/<int:attachment_id>/', telegram_attachment, name="telegram-attachment"),
    path('dashboard/telegram/broadcasts/', telegram_broadcasts, name="telegram-broadcasts"),
    path('dashboard/telegram/broadcasts/new/', telegram_broadcast_create, name="telegram-broadcast-create"),
    path('dashboard/telegram/broadcasts/<int:broadcast_id>/confirm/', telegram_broadcast_confirm_page, name="telegram-broadcast-confirm-page"),
    path('dashboard/telegram/broadcasts/<int:broadcast_id>/confirm/send/', telegram_broadcast_confirm, name="telegram-broadcast-confirm"),
    path('dashboard/telegram/broadcasts/<int:broadcast_id>/cancel/', telegram_broadcast_cancel, name="telegram-broadcast-cancel"),
    path('dashboard/telegram/broadcasts/<int:broadcast_id>/retry/', telegram_broadcast_retry, name="telegram-broadcast-retry"),
    path('dashboard/telegram/broadcasts/<int:broadcast_id>/', telegram_broadcast_detail, name="telegram-broadcast-detail"),

    # Phase 3 — Student Applications
    path('signup/', signup, name="signup"),
    path('dashboard/applications/', applications_dashboard, name="applications-dashboard"),
    path('dashboard/applications/<int:user_id>/', application_review, name="application-review"),
    path('dashboard/applications/<int:user_id>/document/<str:document_type>/', application_document, name="application-document"),
    path('dashboard/applications/<int:user_id>/delete/', application_delete, name="application-delete"),
    path('dashboard/applications/<int:user_id>/<str:decision>/', application_decision, name="application-decision"),
    path('api/bulk-application-decision/', bulk_application_decision, name="bulk-application-decision"),

    # Phase 5 — Attendance Calendar, QR, and Scanning
    path('dashboard/calendar/', calendar_management, name="calendar-management"),
    path('dashboard/calendar/meeting/add/', add_meeting, name="add-meeting"),
    path('dashboard/calendar/meeting/<int:meeting_id>/delete/', delete_meeting, name="delete-meeting"),
    path('dashboard/calendar/holiday/add/', add_holiday, name="add-holiday"),
    path('dashboard/calendar/holiday/<int:holiday_id>/delete/', delete_holiday, name="delete-holiday"),
    path('my-calendar/', student_calendar, name="student-calendar"),
    path('my-qr/', download_qr, name="download-qr"),
    path('dashboard/users/<int:user_id>/regenerate-qr/', regenerate_qr, name="regenerate-qr"),
    path('scanner/', scanner, name="scanner"),
    path('scanner/lookup/', student_lookup, name="student-lookup"),
    path('scan/<str:token>/', scan_preview, name="scan-preview"),
    path('scan/<str:token>/<str:action>/', record_attendance, name="record-attendance"),
    path('dashboard/attendance/', attendance_management, name="attendance-management"),
    path('dashboard/attendance/<int:record_id>/correct/', attendance_correction, name="attendance-correction"),
    path('dashboard/attendance/<int:record_id>/delete/', delete_attendance, name="attendance-delete"),

    # Phase 6 — Online Lecture Progress Tracking
    path('api/offerings/<int:offering_id>/lesson/<int:lesson_id>/start-session/<int:file_index>/', start_viewing_session, name="start-viewing-session"),
    path('offerings/<int:offering_id>/lesson/<int:lesson_id>/manifest/<int:file_index>/', lesson_manifest, name="lesson-manifest"),
    path('api/worker/receipt/', worker_receipt, name="worker-receipt"),
    path('api/progress/heartbeat/', progress_heartbeat, name="progress-heartbeat"),
    path('dashboard/progress/', progress_dashboard, name="progress-dashboard"),

    # Phase 7 — Reports and Exports
    path('dashboard/reports/', report_dashboard, name="report-dashboard"),
    path('dashboard/reports/export-csv/', export_report_csv, name="export-report-csv"),
    path('dashboard/reports/export-xlsx/', export_report_xlsx, name="export-report-xlsx"),
    path('dashboard/academic/promotion-formula/export-xlsx/', export_evaluation_xlsx, name="export-evaluation-xlsx"),

    # Logout
    path('logout/', views.LogoutView.as_view(next_page='home'), name='logout'),

    # P8-T04 — SEO
    path('robots.txt', robots_txt, name="robots-txt"),
    path('sitemap.xml', sitemap_xml, name="sitemap-xml"),
]
