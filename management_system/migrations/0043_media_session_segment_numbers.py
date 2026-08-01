import re

from django.db import migrations, models


SEGMENT_NUMBER_RE = re.compile(r"(?:^|[_-])(?P<number>\d+)\.ts$", re.IGNORECASE)


def backfill_segment_numbers(apps, schema_editor):
    VerifiedSegmentRequest = apps.get_model("management_system", "VerifiedSegmentRequest")
    seen = set()
    updates = []
    for receipt in VerifiedSegmentRequest.objects.order_by("pk").iterator():
        match = SEGMENT_NUMBER_RE.search(receipt.segment_key.rsplit("/", 1)[-1])
        if not match:
            raise RuntimeError(
                "Cannot derive a segment number from VerifiedSegmentRequest "
                f"{receipt.pk}: {receipt.segment_key}"
            )
        segment_number = int(match.group("number"))
        identity = (receipt.session_id, segment_number)
        if identity in seen:
            raise RuntimeError(
                "Duplicate segment number for one viewing session while applying "
                f"0043: session={receipt.session_id}, segment={segment_number}"
            )
        seen.add(identity)
        receipt.segment_number = segment_number
        updates.append(receipt)

    if updates:
        VerifiedSegmentRequest.objects.bulk_update(updates, ["segment_number"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0042_seed_quiz_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="verifiedsegmentrequest",
            name="segment_number",
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.RunPython(backfill_segment_numbers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="verifiedsegmentrequest",
            name="segment_number",
            field=models.PositiveIntegerField(),
        ),
        migrations.AlterUniqueTogether(
            name="verifiedsegmentrequest",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="verifiedsegmentrequest",
            constraint=models.UniqueConstraint(
                fields=("session", "segment_number"),
                name="unique_verified_segment_number_per_session",
            ),
        ),
    ]
