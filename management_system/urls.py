from django.urls import path
from management_system.views import *
from management_system.forms import UserLoginForm
# from django.contrib.auth import views

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
    path('lesson/<int:lesson_id>/<int:file_index>/', stream_lesson, name="lesson-stream"),
    path('lesson/<int:lesson_id>/audio/download/', generate_audio_download, name="audio-download"),
    # path('lesson/<int:lesson_id>/<str:segment_id>/', retrieve_segment, name="segment-stream"),
    # path('lesson/<int:lesson_id>/audio/<int:file_index>', stream_audio_lesson, name="lesson-audio-stream"),
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
    # Lesson Dashboard
    path('dashboard/lessons/', lesson_dashboard, name="lesson-dashboard"),
    path('dashboard/lessons/<int:lesson_id>/', index, name="lesson-view"),
    path('dashboard/lessons/create/', create_lesson, name="lesson-create"),
    path('dashboard/lessons/<int:lesson_id>/update/', update_lesson, name="lesson-update"),
    path('dashboard/lessons/<int:lesson_id>/delete/', DeleteLesson.as_view(), name="lesson-delete"),
    path('dashboard/folder/<str:folder_id>/', navigate_folder, name="navigate-folder"),

    # Quiz Dashboard
    path('dashboard/quizzez/', quiz_dashboard, name="quiz-dashboard"),
    path('dashboard/quizzez/<int:quiz_id>/', index, name="quiz-view"),
    path('dashboard/quizzez/create/', create_quiz, name="quiz-create"),
    path('dashboard/quizzez/<int:quiz_id>/update/', update_quiz, name="quiz-update"),
    path('dashboard/quizzez/<int:quiz_id>/delete/', DeleteQuiz.as_view(), name="quiz-delete"),

    # Submission Dashboard
    # path('dashboard/submission/', quiz_dashboard, name="submission-dashboard"),
    path('dashboard/quizzez/<int:quiz_id>/view-submission/', submission_dashboard, name="submission-dashboard"),
    path('dashboard/quizzez/<int:quiz_id>/view-submission/<int:user_id>/', submission_user, name="submission-user"),
    path('dashboard/quizzez/<int:quiz_id>/export-csv/', export_quiz_submissions_csv, name="export-quiz-submissions-csv"),
    path('dashboard/grade/<int:grade_id>/export-csv/', export_submission_csv, name="export-submission-csv"),

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

    # Test
    # path('test/', test, name="test"),
]