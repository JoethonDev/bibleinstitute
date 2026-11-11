"""Localized student PWA, Web Push and passkey routes."""

from django.urls import path

from . import pwa_views

urlpatterns = [
    path("offline-library/", pwa_views.offline_library, name="offline-library"),
    path("pwa/offline-shell/", pwa_views.pwa_offline_shell, name="pwa-offline-shell"),
    path(
        "pwa/media/save/<int:offering_id>/<int:lesson_id>/<int:file_index>/",
        pwa_views.pwa_save_media,
        name="pwa-save-media",
    ),
    path(
        "pwa/offline/validate/",
        pwa_views.pwa_validate_offline,
        name="pwa-validate-offline",
    ),
    path("pwa/push/subscribe/", pwa_views.web_push_subscribe, name="web-push-subscribe"),
    path(
        "pwa/push/unsubscribe/",
        pwa_views.web_push_unsubscribe,
        name="web-push-unsubscribe",
    ),
    path("pwa/push/status/", pwa_views.web_push_status, name="web-push-status"),
    path(
        "pwa/passkey/login/options/",
        pwa_views.passkey_login_options,
        name="passkey-login-options",
    ),
    path(
        "pwa/passkey/login/verify/",
        pwa_views.passkey_login_verify,
        name="passkey-login-verify",
    ),
    path(
        "pwa/passkey/register/options/",
        pwa_views.passkey_register_options,
        name="passkey-register-options",
    ),
    path(
        "pwa/passkey/register/verify/",
        pwa_views.passkey_register_verify,
        name="passkey-register-verify",
    ),
    path("pwa/passkey/revoke/", pwa_views.passkey_revoke, name="passkey-revoke"),
]
