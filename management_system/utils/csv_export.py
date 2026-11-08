import csv
import io
import json
import re
from collections import defaultdict
from tempfile import SpooledTemporaryFile

from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils import translation
from django.utils.translation import gettext as _
from ..models import AdminOperationJob, Grade, Question, Quiz, Submission, User
from .timezones import format_application_datetime
from ..admin_operations import (
    AdminOperationError,
    assert_admin_operation_progress,
    create_admin_operation,
    finalize_admin_operation_export,
    require_admin_operation_actor,
)
from ..utils.decorators import can_grade



def export_users_to_csv():
    """
    Stream every user as CSV.

    The row iterator keeps one page of users in memory, and the single
    select_related removes the per-row role lookup the old loop performed.
    """
    headers = [
        f"  {_('Username')}  ",
        f"  {_('First Name')}  ",
        f"  {_('Last Name')}  ",
        f"  {_('Email')}  ",
        f"  {_('Role')}  ",
        f"  {_('Joined Date')}  ",
        f"  {_('Last Login')}  ",
        f"  {_('Is Active')}  "
    ]

    def csv_rows():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        yield output.getvalue()
        users = (
            User.objects.select_related("role")
            .order_by("joined_date", "pk")
            .iterator(chunk_size=500)
        )
        for user in users:
            output.seek(0)
            output.truncate(0)
            writer.writerow([
                f"  {_csv_safe_cell(user.username)}  ",
                f"  {_csv_safe_cell(user.first_name)}  ",
                f"  {_csv_safe_cell(user.last_name)}  ",
                f"  {_csv_safe_cell(user.email)}  ",
                f"  {_csv_safe_cell(user.role.get_role_display() if user.role else '')}  ",
                f"  {_csv_safe_cell(user.joined_date.strftime('%Y-%m-%d %H:%M:%S') if user.joined_date else '')}  ",
                f"  {_csv_safe_cell(user.last_login.strftime('%Y-%m-%d %H:%M:%S') if user.last_login else '')}  ",
                f"  {_csv_safe_cell(_('Yes') if user.is_active else _('No'))}  "
            ])
            yield output.getvalue()

    response = StreamingHttpResponse(csv_rows(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="users_export_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    return response


def _safe_filename(value: str) -> str:
    return "".join(c for c in value if c.isalnum() or c in (' ', '-', '_')).rstrip()


def _csv_safe_cell(value):
    if value is None:
        return ''

    text = str(value)
    if re.match(r'^[\s]*[=+\-@]', text):
        return f"'{text}"

    return text


def _question_choices_text(question: Question) -> str:
    if question.question_type == 'mcq' and question.choices:
        try:
            choices_list = json.loads(question.choices)
            return ' | '.join(choices_list)
        except (json.JSONDecodeError, TypeError):
            return question.choices

    if question.question_type == 'order_events':
        return ' | '.join(question.get_choices_list())

    if question.question_type == 'match_related':
        pairs = question.get_config().get('pairs', [])
        return ' | '.join(
            f"{pair.get('left', '')}={pair.get('right', '')}"
            for pair in pairs
            if isinstance(pair, dict)
        )

    return ''


def _question_answer_text(question: Question) -> str:
    payload = question.get_correct_answer_payload()
    if isinstance(payload, (dict, list)):
        return json.dumps(payload, ensure_ascii=False)
    return payload or ''


def _build_submission_map(submissions):
    grouped = defaultdict(dict)
    for submission in submissions:
        grouped[submission.user_id][submission.question_id] = submission
    return grouped


def _grade_batches(quiz, batch_size: int = 500):
    """Yield bounded grade batches in username order without materializing all."""

    grades = Grade.objects.select_related("user").filter(quiz=quiz).order_by(
        "user__username", "pk"
    ).iterator(chunk_size=batch_size)
    batch = []
    for grade in grades:
        batch.append(grade)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _write_csv_row(spool, row) -> None:
    output = io.StringIO()
    csv.writer(output).writerow(row)
    spool.write(output.getvalue().encode("utf-8"))


def _new_csv_spool():
    return SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")


def _quiz_export_filename(quiz, prefix: str) -> str:
    safe_quiz_name = _safe_filename(quiz.name)
    return f"{prefix}_{safe_quiz_name}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"


def build_quiz_detail_csv(quiz, progress=None):
    """Write the detailed quiz/submission CSV to a bounded spooled file."""
    questions = list(Question.objects.filter(quiz=quiz).order_by("id"))
    total = Grade.objects.filter(quiz=quiz).count() if progress is not None else 0
    processed = 0
    spool = _new_csv_spool()

    _write_csv_row(spool, [_("Quiz Export")])
    _write_csv_row(spool, [_("Quiz Name"), quiz.name])
    _write_csv_row(spool, [_("Course"), quiz.course_offering.course.name if quiz.course_offering_id else ""])
    _write_csv_row(spool, [_("Opening Date"), format_application_datetime(quiz.opening_date, "%Y-%m-%d %H:%M:%S") if quiz.opening_date else ""])
    _write_csv_row(spool, [_("Closing Date"), format_application_datetime(quiz.closing_date, "%Y-%m-%d %H:%M:%S") if quiz.closing_date else ""])
    _write_csv_row(spool, [])
    _write_csv_row(spool, [_("QUIZ QUESTIONS AND ANSWERS")])
    _write_csv_row(spool, [
        f"  {_('Question Number')}  ",
        f"  {_('Question Text')}  ",
        f"  {_('Question Type')}  ",
        f"  {_('Grade')}  ",
        f"  {_('Choices (for MCQ)')}  ",
        f"  {_('Correct Answer')}  ",
    ])
    for idx, question in enumerate(questions, 1):
        _write_csv_row(spool, [
            f"  {idx}  ",
            f"  {_csv_safe_cell(question.title)}  ",
            f"  {_csv_safe_cell(question.get_question_type_display())}  ",
            f"  {_csv_safe_cell(question.grade)}  ",
            f"  {_csv_safe_cell(_question_choices_text(question))}  ",
            f"  {_csv_safe_cell(_question_answer_text(question))}  ",
        ])
    _write_csv_row(spool, [])
    _write_csv_row(spool, [])
    _write_csv_row(spool, [_("STUDENT SUBMISSIONS")])

    submission_headers = [
        f"  {_('Student Username')}  ",
        f"  {_('Student Name')}  ",
        f"  {_('Submission Date')}  ",
        f"  {_('Total Grade')}  ",
        f"  {_('Maximum Grade')}  ",
    ]
    for idx, question in enumerate(questions, 1):
        submission_headers.append(f"  {_('Q')}{idx}: {question.title[:30]}...  ")
        submission_headers.append(f"  {_('Q')}{idx} {_('Grade')}  ")
    _write_csv_row(spool, submission_headers)

    wrote_row = False
    for grade_batch in _grade_batches(quiz):
        submissions = Submission.objects.filter(
            question__quiz=quiz,
            user_id__in=[grade.user_id for grade in grade_batch],
        ).only("user_id", "question_id", "submitted_answer", "grade")
        submissions_by_user = _build_submission_map(submissions)
        for grade in grade_batch:
            wrote_row = True
            row_data = [
                f"  {_csv_safe_cell(grade.user.username)}  ",
                f"  {_csv_safe_cell(grade.user.get_full_name())}  ",
                f"  {_csv_safe_cell(grade.submitted_at.strftime('%Y-%m-%d %H:%M:%S'))}  ",
                f"  {_csv_safe_cell(grade.total_grade)}  ",
                f"  {_csv_safe_cell(quiz.total_grade)}  ",
            ]
            user_submissions = submissions_by_user.get(grade.user_id, {})
            for question in questions:
                user_submission = user_submissions.get(question.id)
                answer_text = user_submission.submitted_answer if user_submission else ""
                question_grade = user_submission.grade if user_submission else 0
                row_data.append(f"  {_csv_safe_cell(answer_text)}  ")
                row_data.append(f"  {_csv_safe_cell(question_grade)}  ")
            _write_csv_row(spool, row_data)
        processed += len(grade_batch)
        if progress is not None:
            progress(processed=processed, total=total)
    if not wrote_row:
        _write_csv_row(spool, [_("No submissions found for this quiz")])
    spool.seek(0)
    return spool


def build_quiz_summary_csv(quiz, progress=None):
    """Write the summary quiz CSV to a bounded spooled file."""
    total = Grade.objects.filter(quiz=quiz).count() if progress is not None else 0
    processed = 0
    spool = _new_csv_spool()
    _write_csv_row(spool, [_("Quiz Summary Export")])
    _write_csv_row(spool, [_("Quiz Name"), quiz.name])
    _write_csv_row(spool, [_("Course"), quiz.course_offering.course.name if quiz.course_offering_id else ""])
    _write_csv_row(spool, [])
    _write_csv_row(spool, [
        _("User ID"),
        _("Username"),
        _("First Name"),
        _("Last Name"),
        _("Grade"),
        _("Total Grade"),
        _("Submitted At"),
    ])
    for grade_batch in _grade_batches(quiz):
        for grade in grade_batch:
            _write_csv_row(spool, [
                _csv_safe_cell(grade.user_id),
                _csv_safe_cell(grade.user.username),
                _csv_safe_cell(grade.user.first_name),
                _csv_safe_cell(grade.user.last_name),
                _csv_safe_cell(grade.total_grade),
                _csv_safe_cell(quiz.total_grade),
                _csv_safe_cell(grade.submitted_at.strftime("%Y-%m-%d %H:%M:%S")),
            ])
        processed += len(grade_batch)
        if progress is not None:
            progress(processed=processed, total=total)
    spool.seek(0)
    return spool


def enqueue_quiz_export(*, actor, quiz, kind, language="ar"):
    """Create or reuse one durable whole-population quiz export job."""

    if kind not in {
        AdminOperationJob.Kind.QUIZ_DETAIL_EXPORT,
        AdminOperationJob.Kind.QUIZ_SUMMARY_EXPORT,
    }:
        raise ValueError("Unsupported quiz export kind.")
    prefix = "quiz" if kind == AdminOperationJob.Kind.QUIZ_DETAIL_EXPORT else "quiz_summary"
    total = Grade.objects.filter(quiz=quiz).count()
    return create_admin_operation(
        actor=actor,
        kind=kind,
        input={"quiz_id": quiz.pk},
        conflict_key=f"{prefix}-export:{quiz.pk}",
        offering=quiz.course_offering,
        language=language,
        total=total,
    )


def execute_quiz_export(job, token) -> dict:
    """J01 executor: build one private CSV artifact and publish it on completion."""



    require_admin_operation_actor(job)
    if not can_grade(job.actor):
        raise AdminOperationError(
            "not_permitted",
            _("You are no longer allowed to export quiz submissions."),
        )
    quiz_id = int((job.input or {}).get("quiz_id") or 0)
    quiz = Quiz.objects.select_related("course_offering__course").filter(pk=quiz_id).first()
    if quiz is None:
        raise AdminOperationError("quiz_missing", _("The quiz no longer exists."))

    def progress(*, processed, total):
        assert_admin_operation_progress(
            job.pk,
            token,
            processed=processed,
            succeeded=processed,
            failed=0,
            skipped=0,
            total=total,
        )

    with translation.override(job.language or "ar"):
        if job.kind == AdminOperationJob.Kind.QUIZ_SUMMARY_EXPORT:
            file_obj = build_quiz_summary_csv(quiz, progress=progress)
            filename = _quiz_export_filename(quiz, "quiz_summary")
        else:
            file_obj = build_quiz_detail_csv(quiz, progress=progress)
            filename = _quiz_export_filename(quiz, "quiz")
        finalize_admin_operation_export(
            job, token, file_obj, filename, content_type="text/csv"
        )
    return {
        "status": AdminOperationJob.Status.COMPLETED,
        "result": {
            "filename": filename,
            "content_type": "text/csv",
        },
    }


def export_single_submission_to_csv(grade_id):
    """
    Export a single submission with all details.
    Returns HttpResponse with CSV data.
    """
    try:
        grade = Grade.objects.get(id=grade_id)
    except Grade.DoesNotExist:
        # Return empty response if grade doesn't exist
        response = HttpResponse('', content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="submission_not_found.csv"'
        return response
    
    quiz = grade.quiz
    
    # Create CSV buffer
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write submission header information
    writer.writerow([_('Individual Submission Export')])
    writer.writerow([_('Student'), grade.user.get_full_name()])
    writer.writerow([_('Username'), grade.user.username])
    writer.writerow([_('Quiz Name'), quiz.name])
    writer.writerow([_('Course'), quiz.course_offering.course.name if quiz.course_offering_id else ''])
    writer.writerow([_('Submission Date'), grade.submitted_at.strftime('%Y-%m-%d %H:%M:%S')])
    writer.writerow([_('Total Grade'), f"({grade.total_grade}/{quiz.total_grade}) Grade"])
    writer.writerow([])  # Empty row separator
    
    # Write detailed answers
    writer.writerow([_('DETAILED ANSWERS')])
    
    headers = [
        f"  {_('Question Number')}  ",
        f"  {_('Question Text')}  ",
        f"  {_('Question Type')}  ",
        f"  {_('Student Answer')}  ",
        f"  {_('Correct Answer')}  ",
        f"  {_('Question Grade')}  ",
        f"  {_('Earned Grade')}  "
    ]
    writer.writerow(headers)
    
    questions = Question.objects.filter(quiz=quiz).order_by('id')
    
    # Get all submissions for this user and quiz
    user_submissions = Submission.objects.filter(
        user=grade.user, 
        question__quiz=quiz
    ).select_related('question')
    
    for idx, question in enumerate(questions, 1):
        # Find student's answer
        student_answer = ''
        earned_grade = 0
        
        user_submission = user_submissions.filter(question=question).first()
        if user_submission:
            student_answer = user_submission.submitted_answer
            earned_grade = user_submission.grade
        
        writer.writerow([
            f"  {idx}  ",
            f"  {question.title}  ",
            f"  {question.get_question_type_display()}  ",
            f"  {student_answer}  ",
            f"  {question.correct_answer or ''}  ",
            f"  {question.grade}  ",
            f"  {earned_grade}  "
        ])
    
    # Create HTTP response
    output.seek(0)
    response = HttpResponse(output.read(), content_type='text/csv')
    
    # Create safe filename
    safe_username = "".join(c for c in grade.user.username if c.isalnum() or c in (' ', '-', '_')).rstrip()
    safe_quiz_name = "".join(c for c in quiz.name if c.isalnum() or c in (' ', '-', '_')).rstrip()
    filename = f"submission_{safe_username}_{safe_quiz_name}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response
