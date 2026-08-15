import csv
import io
import json
import re
from collections import defaultdict
from django.http import HttpResponse
from django.utils.translation import gettext as _
from django.utils import timezone
from ..models import User, Quiz, Submission, Question, Grade
from .timezones import format_application_datetime


def export_users_to_csv():
    """
    Export all users to CSV format.
    Returns HttpResponse with CSV data.
    """
    # Create CSV buffer
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header row
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
    writer.writerow(headers)
    
    # Write user data
    users = User.objects.all().order_by('joined_date')
    for user in users:
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
    
    # Create HTTP response
    output.seek(0)
    response = HttpResponse(output.read(), content_type='text/csv')
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


def export_quiz_with_submissions_to_csv(quiz_id):
    """
    Export quiz questions with choices and correct answers, plus all submissions.
    Returns HttpResponse with CSV data.
    """
    try:
        quiz = Quiz.objects.select_related('course_offering__course').get(id=quiz_id)
    except Quiz.DoesNotExist:
        # Return empty response if quiz doesn't exist
        response = HttpResponse('', content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="quiz_not_found.csv"'
        return response
    
    # Create CSV buffer
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write quiz header information
    writer.writerow([_('Quiz Export')])
    writer.writerow([_('Quiz Name'), quiz.name])
    writer.writerow([_('Course'), quiz.course_offering.course.name if quiz.course_offering_id else ''])
    writer.writerow([_('Opening Date'), format_application_datetime(quiz.opening_date, '%Y-%m-%d %H:%M:%S') if quiz.opening_date else ''])
    writer.writerow([_('Closing Date'), format_application_datetime(quiz.closing_date, '%Y-%m-%d %H:%M:%S') if quiz.closing_date else ''])
    writer.writerow([])  # Empty row separator
    
    # Write quiz questions section
    writer.writerow([_('QUIZ QUESTIONS AND ANSWERS')])
    questions = list(Question.objects.filter(quiz=quiz).order_by('id'))
    
    question_headers = [
        f"  {_('Question Number')}  ",
        f"  {_('Question Text')}  ",
        f"  {_('Question Type')}  ",
        f"  {_('Grade')}  ",
        f"  {_('Choices (for MCQ)')}  ",
        f"  {_('Correct Answer')}  "
    ]
    writer.writerow(question_headers)
    
    for idx, question in enumerate(questions, 1):
        writer.writerow([
            f"  {idx}  ",
            f"  {_csv_safe_cell(question.title)}  ",
            f"  {_csv_safe_cell(question.get_question_type_display())}  ",
            f"  {_csv_safe_cell(question.grade)}  ",
            f"  {_csv_safe_cell(_question_choices_text(question))}  ",
            f"  {_csv_safe_cell(_question_answer_text(question))}  "
        ])
    
    writer.writerow([])  # Empty row separator
    writer.writerow([])  # Empty row separator
    
    # Write submissions section
    writer.writerow([_('STUDENT SUBMISSIONS')])
    grades = Grade.objects.select_related('user').filter(quiz=quiz).order_by('user__username')
    submissions = Submission.objects.filter(question__quiz=quiz).select_related('question', 'user')
    submissions_by_user = _build_submission_map(submissions)
    
    if grades.exists():
        # Create dynamic headers for submissions
        submission_headers = [
            f"  {_('Student Username')}  ",
            f"  {_('Student Name')}  ",
            f"  {_('Submission Date')}  ",
            f"  {_('Total Grade')}  ",
            f"  {_('Maximum Grade')}  "
        ]
        
        # Add question columns
        for idx, question in enumerate(questions, 1):
            submission_headers.append(f"  {_('Q')}{idx}: {question.title[:30]}...  ")
            submission_headers.append(f"  {_('Q')}{idx} {_('Grade')}  ")
        
        writer.writerow(submission_headers)
        
        # Write submission data
        for grade in grades:
            row_data = [
                f"  {_csv_safe_cell(grade.user.username)}  ",
                f"  {_csv_safe_cell(grade.user.get_full_name())}  ",
                f"  {_csv_safe_cell(grade.submitted_at.strftime('%Y-%m-%d %H:%M:%S'))}  ",
                f"  {_csv_safe_cell(grade.total_grade)}  ",
                f"  {_csv_safe_cell(quiz.total_grade)}  "
            ]
            
            user_submissions = submissions_by_user.get(grade.user_id, {})

            for question in questions:
                answer_text = ''
                question_grade = 0
                
                user_submission = user_submissions.get(question.id)
                if user_submission:
                    answer_text = user_submission.submitted_answer
                    question_grade = user_submission.grade
                
                row_data.append(f"  {_csv_safe_cell(answer_text)}  ")
                row_data.append(f"  {_csv_safe_cell(question_grade)}  ")
            
            writer.writerow(row_data)
    else:
        writer.writerow([_('No submissions found for this quiz')])
    
    # Create HTTP response
    output.seek(0)
    response = HttpResponse(output.read(), content_type='text/csv')
    
    # Create safe filename
    safe_quiz_name = "".join(c for c in quiz.name if c.isalnum() or c in (' ', '-', '_')).rstrip()
    filename = f"quiz_{safe_quiz_name}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response


def export_quiz_summary_to_csv(quiz_id):
    """
    Export a concise quiz report with user identity and grade columns.
    """
    try:
        quiz = Quiz.objects.select_related('course_offering__course').get(id=quiz_id)
    except Quiz.DoesNotExist:
        response = HttpResponse('', content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="quiz_not_found.csv"'
        return response

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([_('Quiz Summary Export')])
    writer.writerow([_('Quiz Name'), quiz.name])
    writer.writerow([_('Course'), quiz.course_offering.course.name if quiz.course_offering_id else ''])
    writer.writerow([])

    headers = [
        _('User ID'),
        _('Username'),
        _('First Name'),
        _('Last Name'),
        _('Grade'),
        _('Total Grade'),
        _('Submitted At'),
    ]
    writer.writerow(headers)

    grades = Grade.objects.select_related('user').filter(quiz=quiz).order_by('user__username')
    for grade in grades:
        writer.writerow([
            _csv_safe_cell(grade.user_id),
            _csv_safe_cell(grade.user.username),
            _csv_safe_cell(grade.user.first_name),
            _csv_safe_cell(grade.user.last_name),
            _csv_safe_cell(grade.total_grade),
            _csv_safe_cell(quiz.total_grade),
            _csv_safe_cell(grade.submitted_at.strftime('%Y-%m-%d %H:%M:%S')),
        ])

    output.seek(0)
    response = HttpResponse(output.read(), content_type='text/csv')
    safe_quiz_name = _safe_filename(quiz.name)
    response['Content-Disposition'] = f'attachment; filename="quiz_summary_{safe_quiz_name}_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    return response


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
