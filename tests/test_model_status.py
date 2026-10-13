"""Local inventory check never sends prompts or starts model downloads."""
import unittest
from unittest.mock import patch
import test_local_app
import model_status as status

class ModelStatusTests(unittest.TestCase):
    def test_local_candidates_are_filtered_without_generation(self):
        with patch.object(status.OllamaProvider,'_request',return_value={'models':[{'name':'synthetic:latest'},{'name':'synthetic:latest'},
            {'name':'remote-cloud'},{'name':'remote','remote_host':'example.invalid'},{'name':'../bad'}]}) as request:
            value=status.model_status()
        self.assertEqual(value['status'],'available');self.assertEqual(value['models'],['synthetic:latest'])
        request.assert_called_once_with('/api/tags')

    def test_empty_invalid_and_unreachable_service_are_actionable(self):
        for inventory,expected in [({'models':[]},'empty'),({'models':'bad'},'unavailable')]:
            with patch.object(status.OllamaProvider,'_request',return_value=inventory):
                self.assertEqual(status.model_status()['status'],expected)
        with patch.object(status.OllamaProvider,'_request',side_effect=ValueError('RAW_INTERNAL_OUTPUT')):
            value=status.model_status()
        self.assertEqual(value['status'],'unavailable');self.assertNotIn('RAW_INTERNAL',value['message'])

class ModelStatusHttpTests(unittest.TestCase):
    setUp=test_local_app.LocalAppTests.setUp
    close=test_local_app.LocalAppTests.close
    request=test_local_app.LocalAppTests.request

    def test_http_metadata_check_and_ui_scripts(self):
        with patch.object(status.OllamaProvider,'_request',return_value={'models':[{'name':'synthetic:latest'}]}):
            value=self.request('/api/model-status')
        self.assertEqual(value['models'],['synthetic:latest'])
        for route in ('/','/lecture','/story','/chat'):
            self.assertIn('/model-status.js',self.request(route))
        self.assertIn('检查本机模型',self.request('/model-status.js'))

if __name__=='__main__':unittest.main()
