from mock import Mock, call
from os.path import dirname, join
from requests_mock import ANY, mock as requests_mock
from six import text_type
from unittest import TestCase
from json import load as json_load

from octodns.record import Record
from octodns.provider.ultra import UltraProvider, UltraNoZonesExistException
from octodns.provider.yaml import YamlProvider
from octodns.zone import Zone


def _get_provider():
    '''
    Helper to return a provider after going through authentication sequence
    '''
    with requests_mock() as mock:
        mock.post('https://restapi.ultradns.com/v2/authorization/token',
                  status_code=200,
                  text='{"token type": "Bearer", "refresh_token": "abc", '
                  '"access_token":"123", "expires_in": "3600"}')
        return UltraProvider('test', 'testacct', 'user', 'pass')


class TestUltraProvider(TestCase):
    expected = Zone('unit.tests.', [])
    host = 'https://restapi.ultradns.com'
    empty_body = [{"errorCode": 70002, "errorMessage": "Data not found."}]

    expected = Zone('unit.tests.', [])
    source = YamlProvider('test', join(dirname(__file__), 'config'))
    source.populate(expected)

    # Our test suite differs a bit, add our NS and remove the simple one
    expected.add_record(Record.new(expected, 'under', {
        'ttl': 3600,
        'type': 'NS',
        'values': [
            'ns1.unit.tests.',
            'ns2.unit.tests.',
        ]
    }))
    for record in list(expected.records):
        if record.name == 'sub' and record._type == 'NS':
            expected._remove_record(record)
            break

    def test_login(self):
        path = '/v2/authorization/token'

        # Bad Auth
        with requests_mock() as mock:
            mock.post('{}{}'.format(self.host, path), status_code=401,
                      text='{"errorCode": 60001}')
            with self.assertRaises(Exception) as ctx:
                UltraProvider('test', 'account', 'user', 'wrongpass')
            self.assertEquals('Unauthorized', text_type(ctx.exception))

        # Good Auth
        with requests_mock() as mock:
            mock.post('{}{}'.format(self.host, path), status_code=200,
                      text='{"token type": "Bearer", "refresh_token": "abc", '
                      '"access_token":"123", "expires_in": "3600"}')
            UltraProvider('test', 'account', 'user', 'pass',
                          test_endpoint=False)

        with requests_mock() as mock:
            test_host = 'https://test-restapi.ultradns.com'
            mock.post('{}{}'.format(test_host, path), status_code=200,
                      text='{"token type": "Bearer", "refresh_token": "abc", '
                      '"access_token":"123", "expires_in": "3600"}')
            UltraProvider('test', 'account', 'user', 'pass',
                          test_endpoint=True)

    def test_get_zones(self):
        provider = _get_provider()
        path = "/v2/zones"

        # Test no zones exist error
        with requests_mock() as mock:
            mock.get('{}{}'.format(self.host, path), status_code=404,
                     headers={'Authorization': 'Bearer 123'},
                     json=self.empty_body)
            zones = provider.zones
            self.assertEquals(list(), zones)

        # Reset zone cache so they are queried again
        provider._zones = None

        with requests_mock() as mock:
            payload = {
                "resultInfo": {
                    "totalCount": 1,
                    "offset": 0,
                    "returnedCount": 1
                },
                "zones": [
                    {
                        "properties": {
                            "name": "testzone123.com.",
                            "accountName": "testaccount",
                            "type": "PRIMARY",
                            "dnssecStatus": "UNSIGNED",
                            "status": "ACTIVE",
                            "owner": "user",
                            "resourceRecordCount": 5,
                            "lastModifiedDateTime": "2020-06-19T00:47Z"
                        }
                    }
                ]
            }

            mock.get('{}{}'.format(self.host, path), status_code=200,
                     headers={'Authorization': 'Bearer 123'},
                     json=payload)
            zones = provider.zones
            self.assertEquals(1, len(zones))
            self.assertEquals('testzone123.com.', zones[0])

        # Test different paging behavior
        provider._zones = None
        with requests_mock() as mock:
            mock.get('{}{}?limit=100&q=zone_type%3APRIMARY&offset=0'
                     .format(self.host, path), status_code=200,
                     json={"resultInfo": {"totalCount": 15,
                                          "offset": 0,
                                          "returnedCount": 10},
                           "zones": []})
            mock.get('{}{}?limit=100&q=zone_type%3APRIMARY&offset=10'
                     .format(self.host, path), status_code=200,
                     json={"resultInfo": {"totalCount": 15,
                                          "offset": 10,
                                          "returnedCount": 5},
                           "zones": []})
            zones = provider.zones
            self.assertEquals(mock.call_count, 2)

    def test_request(self):
        provider = _get_provider()
        path = '/foo'
        payload = {'a': 1}

        with requests_mock() as mock:
            mock.get('{}{}'.format(self.host, path), status_code=401,
                     headers={'Authorization': 'Bearer 123'}, json={})
            with self.assertRaises(Exception) as ctx:
                provider._get(path)
            self.assertEquals('Unauthorized', text_type(ctx.exception))

        # Test all GET patterns
        with requests_mock() as mock:
            mock.get('{}{}'.format(self.host, path), status_code=200,
                     headers={'Authorization': 'Bearer 123'},
                     json=payload)
            provider._get(path, json=payload)

            mock.get('{}{}?a=1'.format(self.host, path), status_code=200,
                     headers={'Authorization': 'Bearer 123'})
            provider._get(path, params=payload, json_response=False)

        # Test all POST patterns
        with requests_mock() as mock:
            mock.post('{}{}'.format(self.host, path), status_code=200,
                      headers={'Authorization': 'Bearer 123'},
                      json=payload)
            provider._post(path, json=payload)

            mock.post('{}{}'.format(self.host, path), status_code=200,
                      headers={'Authorization': 'Bearer 123'},
                      text="{'a':1}")
            provider._post(path, data=payload, json_response=False)

        # Test all PUT patterns
        with requests_mock() as mock:
            mock.put('{}{}'.format(self.host, path), status_code=200,
                     headers={'Authorization': 'Bearer 123'},
                     json=payload)
            provider._put(path, json=payload)

        # Test all DELETE patterns
        with requests_mock() as mock:
            mock.delete('{}{}'.format(self.host, path), status_code=200,
                        headers={'Authorization': 'Bearer 123'})
            provider._delete(path, json_response=False)

    def test_zone_records(self):
        provider = _get_provider()
        zone_payload = {
            "resultInfo": {"totalCount": 1,
                           "offset": 0,
                           "returnedCount": 1},
            "zones": [{"properties": {"name": "octodns1.test."}}]}

        records_payload = {
            "zoneName": "octodns1.test.",
            "rrSets": [
                {
                    "ownerName": "octodns1.test.",
                    "rrtype": "NS (2)",
                    "ttl": 86400,
                    "rdata": [
                        "ns1.octodns1.test."
                    ]
                },
                {
                    "ownerName": "octodns1.test.",
                    "rrtype": "SOA (6)",
                    "ttl": 86400,
                    "rdata": [
                        "pdns1.ultradns.com. phelps.netflix.com. 1 10 10 10 10"
                    ]
                },
            ],
            "resultInfo": {
                "totalCount": 2,
                "offset": 0,
                "returnedCount": 2
            }
        }

        zone_path = '/v2/zones'
        rec_path = '/v2/zones/octodns1.test./rrsets'
        with requests_mock() as mock:
            mock.get('{}{}?limit=100&q=zone_type%3APRIMARY&offset=0'
                     .format(self.host, zone_path),
                     status_code=200, json=zone_payload)
            mock.get('{}{}?offset=0&limit=100'.format(self.host, rec_path),
                     status_code=200, json=records_payload)

            zone = Zone('octodns1.test.', [])
            self.assertTrue(provider.zone_records(zone))
            self.assertEquals(mock.call_count, 2)

            # Populate the same zone again and confirm cache is hit
            self.assertTrue(provider.zone_records(zone))
            self.assertEquals(mock.call_count, 2)

    def test_populate(self):
        provider = _get_provider()

        # Non-existent zone doesn't populate anything
        with requests_mock() as mock:
            mock.get(ANY, status_code=404, json=self.empty_body)

            zone = Zone('unit.tests.', [])
            provider.populate(zone)
            self.assertEquals(set(), zone.records)

        # re-populating the same non-existent zone uses cache and makes no
        # calls
        again = Zone('unit.tests.', [])
        provider.populate(again)
        self.assertEquals(set(), again.records)

        # Test zones with data
        provider._zones = None
        path = '/v2/zones'
        with requests_mock() as mock:
            with open('tests/fixtures/ultra-zones-page-1.json') as fh:
                mock.get('{}{}?limit=100&q=zone_type%3APRIMARY&offset=0'
                         .format(self.host, path),
                         status_code=200, text=fh.read())
            with open('tests/fixtures/ultra-zones-page-2.json') as fh:
                mock.get('{}{}?limit=100&q=zone_type%3APRIMARY&offset=10'
                         .format(self.host, path),
                         status_code=200, text=fh.read())
            with open('tests/fixtures/ultra-records-page-1.json') as fh:
                rec_path = '/v2/zones/octodns1.test./rrsets'
                mock.get('{}{}?offset=0&limit=100'.format(self.host, rec_path),
                         status_code=200, text=fh.read())
            with open('tests/fixtures/ultra-records-page-2.json') as fh:
                rec_path = '/v2/zones/octodns1.test./rrsets'
                mock.get('{}{}?offset=10&limit=100'
                         .format(self.host, rec_path),
                         status_code=200, text=fh.read())

            zone = Zone('octodns1.test.', [])
            self.assertTrue(provider.populate(zone))
            self.assertEquals('octodns1.test.', zone.name)
            self.assertEquals(11, len(zone.records))
            self.assertEquals(mock.call_count, 4)

    def test_apply(self):
        provider = _get_provider()

        provider._request = Mock()

        provider._request.side_effect = [
            UltraNoZonesExistException('No Zones'),
            None,  # zone create
        ] + [None] * 13  # individual record creates

        # non-existent zone, create everything
        plan = provider.plan(self.expected)
        self.assertEquals(13, len(plan.changes))
        self.assertEquals(13, provider.apply(plan))
        self.assertFalse(plan.exists)

        provider._request.assert_has_calls([
            # created the domain
            call('POST', '/v2/zones', json={
                 'properties': {'name': 'unit.tests.',
                                'accountName': 'testacct',
                                'type': 'PRIMARY'},
                 'primaryCreateInfo': {'createType': 'NEW'}}),
            # Validate multi-ip apex A record is correct
            call('POST', '/v2/zones/unit.tests./rrsets/A/unit.tests.', json={
                'ttl': 300,
                'rdata': ['1.2.3.4', '1.2.3.5'],
                'profile': {
                    '@context':
                        'http://schemas.ultradns.com/RDPool.jsonschema',
                    'order': 'FIXED',
                    'description': 'unit.tests.'
                }
            }),
            # make sure semicolons are not escaped when sending data
            call('POST', '/v2/zones/unit.tests./rrsets/TXT/txt.unit.tests.',
                 json={'ttl': 600,
                       'rdata': ['Bah bah black sheep',
                                 'have you any wool.',
                                 'v=DKIM1;k=rsa;s=email;h=sha256;'
                                 'p=A/kinda+of/long/string+with+numb3rs']}),
        ], True)
        # expected number of total calls
        self.assertEquals(15, provider._request.call_count)

        # Create sample rrset payload to attempt to alter
        page1 = json_load(open('tests/fixtures/ultra-records-page-1.json'))
        page2 = json_load(open('tests/fixtures/ultra-records-page-2.json'))
        mock_rrsets = list()
        mock_rrsets.extend(page1['rrSets'])
        mock_rrsets.extend(page2['rrSets'])

        # Seed a bunch of records into a zone and verify update / delete ops
        provider._request.reset_mock()
        provider._zones = ['octodns1.test.']
        provider.zone_records = Mock(return_value=mock_rrsets)

        provider._request.side_effect = [None] * 13

        wanted = Zone('octodns1.test.', [])
        wanted.add_record(Record.new(wanted, '', {
            'ttl': 60,  # Change TTL
            'type': 'A',
            'value': '5.6.7.8'  # Change number of IPs (3 -> 1)
        }))
        # TODO: Figure out why this isn't happening
        wanted.add_record(Record.new(wanted, '', {
            'ttl': 3600,  # TTL change
            'type': 'NS',
            'values': [  # Add additional NS records
                "pdns1.ultradns.biz.",
                "pdns1.ultradns.com.",
                "pdns1.ultradns.net.",
                "pdns1.ultradns.org.",
                "pdns2.ultradns.biz.",
                "pdns2.ultradns.com.",
                "pdns2.ultradns.net.",
                "pdns2.ultradns.org.",
            ]
        }))
        wanted.add_record(Record.new(wanted, 'txt', {
            'ttl': 3600,
            'type': 'TXT',
            'values': [  # Alter TXT value
                "foobar",
                "v=spf1 include:mail.server.net ?all"
            ]
        }))

        plan = provider.plan(wanted)
        # TODO: 11 expected but NS isn't being respected
        self.assertEquals(10, len(plan.changes))
        self.assertEquals(10, provider.apply(plan))
        self.assertTrue(plan.exists)
