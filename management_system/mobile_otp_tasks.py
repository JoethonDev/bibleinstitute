"""Celery delivery task for mobile Telegram OTPs."""

from __future__ import annotations

from logging import getLogger

import telebot
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from django.utils.translation import override

from .models import MobileOtpChallenge, TelegramAccount, TelegramBotConfig
from .telegram.configuration import decrypt_secret


logger = getLogger(__name__)


@shared_task(bind=True, acks_late=True, reject_on_worker_lost=True, max_retries=2)
def send_mobile_otp(self, challenge_id: str, encrypted_otp: str):
    now = timezone.now()
    with transaction.atomic():
        try:
            challenge = MobileOtpChallenge.objects.select_for_update().get(
                challenge_id=challenge_id
            )
            challenge = MobileOtpChallenge.objects.select_related(
                "student", "student__role"
            ).get(pk=challenge.pk)
        except MobileOtpChallenge.DoesNotExist:
            return
        if challenge.status not in {
            MobileOtpChallenge.Status.QUEUED,
            MobileOtpChallenge.Status.SENDING,
        } or challenge.expires_at <= now:
            if challenge.expires_at <= now and challenge.status in {
                MobileOtpChallenge.Status.QUEUED,
                MobileOtpChallenge.Status.SENDING,
            }:
                challenge.status = MobileOtpChallenge.Status.EXPIRED
                challenge.save(update_fields=["status"])
            return
        if (
            not challenge.student.is_active
            or challenge.student.application_status != "active"
        ):
            challenge.status = MobileOtpChallenge.Status.FAILED
            challenge.delivery_error = "Student is no longer eligible for mobile OTP."
            challenge.save(update_fields=["status", "delivery_error"])
            return
        if challenge.status == MobileOtpChallenge.Status.QUEUED:
            challenge.status = MobileOtpChallenge.Status.SENDING
            challenge.last_sent_at = now
            challenge.save(update_fields=["status", "last_sent_at"])
        account = TelegramAccount.objects.filter(user=challenge.student, is_active=True).first()
        config = TelegramBotConfig.objects.filter(is_active=True).only("token_ciphertext").first()
    if not account or not config or not config.token_ciphertext:
        MobileOtpChallenge.objects.filter(pk=challenge.pk).update(
            status=MobileOtpChallenge.Status.FAILED,
            delivery_error="No active Telegram account or bot configuration.",
        )
        return
    try:
        token = decrypt_secret(config.token_ciphertext)
        otp = decrypt_secret(encrypted_otp)
        bot = telebot.TeleBot(token, parse_mode=None, threaded=False)
        with override("ar"):
            message = bot.send_message(
                account.telegram_user_id,
                f"رمز الدخول إلى تطبيق المعهد: {otp}\nصلاحية الرمز خمس دقائق.",
            )
        MobileOtpChallenge.objects.filter(pk=challenge.pk, status=MobileOtpChallenge.Status.SENDING).update(
            status=MobileOtpChallenge.Status.SENT,
            telegram_message_id=getattr(message, "id", None),
        )
    except Exception:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=Exception("Telegram OTP delivery failed"), countdown=2 ** self.request.retries)
        MobileOtpChallenge.objects.filter(pk=challenge.pk).update(
            status=MobileOtpChallenge.Status.FAILED,
            delivery_error="Telegram OTP delivery failed.",
        )
        logger.warning(
            "Mobile OTP delivery failed challenge_id=%s error_code=delivery_error",
            challenge_id,
        )
