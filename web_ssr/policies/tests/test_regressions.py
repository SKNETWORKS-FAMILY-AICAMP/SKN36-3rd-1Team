"""Independent regression cases for issue #120; all policy payloads are sample_only."""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, SimpleTestCase, override_settings

from policies import services
from policies.converters import is_policy_id
from policies.tests.fixtures import make_item, make_page, patch_get, response, today


class IndependentRegressionTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @override_settings(SSR_CATALOG_CACHE_SECONDS=60)
    def test_concurrent_cold_requests_fetch_catalog_once(self):
        ready = Barrier(6)
        def reply(*args, **kwargs):
            time.sleep(0.05)
            return response(make_page([make_item()]))
        def request(_):
            ready.wait()
            return services.fetch_policy('POL_TEST_001').policy['policy_id']
        with patch('policies.services.requests.get', side_effect=reply) as get:
            with ThreadPoolExecutor(max_workers=6) as pool:
                self.assertEqual(list(pool.map(request, range(6))), ['POL_TEST_001'] * 6)
        self.assertEqual(get.call_count, 1)

    def test_unhashable_reception_status_is_unavailable_not_500(self):
        for status in ([], {}):
            with self.subTest(status=status), patch_get(response(make_page([make_item(reception_status=status)]))):
                with self.assertRaises(services.PolicyServiceUnavailable):
                    services.fetch_policy('POL_TEST_001')

    def test_policy_id_rejects_trailing_newline(self):
        self.assertFalse(is_policy_id('POL_010\n'))

    def test_stale_catalog_without_schedule_does_not_assert_open(self):
        old = (datetime.now(services.KST).date() - timedelta(days=1)).isoformat()
        with patch_get(response(make_page([make_item(application_schedule=None)], evaluated_on=old))):
            res = self.client.get('/policies/POL_TEST_001/')
        self.assertContains(res, '일정 재확인 필요')
        self.assertNotContains(res, 'data-testid="reception">접수 중')

    def test_csrf_enforced_post_is_rejected_before_view(self):
        with patch_get() as get:
            self.assertEqual(Client(enforce_csrf_checks=True).post('/policies/POL_TEST_001/').status_code, 403)
        get.assert_not_called()

    def test_duplicates_across_pages_are_unavailable(self):
        with patch_get(response(make_page([make_item('POL_A')], total=2, limit=1)),
                       response(make_page([make_item('POL_A')], total=2, offset=1, limit=1))):
            with self.assertRaises(services.PolicyServiceUnavailable):
                services.fetch_policy('POL_A')

    def test_over_500_policies_and_exact_match(self):
        rows = [make_item(f'POL_{i:04}') for i in range(501)]
        with patch_get(response(make_page(rows[:500], total=501)),
                       response(make_page(rows[500:], total=501, offset=500))) as get:
            self.assertEqual(services.fetch_policy('POL_0500').policy['policy_id'], 'POL_0500')
        self.assertEqual(get.call_count, 2)

    @override_settings(SSR_CATALOG_CACHE_SECONDS=60)
    def test_ttl_expiry_refetches(self):
        with patch_get(response(make_page([make_item()])), response(make_page([make_item()]))) as get:
            with patch('django.core.cache.backends.locmem.time.time', return_value=1000):
                services.fetch_policy('POL_TEST_001')
            with patch('django.core.cache.backends.locmem.time.time', return_value=1059):
                services.fetch_policy('POL_TEST_001')
            self.assertEqual(get.call_count, 1)
            with patch('django.core.cache.backends.locmem.time.time', return_value=1061):
                services.fetch_policy('POL_TEST_001')
        self.assertEqual(get.call_count, 2)

    @override_settings(SSR_CATALOG_CACHE_SECONDS=60)
    def test_kst_date_rollover_refetches(self):
        with patch_get(response(make_page([make_item()])), response(make_page([make_item()]))) as get:
            for moment in (datetime(2026, 11, 24, 23, 59, tzinfo=services.KST),
                           datetime(2026, 11, 25, 0, 0, tzinfo=services.KST)):
                with patch('policies.services.datetime', wraps=datetime) as clock:
                    clock.now.return_value = moment
                    services.fetch_policy('POL_TEST_001')
        self.assertEqual(get.call_count, 2)

    def test_upstream_redirects_are_not_followed(self):
        with patch_get(response(make_page([make_item()]))) as get:
            services.fetch_policy('POL_TEST_001')
        self.assertIs(get.call_args.kwargs.get('allow_redirects'), False)

    @override_settings(SSR_CATALOG_CACHE_SECONDS=60)
    def test_concurrent_failure_is_shared_and_next_request_retries(self):
        ready = Barrier(6)
        def failed(*args, **kwargs):
            time.sleep(0.08)
            raise services.requests.Timeout()
        def request(_):
            ready.wait()
            try:
                services.fetch_policy('POL_TEST_001')
            except services.PolicyServiceUnavailable:
                return 'unavailable'
        with patch('policies.services.requests.get', side_effect=failed) as get:
            with ThreadPoolExecutor(max_workers=6) as pool:
                self.assertEqual(list(pool.map(request, range(6))), ['unavailable'] * 6)
        self.assertEqual(get.call_count, 1)
        with patch_get(response(make_page([make_item()]))) as get:
            services.fetch_policy('POL_TEST_001')
        self.assertEqual(get.call_count, 1)

    def test_invalid_ids_and_manifest_in_other_rows_are_data_errors(self):
        for page in (make_page([make_item('not_a_policy')]),
                     make_page([make_item()], manifest_sha256='a' * 64 + '\n')):
            with self.subTest(page=page), patch_get(response(page)):
                with self.assertRaises(services.PolicyServiceUnavailable):
                    services.fetch_policy('POL_TEST_001')

    @override_settings(SSR_CATALOG_CACHE_SECONDS=60)
    def test_expired_failure_never_returns_stale_and_recovery_updates_manifest(self):
        from policies.tests.fixtures import TIMEOUT
        with patch_get(response(make_page([make_item()])), TIMEOUT,
                       response(make_page([make_item()], manifest_sha256='b' * 64))):
            with patch('django.core.cache.backends.locmem.time.time', return_value=1000):
                services.fetch_policy('POL_TEST_001')
            with patch('django.core.cache.backends.locmem.time.time', return_value=1061):
                with self.assertRaises(services.PolicyServiceUnavailable):
                    services.fetch_policy('POL_TEST_001')
                self.assertEqual(services.fetch_policy('POL_TEST_001').manifest_sha256, 'b' * 64)
