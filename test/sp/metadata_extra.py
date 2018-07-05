import json
import os
import requests
import sys
import time
import unittest
import urllib.parse
import validators
import warnings

from io import BytesIO
from lxml import etree as ET

import common.constants
import common.helpers
import common.wrap

METADATA = os.getenv('SP_METADATA', None)
DATA_DIR = os.getenv('DATA_DIR', './data')
SSLLABS_FORCE_NEW = int(os.getenv('SSLLABS_FORCE_NEW', 0))
SSLLABS_SKIP = int(os.getenv('SSLLABS_SKIP', 0))

API = 'https://api.ssllabs.com/api/v2/'


def ssllabs_api(path, payload={}):
    url = API + path

    try:
        response = requests.get(url, params=payload)
    except requests.exception.RequestException:
        sys.stderr.write('Request failed.')
        sys.exit(1)

    data = response.json()
    return data


def ssllabs_from_cache(host, publish='off', startNew='off', fromCache='on',
                       all='done'):
    path = 'analyze'
    payload = {
                'host': host,
                'publish': publish,
                'startNew': startNew,
                'fromCache': fromCache,
                'all': all
              }
    data = ssllabs_api(path, payload)
    return data


def ssllabs_new_scan(host, publish='off', startNew='on', all='done',
                     ignoreMismatch='on'):
    path = 'analyze'
    payload = {
                'host': host,
                'publish': publish,
                'startNew': startNew,
                'all': all,
                'ignoreMismatch': ignoreMismatch
              }
    results = ssllabs_api(path, payload)

    payload.pop('startNew')

    if 'status' in results:
        while results['status'] != 'READY' and results['status'] != 'ERROR':
            time.sleep(30)
            results = ssllabs_api(path, payload)

    return results


class TestSPMetadataExtra(unittest.TestCase, common.wrap.TestCaseWrap):
    longMessage = False

    @classmethod
    def tearDownClass(cls):
        fname = '%s/sp-metadata-extra.json' % DATA_DIR
        with open(fname, 'w') as f:
            f.write(json.dumps(cls.report, indent=2))
            f.close()

    def setUp(self):
        self.failures = []
        _report = self.__class__.report
        paths = self.id().split('.')
        c = 1
        for path in paths:
            if path not in _report:
                if c == len(paths):
                    _report[path] = {
                        'description': self.shortDescription(),
                        'assertions': [],
                    }
                else:
                    _report[path] = {}
            _report = _report[path]
            c += 1

        if not METADATA:
            self.fail('SP_METADATA not set')

        with open(METADATA, 'rb') as md_file:
            md = md_file.read()
            md_file.close()

        self.doc = ET.parse(BytesIO(md))
        common.helpers.del_ns(self.doc)

        warnings.filterwarnings(
            action="ignore",
            message="unclosed",
            category=ResourceWarning
        )

    def tearDown(self):
        warnings.filterwarnings(
            action="ignore",
            message="unclosed",
            category=ResourceWarning
        )
        if self.failures:
            self.fail(common.helpers.dump_failures(self.failures))

    def test_entityID(self):
        ed = self.doc.xpath('//EntityDescriptor')[0]
        eid = ed.get('entityID')
        self._assertIsValidHttpsUrl(
            eid,
            'the entityID attribute must be a valid HTTPS url'
        )

    def test_SPSSODescriptor(self):
        spsso = self.doc.xpath('//EntityDescriptor/SPSSODescriptor')

        pse = spsso[0].get('protocolSupportEnumeration')
        self._assertEqual(
            pse,
            'urn:oasis:names:tc:SAML:2.0:protocol',
            'the protocolSupportEnumeration attribute must be '
            'urn:oasis:names:tc:SAML:2.0:protocol'
        )

        was = spsso[0].get('WantAssertionsSigned')
        self._assertEqual(was.lower(), 'true',
                          'the WantAssertionsSigned attribute must be true')

    def test_Organization(self):
        org = self.doc.xpath('//EntityDescriptor/Organization')[0]

        for elem in ['Name', 'URL', 'DisplayName']:
            e = org.xpath(
                './Organization%s[@xml:lang="it"]' % elem,
                namespaces={
                    'xml': 'http://www.w3.org/XML/1998/namespace',
                }
            )
            self._assertEqual(
                len(e),
                1,
                'an IT localised Organization%s must be present' % elem
            )

    @unittest.skipIf(SSLLABS_SKIP == 1, 'x')
    def test_ssllabs(self):
        locations = []
        c = 0
        acss = self.doc.xpath('//EntityDescriptor/SPSSODescriptor'
                              '/AssertionConsumerService')
        for acs in acss:
            url = acs.get('Location')
            locations.append(url)

        to_check = [(urllib.parse.urlparse(location).netloc, location)
                    for location in locations]
        for t in to_check:
            if (SSLLABS_FORCE_NEW == 1):
                data = ssllabs_new_scan(t[0])
            else:
                data = ssllabs_from_cache(t[0])
                while data['status'] != 'ERROR' and data['status'] != 'READY':
                    time.sleep(30)
                    data = ssllabs_from_cache(t[0])

            self._assertIsTLSGrade(
                {'location': t[1], 'data': data,
                 'service': 'AssertionConsumerService'},
                ['A+', 'A', 'A-'],
                '%s must be reachable and have strong TLS configuration' % t[1]
            )

        locations = []
        slos = self.doc.xpath('//EntityDescriptor/SPSSODescriptor'
                              '/SingleLogoutService')
        for slo in slos:
            url = slo.get('Location')
            locations.append(url)

        to_check = [(urllib.parse.urlparse(location).netloc, location)
                    for location in locations]
        for t in to_check:
            if (SSLLABS_FORCE_NEW == 1):
                data = ssllabs_new_scan(t[0])
            else:
                data = ssllabs_from_cache(t[0])
                while data['status'] != 'ERROR' and data['status'] != 'READY':
                    time.sleep(30)
                    data = ssllabs_from_cache(t[0])

            self._assertIsTLSGrade(
                {'location': t[1], 'data': data,
                 'service': 'SingleLogoutService'},
                ['A+', 'A', 'A-'],
                '%s must be reachable and have strong TLS configuration' % t[1]
            )

    def test_AttributeConsumingService(self):
        acss = self.doc.xpath('//EntityDescriptor/SPSSODescriptor'
                              '/AttributeConsumingService')
        for acs in acss:
            ras = acs.xpath('./RequestedAttribute')
            for ra in ras:
                a = ra.get('NameFormat')
                if a is not None:
                    self._assertIn(a, common.constants.ALLOWED_FORMATS,
                                   'NameFormat attribute must be valid')
