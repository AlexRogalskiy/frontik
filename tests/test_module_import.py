import unittest


from .instances import FrontikTestInstance, common_frontik_start_options


class ModuleImportCacheTestCase(unittest.TestCase):

    def setUp(self):
        self.test_app = FrontikTestInstance(
            f'./frontik-test --app=tests.projects.test_module_import_app {common_frontik_start_options} '
            ' --config=tests/projects/frontik_consul_mock.cfg')
        self.test_app.start()

    def tearDown(self):
        self.test_app.stop()

    def test_call_serializer_first(self):
        response = self.test_app.get_page('use_serializer_lib')
        self.assertEqual(response.status_code, 200, 'should import json library')
        response = self.test_app.get_page('use_serializer_lib/json')
        self.assertEqual(200, response.status_code, 'should call custom module ok')

    def test_call_custom_module_first(self):
        response = self.test_app.get_page('use_serializer_lib/json')
        self.assertEqual(200, response.status_code, 'should call custom module ok')
        response = self.test_app.get_page('use_serializer_lib')
        self.assertEqual(500, response.status_code, 'should fail on import json library')
