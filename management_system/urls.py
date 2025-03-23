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
    # path('lesson/<int:lesson_id>/<str:segment_id>/', retrieve_segment, name="segment-stream"),
    # path('lesson/<int:lesson_id>/audio/<int:file_index>', stream_audio_lesson, name="lesson-audio-stream"),
    # Admin Routes
    path('dashboard/', admin_panel, name="admin-panel"),
    # Users Dashboard
    path('dashboard/users/', user_dashboard, name="user-dashboard"),
    path('dashboard/users/create/', CreateUser.as_view(), name="user-create"),
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

    # Upload Videos
    path('dashboard/upload-videos/', upload_videos, name="upload-videos"),
    path('api/get-presigned-url/', upload_link, name="upload-link"),

    # Test
    # path('test/', test, name="test"),
]