from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0084_telegram_announcement_deliveries"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="identity_front_preview_key",
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="identity_back_preview_key",
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="payment_preview_key",
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="profile_image_preview_key",
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
        migrations.AddField(
            model_name="academicpayment",
            name="receipt_preview_key",
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
    ]
