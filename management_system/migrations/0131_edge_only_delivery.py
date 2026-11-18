"""Remove the last non-edge delivery path.

Drops the worker receipt ledger, the per-session delivery mode and the mode
selector row. Receipt rows are discarded on purpose: the edge gate verifies
segments at Cloudflare, so the application no longer records them. Reverse
restores empty tables and a defaulted column, which is enough to run the
previous release again, but the discarded receipt history is not recoverable.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0130_phase08_direct_upload_bucket"),
    ]

    operations = [
        migrations.DeleteModel(name="VerifiedSegmentRequest"),
        migrations.RemoveField(
            model_name="viewingsession",
            name="segment_delivery_mode",
        ),
        migrations.DeleteModel(name="MediaDeliverySettings"),
    ]
