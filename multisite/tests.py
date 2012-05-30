import warnings

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.test import TestCase
from django.test.client import RequestFactory as DjangoRequestFactory
from django.utils.unittest import skipUnless

try:
    from django.test.utils import override_settings
except ImportError:
    from override_settings import override_settings

from . import SiteDomain, SiteID, threadlocals
from .middleware import DynamicSiteMiddleware
from .threadlocals import SiteIDHook


class RequestFactory(DjangoRequestFactory):
    def __init__(self, host):
        super(RequestFactory, self).__init__()
        self.host = host

    def get(self, path, data={}, host=None, **extra):
        if host is None:
            host = self.host
        return super(RequestFactory, self).get(path=path, data=data,
                                               HTTP_HOST=host, **extra)


@skipUnless(Site._meta.installed,
            'django.contrib.sites is not in settings.INSTALLED_APPS')
@override_settings(SITE_ID=SiteID())
class TestContribSite(TestCase):
    def setUp(self):
        Site.objects.all().delete()
        self.site = Site.objects.create(domain='example.com')
        settings.SITE_ID.set(self.site.id)

    def test_get_current_site(self):
        current_site = Site.objects.get_current()
        self.assertEqual(current_site, self.site)
        self.assertEqual(current_site.id, settings.SITE_ID)


@skipUnless(Site._meta.installed,
            'django.contrib.sites is not in settings.INSTALLED_APPS')
@override_settings(
    SITE_ID=SiteID(default=0),
    CACHE_MULTISITE_ALIAS='django.core.cache.backends.dummy.DummyCache',
    MULTISITE_FALLBACK=None,
)
class DynamicSiteMiddlewareTest(TestCase):
    def setUp(self):
        self.host = 'example.com'
        self.factory = RequestFactory(host=self.host)

        Site.objects.all().delete()
        self.site = Site.objects.create(domain=self.host)

        self.middleware = DynamicSiteMiddleware()

    def tearDown(self):
        settings.SITE_ID.reset()

    def test_valid_domain(self):
        # Make the request
        request = self.factory.get('/')
        self.assertEqual(self.middleware.process_request(request), None)
        self.assertEqual(settings.SITE_ID, self.site.pk)
        # Request again
        self.assertEqual(self.middleware.process_request(request), None)
        self.assertEqual(settings.SITE_ID, self.site.pk)

    def test_valid_domain_port(self):
        # Make the request with a specific port
        request = self.factory.get('/', host=self.host + ':8000')
        self.assertEqual(self.middleware.process_request(request), None)
        self.assertEqual(settings.SITE_ID, self.site.pk)
        # Request again
        self.assertEqual(self.middleware.process_request(request), None)
        self.assertEqual(settings.SITE_ID, self.site.pk)

    def test_case_sensitivity(self):
        # Make the request in all uppercase
        request = self.factory.get('/', host=self.host.upper())
        self.assertEqual(self.middleware.process_request(request), None)
        self.assertEqual(settings.SITE_ID, self.site.pk)

    def test_change_domain(self):
        # Make the request
        request = self.factory.get('/')
        self.assertEqual(self.middleware.process_request(request), None)
        self.assertEqual(settings.SITE_ID, self.site.pk)
        # Another request with a different site
        site2 = Site.objects.create(domain='anothersite.example')
        request = self.factory.get('/', host=site2.domain)
        self.assertEqual(self.middleware.process_request(request), None)
        self.assertEqual(settings.SITE_ID, site2.pk)

    def test_invalid_domain(self):
        # Make the request
        request = self.factory.get('/', host='invalid')
        self.assertRaises(Http404,
                          self.middleware.process_request, request)
        self.assertEqual(settings.SITE_ID, 0)

    def test_invalid_domain_port(self):
        # Make the request
        request = self.factory.get('/', host=':8000')
        self.assertRaises(Http404,
                          self.middleware.process_request, request)
        self.assertEqual(settings.SITE_ID, 0)

    def test_no_sites(self):
        # Remove all Sites
        Site.objects.all().delete()
        # Make the request
        request = self.factory.get('/')
        self.assertRaises(Http404,
                          self.middleware.process_request, request)
        self.assertEqual(settings.SITE_ID, 0)


@skipUnless(Site._meta.installed,
            'django.contrib.sites is not in settings.INSTALLED_APPS')
@override_settings(
    SITE_ID=SiteID(default=0),
    CACHE_MULTISITE_ALIAS='django.core.cache.backends.dummy.DummyCache',
    MULTISITE_FALLBACK=None,
    MULTISITE_FALLBACK_KWARGS={},
)
class DynamicSiteMiddlewareFallbackTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory(host='unknown')

        Site.objects.all().delete()

        self.middleware = DynamicSiteMiddleware()

    def tearDown(self):
        settings.SITE_ID.reset()

    def test_404(self):
        request = self.factory.get('/')
        self.assertRaises(Http404,
                          self.middleware.process_request, request)
        self.assertEqual(settings.SITE_ID, 0)

    def test_testserver(self):
        host = 'testserver'
        site = Site.objects.create(domain=host)
        request = self.factory.get('/', host=host)
        self.assertEqual(self.middleware.process_request(request), None)
        self.assertEqual(settings.SITE_ID, site.pk)

    def test_string_function(self):
        # Function based
        settings.MULTISITE_FALLBACK = 'django.views.generic.simple.redirect_to'
        settings.MULTISITE_FALLBACK_KWARGS = {'url': 'http://example.com/',
                                              'permanent': False}
        request = self.factory.get('/')
        response = self.middleware.process_request(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'],
                         settings.MULTISITE_FALLBACK_KWARGS['url'])

    def test_string_class(self):
        # Class based
        settings.MULTISITE_FALLBACK = 'django.views.generic.base.RedirectView'
        settings.MULTISITE_FALLBACK_KWARGS = {'url': 'http://example.com/',
                                              'permanent': False}
        request = self.factory.get('/')
        response = self.middleware.process_request(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'],
                         settings.MULTISITE_FALLBACK_KWARGS['url'])

    def test_function_view(self):
        from django.views.generic.simple import redirect_to
        settings.MULTISITE_FALLBACK = redirect_to
        settings.MULTISITE_FALLBACK_KWARGS = {'url': 'http://example.com/',
                                              'permanent': False}
        request = self.factory.get('/')
        response = self.middleware.process_request(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'],
                         settings.MULTISITE_FALLBACK_KWARGS['url'])

    def test_class_view(self):
        from django.views.generic.base import RedirectView
        settings.MULTISITE_FALLBACK = RedirectView.as_view(
            url='http://example.com/', permanent=False
        )
        request = self.factory.get('/')
        response = self.middleware.process_request(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'http://example.com/')

    def test_invalid(self):
        settings.MULTISITE_FALLBACK = ''
        request = self.factory.get('/')
        self.assertRaises(ImproperlyConfigured,
                          self.middleware.process_request, request)


@skipUnless(Site._meta.installed,
            'django.contrib.sites is not in settings.INSTALLED_APPS')
@override_settings(SITE_ID=0,)
class DynamicSiteMiddlewareSettingsTest(TestCase):
    def test_invalid_settings(self):
        self.assertRaises(TypeError, DynamicSiteMiddleware)


@override_settings(
    SITE_ID=SiteID(default=0),
    CACHE_MULTISITE_ALIAS='django.core.cache.backends.locmem.LocMemCache',
    MULTISITE_FALLBACK=None,
)
class CacheTest(TestCase):
    def setUp(self):
        self.host = 'example.com'
        self.factory = RequestFactory(host=self.host)

        Site.objects.all().delete()
        self.site = Site.objects.create(domain=self.host)

        self.middleware = DynamicSiteMiddleware()

    def test_site_domain_changed(self):
        # Test to ensure that the cache is cleared properly
        cache_key = self.middleware.get_cache_key(self.host)
        self.assertEqual(self.middleware.cache.get(cache_key), None)
        # Make the request
        request = self.factory.get('/')
        self.assertEqual(self.middleware.process_request(request), None)
        self.assertEqual(self.middleware.cache.get(cache_key), self.site.pk)
        # Change the domain name
        self.site.domain = 'example.org'
        self.site.save()
        self.assertEqual(self.middleware.cache.get(cache_key), None)
        # Make the request again, which will now be invalid
        request = self.factory.get('/')
        self.assertRaises(Http404,
                          self.middleware.process_request, request)
        self.assertEqual(settings.SITE_ID, 0)


class TestSiteID(TestCase):
    def setUp(self):
        Site.objects.all().delete()
        self.site = Site.objects.create(domain='example.com')
        self.site_id = SiteID()

    def test_invalid_default(self):
        self.assertRaises(ValueError, SiteID, default='a')
        self.assertRaises(ValueError, SiteID, default=self.site_id)

    def test_compare_default_site_id(self):
        self.site_id = SiteID(default=self.site.id)
        self.assertEqual(self.site_id, self.site.id)
        self.assertFalse(self.site_id != self.site.id)
        self.assertFalse(self.site_id < self.site.id)
        self.assertTrue(self.site_id <= self.site.id)
        self.assertFalse(self.site_id > self.site.id)
        self.assertTrue(self.site_id >= self.site.id)

    def test_compare_site_ids(self):
        self.site_id.set(1)
        self.assertEqual(self.site_id, self.site_id)
        self.assertFalse(self.site_id != self.site_id)
        self.assertFalse(self.site_id < self.site_id)
        self.assertTrue(self.site_id <= self.site_id)
        self.assertFalse(self.site_id > self.site_id)
        self.assertTrue(self.site_id >= self.site_id)

    def test_compare_differing_types(self):
        self.site_id.set(1)
        # SiteIDHook <op> int
        self.assertNotEqual(self.site_id, '1')
        self.assertFalse(self.site_id == '1')
        self.assertTrue(self.site_id < '1')
        self.assertTrue(self.site_id <= '1')
        self.assertFalse(self.site_id > '1')
        self.assertFalse(self.site_id >= '1')
        # int <op> SiteIDHook
        self.assertNotEqual('1', self.site_id)
        self.assertFalse('1' == self.site_id)
        self.assertFalse('1' < self.site_id)
        self.assertFalse('1' <= self.site_id)
        self.assertTrue('1' > self.site_id)
        self.assertTrue('1' >= self.site_id)

    def test_set(self):
        self.site_id.set(10)
        self.assertEqual(int(self.site_id), 10)
        self.site_id.set(20)
        self.assertEqual(int(self.site_id), 20)
        self.site_id.set(self.site)
        self.assertEqual(int(self.site_id), self.site.id)

    def test_hash(self):
        self.site_id.set(10)
        self.assertEqual(hash(self.site_id), 10)
        self.site_id.set(20)
        self.assertEqual(hash(self.site_id), 20)

    def test_str_repr(self):
        self.site_id.set(10)
        self.assertEqual(str(self.site_id), '10')
        self.assertEqual(repr(self.site_id), '10')


@skipUnless(Site._meta.installed,
            'django.contrib.sites is not in settings.INSTALLED_APPS')
class TestSiteDomain(TestCase):
    def setUp(self):
        Site.objects.all().delete()
        self.domain = 'example.com'
        self.site = Site.objects.create(domain=self.domain)

    def test_init(self):
        self.assertEqual(int(SiteDomain(default=self.domain)), self.site.id)
        self.assertRaises(Site.DoesNotExist,
                          int, SiteDomain(default='invalid'))
        self.assertRaises(ValueError, SiteDomain, default=None)
        self.assertRaises(ValueError, SiteDomain, default=1)

    def test_deferred_site(self):
        domain = 'example.org'
        self.assertRaises(Site.DoesNotExist,
                          int, SiteDomain(default=domain))
        site = Site.objects.create(domain=domain)
        self.assertEqual(int(SiteDomain(default=domain)),
                         site.id)


class TestSiteIDHook(TestCase):
    def test_deprecation_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            threadlocals.__warningregistry__ = {}
            SiteIDHook()
            self.assertTrue(w)
            self.assertTrue(issubclass(w[-1].category, DeprecationWarning))

    def test_default_value(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            site_id = SiteIDHook()
            self.assertEqual(site_id.default, 1)
            self.assertEqual(int(site_id), 1)
