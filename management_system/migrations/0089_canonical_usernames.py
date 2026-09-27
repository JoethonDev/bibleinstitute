import unicodedata

from django.core.exceptions import ValidationError
from django.db import migrations, models
from django.db.models import Q
from django.db.models.functions import Lower, Trim

import management_system.managers


BATCH_SIZE = 500


def _canonical_username(value):
    return unicodedata.normalize("NFKC", value).strip().lower()


def normalize_existing_usernames(apps, schema_editor):
    User = apps.get_model("management_system", "User")
    database = schema_editor.connection.alias
    users = User.objects.using(database)
    username_field = User._meta.get_field("username")
    if schema_editor.connection.vendor == "postgresql":
        table_name = schema_editor.quote_name(User._meta.db_table)
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(f"LOCK TABLE {table_name} IN SHARE ROW EXCLUSIVE MODE")
    seen = {}
    collisions = []
    invalid = []

    # Complete the preflight before any UPDATE so ambiguity cannot partially
    # rewrite stored identities.
    for user_id, original in users.order_by("pk").values_list("pk", "username").iterator(
        chunk_size=BATCH_SIZE
    ):
        canonical = _canonical_username(original)
        prior_id = seen.get(canonical)
        if prior_id is not None:
            collisions.append((canonical, prior_id, user_id))
        else:
            seen[canonical] = user_id
        try:
            username_field.clean(canonical, None)
        except ValidationError:
            invalid.append(user_id)

    if collisions or invalid:
        details = []
        if collisions:
            sample = ", ".join(
                f"{canonical!r} (user IDs {first_id}, {second_id})"
                for canonical, first_id, second_id in collisions[:10]
            )
            details.append(
                f"{len(collisions)} canonical username collision(s): {sample}"
            )
        if invalid:
            details.append(
                f"{len(invalid)} normalized username(s) fail username validation "
                f"(user IDs {', '.join(map(str, invalid[:20]))})"
            )
        raise RuntimeError(
            "Username normalization stopped before changing any rows; "
            + "; ".join(details)
        )

    batch = []
    for user_id, original in users.order_by("pk").values_list("pk", "username").iterator(
        chunk_size=BATCH_SIZE
    ):
        canonical = _canonical_username(original)
        if canonical != original:
            user = User(pk=user_id, username=canonical)
            batch.append(user)
        if len(batch) >= BATCH_SIZE:
            users.bulk_update(batch, ["username"], batch_size=BATCH_SIZE)
            batch.clear()
    if batch:
        users.bulk_update(batch, ["username"], batch_size=BATCH_SIZE)


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0088_backfill_missing_lesson_part_ids"),
    ]

    operations = [
        migrations.AlterModelManagers(
            name="user",
            managers=[("objects", management_system.managers.CanonicalUserManager())],
        ),
        migrations.RunPython(normalize_existing_usernames),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=Q(username=Lower(Trim("username"))),
                name="user_username_canonical",
            ),
        ),
    ]
