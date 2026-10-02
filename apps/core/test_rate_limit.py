from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase

from apps.core.rate_limit import rate_limited


class AtomicRateLimitTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/", REMOTE_ADDR="192.0.2.10")
        self.request.user = AnonymousUser()

    @patch("apps.core.rate_limit.cache")
    def test_first_request_uses_atomic_add(self, cache):
        cache.add.return_value = True

        self.assertFalse(rate_limited(self.request, "test", limit=2, window=60))
        cache.add.assert_called_once()
        cache.incr.assert_not_called()

    @patch("apps.core.rate_limit.cache")
    def test_increment_over_limit_is_blocked(self, cache):
        cache.add.return_value = False
        cache.incr.return_value = 3

        self.assertTrue(rate_limited(self.request, "test", limit=2, window=60))

    @patch("apps.core.rate_limit.cache")
    def test_expired_key_is_recreated_without_non_atomic_set(self, cache):
        cache.add.side_effect = (False, True)
        cache.incr.side_effect = ValueError

        self.assertFalse(rate_limited(self.request, "test", limit=2, window=60))
        cache.set.assert_not_called()
