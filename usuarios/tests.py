import time
from datetime import date
from unittest.mock import patch

import pyotp
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Rol


class SessionActivityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="tecnico")
        role, _ = Rol.objects.get_or_create(nombre="usuario")
        cls.user.roles.add(role)
        cls.admin = get_user_model().objects.create_user(username="admin", is_staff=True)

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.csrf_token = "a" * 32
        self.activity_url = reverse("usuarios:session_activity")
        self.photo_status_url = reverse("operaciones:fotos_status_json", args=[999999])
        self.login_url = reverse("usuarios:login_unificado")
        self.start = int(time.time())
        middleware_clock = patch("usuarios.middleware.time")
        view_clock = patch("usuarios.views.time")
        self.middleware_clock = middleware_clock.start()
        self.view_clock = view_clock.start()
        self.addCleanup(middleware_clock.stop)
        self.addCleanup(view_clock.stop)
        self.set_time(self.start)

    def set_time(self, timestamp):
        self.middleware_clock.time.return_value = timestamp
        self.view_clock.time.return_value = timestamp

    def start_session(self, user, **values):
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        self.client.cookies[settings.CSRF_COOKIE_NAME] = self.csrf_token
        session = self.client.session
        session.update({"last_activity": self.start, "login_time": self.start, **values})
        session.save()

    def send_activity(self):
        return self.client.post(self.activity_url, HTTP_X_CSRFTOKEN=self.csrf_token)

    def test_activity_on_the_same_page_renews_the_six_hour_window(self):
        self.start_session(self.user)
        renewed_at = self.start + 6 * 3600 - 1
        self.set_time(renewed_at)
        self.assertEqual(self.send_activity().status_code, 204)
        self.assertEqual(self.client.session["last_activity"], renewed_at)

        self.set_time(renewed_at + 6 * 3600)
        self.assertRedirects(self.send_activity(), self.login_url, fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_photo_poll_does_not_save_or_renew_the_session(self):
        self.start_session(self.user)
        self.set_time(self.start + 6 * 3600 - 1)
        response = self.client.get(self.photo_status_url)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)
        self.assertEqual(self.client.session["last_activity"], self.start)

        self.set_time(self.start + 6 * 3600)
        response = self.client.get(self.photo_status_url)
        self.assertRedirects(response, self.login_url, fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_activity_requires_a_post_with_csrf(self):
        self.start_session(self.user)
        self.set_time(self.start + 60)
        self.assertEqual(self.client.get(self.activity_url).status_code, 405)
        self.assertRedirects(
            self.client.post(self.activity_url), self.login_url, fetch_redirect_response=False
        )
        self.assertEqual(self.client.session["last_activity"], self.start)

    def test_photo_upload_request_still_reaches_the_view_and_renews_activity(self):
        self.start_session(self.user)
        timestamp = self.start + 6 * 3600 - 1
        self.set_time(timestamp)
        upload_url = reverse("operaciones:fotos_upload_ajax", args=[999999])
        response = self.client.post(upload_url, HTTP_X_CSRFTOKEN=self.csrf_token)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.session["last_activity"], timestamp)

    @override_settings(TWO_FACTOR_ENFORCE_DATE=date(2020, 1, 1))
    def test_login_can_complete_two_factor_authentication(self):
        self.admin.set_password("test-password")
        self.admin.two_factor_enabled = True
        self.admin.two_factor_secret = pyotp.random_base32()
        self.admin.save()
        self.client.cookies[settings.CSRF_COOKIE_NAME] = self.csrf_token
        verify_url = reverse("usuarios:two_factor_verify")
        response = self.client.post(
            self.login_url,
            {"username": self.admin.username, "password": "test-password"},
            HTTP_X_CSRFTOKEN=self.csrf_token,
        )
        self.assertRedirects(response, verify_url, fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

        response = self.client.post(
            verify_url,
            {"code": pyotp.TOTP(self.admin.two_factor_secret).now()},
            HTTP_X_CSRFTOKEN=self.csrf_token,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.admin.pk))

    def test_admin_keeps_the_existing_timeout(self):
        self.start_session(self.admin)
        self.set_time(self.start + 15 * 60)
        self.assertEqual(self.send_activity().status_code, 204)
        self.assertEqual(self.client.session["last_activity"], self.start)
        self.set_time(self.start + 15 * 60 + 1)
        self.assertRedirects(self.send_activity(), self.login_url, fetch_redirect_response=False)

    def test_expired_admin_mode_cannot_switch_into_the_longer_window(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.start_session(self.user, ui_mode="admin")
        self.set_time(self.start + 15 * 60 + 1)
        self.assertRedirects(
            self.client.get(reverse("usuarios:switch_mode")),
            self.login_url,
            fetch_redirect_response=False,
        )
