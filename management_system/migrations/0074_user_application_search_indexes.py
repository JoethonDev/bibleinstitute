from django.contrib.postgres.operations import TrigramExtension
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0073_alter_user_options_user_user_app_status_date_idx"),
    ]

    operations = [
        TrigramExtension(),
        migrations.AddIndex(
            model_name="user",
            index=models.Index(
                models.Case(
                    models.When(application_status="pending", then=models.Value(0)),
                    default=models.Value(1),
                    output_field=models.IntegerField(),
                ),
                models.F("date_joined").desc(),
                models.F("id").desc(),
                name="user_app_order_idx",
            ),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='CREATE INDEX "user_username_trgm_idx" ON "management_system_user" USING gin ((UPPER("username")) gin_trgm_ops);',
                reverse_sql='DROP INDEX IF EXISTS "user_username_trgm_idx";',
            )],
            state_operations=[migrations.AddIndex(
                model_name="user",
                index=GinIndex(
                    OpClass(models.functions.Upper("username"), name="gin_trgm_ops"),
                    name="user_username_trgm_idx",
                ),
            )],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='CREATE INDEX "user_email_trgm_idx" ON "management_system_user" USING gin ((UPPER("email")) gin_trgm_ops);',
                reverse_sql='DROP INDEX IF EXISTS "user_email_trgm_idx";',
            )],
            state_operations=[migrations.AddIndex(
                model_name="user",
                index=GinIndex(
                    OpClass(models.functions.Upper("email"), name="gin_trgm_ops"),
                    name="user_email_trgm_idx",
                ),
            )],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='CREATE INDEX "user_first_name_trgm_idx" ON "management_system_user" USING gin ((UPPER("first_name")) gin_trgm_ops);',
                reverse_sql='DROP INDEX IF EXISTS "user_first_name_trgm_idx";',
            )],
            state_operations=[migrations.AddIndex(
                model_name="user",
                index=GinIndex(
                    OpClass(models.functions.Upper("first_name"), name="gin_trgm_ops"),
                    name="user_first_name_trgm_idx",
                ),
            )],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='CREATE INDEX "user_last_name_trgm_idx" ON "management_system_user" USING gin ((UPPER("last_name")) gin_trgm_ops);',
                reverse_sql='DROP INDEX IF EXISTS "user_last_name_trgm_idx";',
            )],
            state_operations=[migrations.AddIndex(
                model_name="user",
                index=GinIndex(
                    OpClass(models.functions.Upper("last_name"), name="gin_trgm_ops"),
                    name="user_last_name_trgm_idx",
                ),
            )],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='CREATE INDEX "user_phone_trgm_idx" ON "management_system_user" USING gin ((UPPER("phone")) gin_trgm_ops);',
                reverse_sql='DROP INDEX IF EXISTS "user_phone_trgm_idx";',
            )],
            state_operations=[migrations.AddIndex(
                model_name="user",
                index=GinIndex(
                    OpClass(models.functions.Upper("phone"), name="gin_trgm_ops"),
                    name="user_phone_trgm_idx",
                ),
            )],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='CREATE INDEX "user_identity_trgm_idx" ON "management_system_user" USING gin ((UPPER("identity_number")) gin_trgm_ops);',
                reverse_sql='DROP INDEX IF EXISTS "user_identity_trgm_idx";',
            )],
            state_operations=[migrations.AddIndex(
                model_name="user",
                index=GinIndex(
                    OpClass(models.functions.Upper("identity_number"), name="gin_trgm_ops"),
                    name="user_identity_trgm_idx",
                ),
            )],
        ),
    ]
