from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import migrations

from management_system.utils.search import SearchNormalize


FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION lms_arabic_search_normalize(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT lower(
        regexp_replace(
            translate(
                replace(replace(replace(replace(replace(replace(replace(
                    coalesce(value, ''),
                    'أ', 'ا'),
                    'إ', 'ا'),
                    'آ', 'ا'),
                    'ٱ', 'ا'),
                    'ى', 'ي'),
                    'ئ', 'ي'),
                    'ؤ', 'و'),
                'ًٌٍَُِّْـٰ',
                ''
            ),
            '[[:space:]]+',
            ' ',
            'g'
        )
    )
$$;
"""


# Semantic name fields only. Stored values are not rewritten by this
# migration; the function and indexes affect search comparisons only.
SEARCH_INDEXES = (
    ("user", "management_system_user", "username", "user_username_trgm_idx"),
    ("user", "management_system_user", "email", "user_email_trgm_idx"),
    ("user", "management_system_user", "first_name", "user_first_name_trgm_idx"),
    ("user", "management_system_user", "last_name", "user_last_name_trgm_idx"),
    ("user", "management_system_user", "priest_name", "user_priest_name_trgm_idx"),
    ("user", "management_system_user", "phone", "user_phone_trgm_idx"),
    ("user", "management_system_user", "identity_number", "user_identity_trgm_idx"),
    ("telegrambroadcastrecipient", "management_system_telegrambroadcastrecipient", "user_display_name", "tg_recipient_name_trgm_idx"),
    ("offlinecity", "management_system_offlinecity", "name", "offline_city_name_trgm_idx"),
    ("level", "management_system_level", "name_en", "level_name_en_trgm_idx"),
    ("level", "management_system_level", "name_ar", "level_name_ar_trgm_idx"),
    ("course", "management_system_course", "name", "course_name_trgm_idx"),
    ("course", "management_system_course", "instructor", "course_instr_trgm_idx"),
    ("academicyear", "management_system_academicyear", "name", "academic_year_name_trgm_idx"),
    ("courseoffering", "management_system_courseoffering", "instructor", "offering_instr_trgm_idx"),
    ("quiztype", "management_system_quiztype", "name_en", "quiz_type_name_en_idx"),
    ("quiztype", "management_system_quiztype", "name_ar", "quiz_type_name_ar_idx"),
    ("historicalacademicsummary", "management_system_historicalacademicsummary", "source_name", "hist_source_name_trgm_idx"),
    ("academicholiday", "management_system_academicholiday", "name", "holiday_name_trgm_idx"),
    ("lesson", "management_system_lesson", "name", "lesson_name_trgm_idx"),
    ("quiz", "management_system_quiz", "name", "quiz_name_trgm_idx"),
)

USER_INDEXES = SEARCH_INDEXES[:7]


def _drop_old_user_indexes_sql():
    return "\n".join(
        f'DROP INDEX IF EXISTS "{index_name}";'
        for _, _, _, index_name in USER_INDEXES
        if index_name != "user_priest_name_trgm_idx"
    )


def _restore_old_user_indexes_sql():
    return "\n".join(
        f'CREATE INDEX "{index_name}" ON "management_system_user" '
        f'USING gin ((UPPER("{field_name}")) gin_trgm_ops);'
        for _, _, field_name, index_name in USER_INDEXES
        if index_name != "user_priest_name_trgm_idx"
    )


def _create_index_sql(table_name, field_name, index_name):
    return (
        f'CREATE INDEX "{index_name}" ON "{table_name}" '
        f'USING gin ((lms_arabic_search_normalize("{field_name}")) gin_trgm_ops);'
    )


def _drop_index_sql(index_name):
    return f'DROP INDEX IF EXISTS "{index_name}";'


operations = [
    migrations.RunSQL(
        sql=FUNCTION_SQL,
        reverse_sql="DROP FUNCTION IF EXISTS lms_arabic_search_normalize(text);",
    ),
    migrations.SeparateDatabaseAndState(
        database_operations=[migrations.RunSQL(
            sql=_drop_old_user_indexes_sql(),
            reverse_sql=_restore_old_user_indexes_sql(),
        )],
        state_operations=[
            migrations.RemoveIndex(model_name="user", name=index_name)
            for _, _, _, index_name in USER_INDEXES
            if index_name != "user_priest_name_trgm_idx"
        ],
    ),
]

for model_name, table_name, field_name, index_name in SEARCH_INDEXES:
    operations.append(
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql=_create_index_sql(table_name, field_name, index_name),
                reverse_sql=_drop_index_sql(index_name),
            )],
            state_operations=[migrations.AddIndex(
                model_name=model_name,
                index=GinIndex(
                    OpClass(SearchNormalize(field_name), name="gin_trgm_ops"),
                    name=index_name,
                ),
            )],
        )
    )


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0074_user_application_search_indexes"),
    ]

    operations = operations
