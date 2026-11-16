"""Offline regression tests. All data is synthetic. No network is permitted."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from email.message import Message
from unittest.mock import patch, Mock
from urllib.error import HTTPError, URLError

from agent.secret_scope import set_secret_scope, reset_secret_scope
from hermes_constants import set_hermes_home_override, reset_hermes_home_override

SPEC = importlib.util.spec_from_file_location('eboekhouden_candidate', Path(__file__).resolve().parents[1] / '__init__.py')
assert SPEC is not None and SPEC.loader is not None
plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin)

class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TEST_TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.secrets = {'EBOEKHOUDEN_API_TOKEN': 'synthetic-api-token', 'EBOEKHOUDEN_SOURCE': 'Hermes', 'EBOEKHOUDEN_BASE_URL': plugin.BASE_URL}
        self.addCleanup(reset_secret_scope, set_secret_scope(self.secrets))
        self.addCleanup(reset_hermes_home_override, set_hermes_home_override(self.home))
        for context in (
            patch.dict(os.environ, {'EBOEKHOUDEN_API_TOKEN': 'synthetic-process-token'}, clear=True),
            patch('socket.socket', side_effect=AssertionError('Network forbidden in offline tests')),
        ):
            context.start()
            self.addCleanup(context.stop)

    def call(self, **args):
        return json.loads(plugin._handle_api(args))

    def test_register_contract(self):
        ctx = Mock()
        plugin.register(ctx)
        self.assertEqual([x.kwargs['name'] for x in ctx.register_tool.call_args_list], ['eboekhouden_status', 'eboekhouden_api'])
        for call in ctx.register_tool.call_args_list:
            self.assertEqual(call.kwargs['schema']['function']['name'], call.kwargs['name'])
            self.assertTrue(callable(call.kwargs['handler']))

    def test_default_dry_run_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(plugin, '_call_api') as api:
            result = self.call(method='POST', path='/v1/relation', body={'name': 'Synthetic example'})
        self.assertTrue(result['dry_run'])
        api.assert_not_called()

    def test_confirm_requires_boolean_true(self):
        for confirm in (False, None, 'false', 'true', 1, [], {}, 'yes'):
            with self.subTest(confirm=confirm), patch.object(plugin, '_call_api') as api:
                result = self.call(method='POST', path='/v1/relation', confirm=confirm)
                self.assertTrue(result['dry_run'])
                api.assert_not_called()

    def test_explicit_write(self):
        with patch.object(plugin, '_call_api', return_value={'status': 201, 'payload': {'id': 123}}) as api:
            self.assertTrue(self.call(method='POST', path='/v1/relation', body={'name': 'Synthetic example'}, confirm=True)['ok'])
        api.assert_called_once_with('POST', '/v1/relation', {}, {'name': 'Synthetic example'})

    def test_delete_acknowledgement(self):
        with patch.object(plugin, '_call_api', return_value={'status': 204, 'payload': None}) as api:
            self.assertIn('error', self.call(method='DELETE', path='/v1/product/123', confirm=True))
            api.assert_not_called()
            self.assertTrue(self.call(method='DELETE', path='/v1/product/123', confirm=True, acknowledge='DELETE /v1/product/123')['ok'])
            api.assert_called_once()

    def test_full_allowlist(self):
        for path in plugin.READ_PATHS:
            self.assertTrue(plugin._method_allowed('GET', path))
        for path in plugin.POST_PATHS:
            self.assertTrue(plugin._method_allowed('POST', path))
        for method, paths in {
            'GET': ['costcenter/123', 'invoice/123', 'ledger/123', 'ledger/123/balance', 'member/123', 'mutation/123', 'product/123', 'relation/123'],
            'PATCH': ['costcenter/123', 'member/123', 'product/123', 'relation/123'],
            'DELETE': ['costcenter/123', 'product/123'],
        }.items():
            for path in paths:
                self.assertTrue(plugin._method_allowed(method, '/v1/' + path))
        for method, path in [('POST', '/v1/mutation'), ('PATCH', '/v1/ledger/123'), ('DELETE', '/v1/relation/123'), ('PUT', '/v1/product'), ('GET', '/v1/session')]:
            with patch.object(plugin, '_call_api') as api:
                self.assertIn('error', self.call(method=method, path=path, confirm=True))
                api.assert_not_called()

    def test_invoice_side_channels(self):
        for field in ('mutation', 'directDebit', 'Mutation', 'MUTATION', 'DirectDebit', 'DIRECTDEBIT'):
            for value in ({}, None, False):
                with patch.object(plugin, '_call_api') as api:
                    self.assertIn('error', self.call(method='POST', path='/v1/invoice', body={field: value}, confirm=True))
                    api.assert_not_called()

    def test_aliases(self):
        for alias, canonical in plugin.PATH_ALIASES.items():
            self.assertEqual(plugin._normalize_path(alias), canonical)
            self.assertEqual(plugin._normalize_path(alias + '/123'), canonical + '/123')

    def test_unsafe_paths(self):
        for path in ('https://example.invalid/v1/relation', '//example.invalid/v1/relation', '/v1/../relation', '/v1/%2e%2e/relation', '/v1/relation%2f123', '/v1/session'):
            with patch.object(plugin, '_call_api') as api:
                self.assertIn('error', self.call(method='GET', path=path))
                api.assert_not_called()

    def test_config_ignores_process_and_legacy_file(self):
        (self.home / 'eboekhouden.env').write_text('EBOEKHOUDEN_API_TOKEN=synthetic-file-token\n')
        self.assertEqual(plugin._config()['token'], 'synthetic-api-token')
        self.assertEqual(plugin._config()['source'], 'Hermes')

    def test_missing_scope_fails_closed_even_with_process_credentials(self):
        token = set_secret_scope(None)
        try:
            with patch.object(plugin, '_json_request') as http:
                self.assertIn('error', json.loads(plugin._handle_status({'action': 'session'})))
                self.assertIn('error', self.call(method='GET', path='/v1/relation'))
                self.assertIn('error', self.call(method='POST', path='/v1/relation', confirm=True))
                self.assertTrue(self.call(method='POST', path='/v1/relation')['dry_run'])
                http.assert_not_called()
        finally:
            reset_secret_scope(token)

    def test_scoped_miss_never_uses_process_credentials(self):
        for values in ({}, {'EBOEKHOUDEN_API_TOKEN': None}, {'EBOEKHOUDEN_API_TOKEN': ''}):
            token = set_secret_scope(values)
            try:
                with patch.object(plugin, '_json_request') as http:
                    self.assertEqual(plugin._config()['token'], '')
                    self.assertIn('error', json.loads(plugin._handle_status({'action': 'session'})))
                    http.assert_not_called()
            finally:
                reset_secret_scope(token)

    def test_legacy_alias_is_scoped(self):
        token = set_secret_scope({'EBOEKHOUDEN_ACCESS_TOKEN': 'synthetic-scoped-alias'})
        try:
            self.assertEqual(plugin._config()['token'], 'synthetic-scoped-alias')
        finally:
            reset_secret_scope(token)

    def test_optional_config_does_not_inherit_process_environment(self):
        token = set_secret_scope({'EBOEKHOUDEN_API_TOKEN': 'synthetic-scoped-token'})
        try:
            with patch.dict(os.environ, {'EBOEKHOUDEN_SOURCE': 'Other', 'EBOEKHOUDEN_BASE_URL': 'https://example.invalid'}):
                self.assertEqual(plugin._config(), {'token': 'synthetic-scoped-token', 'source': 'Hermes', 'base_url': plugin.BASE_URL})
        finally:
            reset_secret_scope(token)

    def test_two_profile_cache_isolation_and_context_restore(self):
        plugin._save_cached_session('synthetic-profile-a-session', 3600)
        profile_b = self.home / 'profile-b'
        home_token = set_hermes_home_override(profile_b)
        scope_token = set_secret_scope({'EBOEKHOUDEN_API_TOKEN': 'synthetic-profile-b-token'}, profile_home=str(profile_b))
        try:
            self.assertEqual(plugin._hermes_home(), profile_b)
            self.assertEqual(plugin._config()['token'], 'synthetic-profile-b-token')
            self.assertEqual(plugin._load_cached_session(), '')
            plugin._save_cached_session('synthetic-profile-b-session', 3600)
            self.assertEqual(plugin._load_cached_session(), 'synthetic-profile-b-session')
            # Even a copied cache with identical credentials belongs to one home.
            plugin._cache_path().write_bytes((self.home / plugin.SESSION_CACHE).read_bytes())
            same_token = set_secret_scope(self.secrets, profile_home=str(profile_b))
            try:
                self.assertEqual(plugin._load_cached_session(), '')
            finally:
                reset_secret_scope(same_token)
        finally:
            reset_secret_scope(scope_token)
            reset_hermes_home_override(home_token)
        self.assertEqual(plugin._config()['token'], 'synthetic-api-token')
        self.assertEqual(plugin._load_cached_session(), 'synthetic-profile-a-session')

    def test_scope_miss_cannot_reuse_existing_cache(self):
        plugin._save_cached_session('synthetic-old-session', 3600)
        token = set_secret_scope({})
        try:
            self.assertEqual(plugin._load_cached_session(), '')
            with patch.object(plugin, '_json_request') as http:
                self.assertIn('error', json.loads(plugin._handle_status({'action': 'session'})))
                http.assert_not_called()
        finally:
            reset_secret_scope(token)

    def test_untrusted_base_url_is_rejected(self):
        for base in ('http://example.invalid', 'https://example.invalid', 'https://api.e-boekhouden.nl@example.invalid', 'https://api.e-boekhouden.nl/extra', 'https://api.e-boekhouden.nl?x=1'):
            with self.subTest(base=base):
                with self.assertRaises(plugin.EboekhoudenError):
                    plugin._validate_config({'token': 'synthetic-api-token', 'source': 'Hermes', 'base_url': base})

    def test_status_does_not_disclose_token_or_home(self):
        result = plugin._handle_status({'action': 'config'})
        self.assertNotIn('synthetic-api-token', result)
        self.assertNotIn(str(self.home), result)
        self.assertTrue(json.loads(result)['config']['token_configured'])

    def test_missing_token_and_invalid_source(self):
        token = set_secret_scope({})
        self.addCleanup(reset_secret_scope, token)
        with patch.dict(os.environ, {}, clear=True):
            result = plugin._handle_status({'action': 'session'})
            self.assertIn('error', json.loads(result))
            self.assertNotIn(str(self.home), result)
        with self.assertRaises(plugin.EboekhoudenError):
            plugin._validate_config({'token': 'synthetic-api-token', 'source': 'too-long-source', 'base_url': plugin.BASE_URL})

    def test_session_creation_and_reuse(self):
        with patch.object(plugin, '_json_request', return_value=(200, {'token': 'synthetic-session-token', 'expiresIn': 3600})) as http:
            self.assertEqual(plugin._session_token(), 'synthetic-session-token')
            self.assertEqual(plugin._session_token(), 'synthetic-session-token')
            http.assert_called_once_with('POST', plugin.BASE_URL + '/v1/session', body={'accessToken': 'synthetic-api-token', 'source': 'Hermes'})
        self.assertEqual(plugin._cache_path().stat().st_mode & 0o777, 0o600)

    def test_cache_is_bound_to_configuration(self):
        plugin._save_cached_session('synthetic-old-session', 3600)
        self.secrets['EBOEKHOUDEN_API_TOKEN'] = 'synthetic-new-token'
        self.assertEqual(plugin._load_cached_session(), '')

    def test_expired_and_corrupt_cache(self):
        plugin._save_cached_session('synthetic-expired-session', 0)
        self.assertEqual(plugin._load_cached_session(), '')
        plugin._cache_path().write_text('{invalid')
        self.assertEqual(plugin._load_cached_session(), '')

    def test_cache_symlink_is_not_followed(self):
        target = self.home / 'synthetic-target'
        target.write_text('unchanged')
        plugin._cache_path().parent.mkdir(parents=True)
        plugin._cache_path().symlink_to(target)
        plugin._save_cached_session('synthetic-session-token', 3600)
        self.assertEqual(target.read_text(), 'unchanged')
        self.assertFalse(plugin._cache_path().is_symlink())

    def test_auth_retry_once_for_get(self):
        with patch.object(plugin, '_session_token', side_effect=['synthetic-old-session', 'synthetic-new-session']) as session, patch.object(plugin, '_json_request', side_effect=[(401, {}), (200, {'items': []})]) as http:
            self.assertEqual(plugin._call_api('GET', '/v1/relation')['status'], 200)
            self.assertEqual(http.call_count, 2)
            self.assertEqual(session.call_args_list[-1].kwargs, {'force_new': True})

    def test_mutations_are_not_automatically_replayed(self):
        for method in ('POST', 'PATCH', 'DELETE'):
            with patch.object(plugin, '_session_token', return_value='synthetic-session-token'), patch.object(plugin, '_json_request', return_value=(401, {})) as http:
                self.assertEqual(plugin._call_api(method, '/v1/relation')['status'], 401)
                http.assert_called_once()

    def test_error_payload_is_not_returned(self):
        with patch.object(plugin, '_session_token', return_value='synthetic-session-token'), patch.object(plugin, '_json_request', return_value=(400, {'token': 'synthetic-secret-marker'})):
            result = plugin._handle_api({'method': 'GET', 'path': '/v1/relation'})
            self.assertNotIn('synthetic-secret-marker', result)
            self.assertFalse(json.loads(result)['ok'])

    def test_session_error_is_redacted(self):
        with patch.object(plugin, '_json_request', return_value=(401, {'accessToken': 'synthetic-secret-marker'})):
            result = plugin._handle_status({'action': 'session'})
            self.assertNotIn('synthetic-secret-marker', result)
            self.assertIn('error', json.loads(result))

    def test_transport_uses_redirect_blocker(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"items": []}'
        response.status = 200
        opener = Mock()
        opener.open.return_value = response
        with patch.object(plugin.request, 'build_opener', return_value=opener) as build:
            self.assertEqual(plugin._json_request('GET', plugin.BASE_URL + '/v1/relation'), (200, {'items': []}))
            self.assertIsInstance(build.call_args.args[0], plugin.request.HTTPRedirectHandler)
            self.assertIsNone(build.call_args.args[0].redirect_request(None, None, 302, 'redirect', {}, 'https://example.invalid'))

    def test_transport_network_error_is_redacted(self):
        opener = Mock()
        opener.open.side_effect = URLError('synthetic-secret-marker')
        with patch.object(plugin.request, 'build_opener', return_value=opener):
            with self.assertRaises(plugin.EboekhoudenError) as result:
                plugin._json_request('GET', plugin.BASE_URL + '/v1/relation')
        self.assertNotIn('synthetic-secret-marker', str(result.exception))

    def test_http_errors_are_redacted(self):
        opener = Mock()
        opener.open.side_effect = HTTPError(plugin.BASE_URL, 403, 'synthetic-secret-marker', Message(), io.BytesIO(b'synthetic-secret-marker'))
        with patch.object(plugin.request, 'build_opener', return_value=opener):
            status, payload = plugin._json_request('GET', plugin.BASE_URL + '/v1/relation')
        self.assertEqual(status, 403)
        self.assertNotIn('synthetic-secret-marker', json.dumps(payload))

    def test_transport_empty_and_invalid_json(self):
        for raw, expected in ((b'', (204, None)), (b'not-json', None)):
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = raw
            response.status = 204
            opener = Mock()
            opener.open.return_value = response
            with patch.object(plugin.request, 'build_opener', return_value=opener):
                if expected is None:
                    with self.assertRaises(plugin.EboekhoudenError):
                        plugin._json_request('GET', plugin.BASE_URL + '/v1/relation')
                else:
                    self.assertEqual(plugin._json_request('GET', plugin.BASE_URL + '/v1/relation'), expected)

    def test_json_transport_encoding(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"id":123}'
        response.status = 201
        opener = Mock()
        opener.open.return_value = response
        with patch.object(plugin.request, 'build_opener', return_value=opener):
            plugin._json_request('POST', plugin.BASE_URL + '/v1/relation', headers={'Authorization': 'synthetic-session-token'}, body={'name': 'Synthetic example'})
        req = opener.open.call_args.args[0]
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(req.get_header('Authorization'), 'synthetic-session-token')
        self.assertEqual(req.get_header('Content-type'), 'application/json')
        self.assertEqual(json.loads(req.data), {'name': 'Synthetic example'})
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 30)

    def test_query_encoding(self):
        with patch.object(plugin, '_session_token', return_value='synthetic-session-token'), patch.object(plugin, '_json_request', return_value=(200, {})) as http:
            plugin._call_api('GET', '/v1/relation', {'name[like]': 'Synthetic & example', 'id': [123, 456]})
        self.assertEqual(http.call_args.args[1], plugin.BASE_URL + '/v1/relation?name%5Blike%5D=Synthetic+%26+example&id=123&id=456')

    def test_persistent_auth_failure_is_not_a_loop(self):
        with patch.object(plugin, '_session_token', return_value='synthetic-session-token'), patch.object(plugin, '_json_request', return_value=(403, {})) as http:
            self.assertEqual(plugin._call_api('GET', '/v1/relation')['status'], 403)
            self.assertEqual(http.call_count, 2)

    def test_status_actions(self):
        with patch.object(plugin, '_session_token', return_value='synthetic-session-token'):
            self.assertEqual(json.loads(plugin._handle_status({'action': 'session'}))['session'], 'ok')
        with patch.object(plugin, '_call_api', return_value={'status': 200, 'payload': []}) as api:
            self.assertTrue(json.loads(plugin._handle_status({'action': 'administrations'}))['ok'])
            api.assert_called_once_with('GET', '/v1/administration/linked', {'limit': 20}, None)
        self.assertIn('error', json.loads(plugin._handle_status({'action': 'invalid'})))

    def test_invoice_email_remains_explicitly_write_capable(self):
        body = {'email': {'subject': 'Synthetic example'}}
        with patch.object(plugin, '_call_api', return_value={'status': 201, 'payload': {}}) as api:
            self.assertTrue(self.call(method='POST', path='/v1/invoice', body=body)['dry_run'])
            api.assert_not_called()
            self.assertTrue(self.call(method='POST', path='/v1/invoice', body=body, confirm=True)['ok'])

    def test_generic_errors_do_not_disclose_details(self):
        with patch.object(plugin, '_call_api', side_effect=OSError('synthetic-secret-marker')):
            result = self.call(method='GET', path='/v1/relation')
            self.assertIn('error', result)
            self.assertNotIn('synthetic-secret-marker', json.dumps(result))

    def test_invalid_input_returns_json_error(self):
        for args in (None, [], 'invalid', {'method': 'GET', 'path': '/v1/relation', 'query': []}, {'method': 'POST', 'path': '/v1/relation', 'body': []}):
            with patch.object(plugin, '_call_api') as api:
                self.assertIn('error', json.loads(plugin._handle_api(args)))
                api.assert_not_called()
        self.assertIn('error', json.loads(plugin._handle_status(None)))

if __name__ == '__main__':
    unittest.main()
