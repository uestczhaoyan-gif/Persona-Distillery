"""Memory-only public chat with real runtime, original stub answers and failure guards."""
import json
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import test_local_app as http
import test_persona_memory as memory
import chat_api

class ChatAppTests(unittest.TestCase):
    request=http.LocalAppTests.request
    close=http.LocalAppTests.close

    def setUp(self):
        self.root=memory.PersonaMemoryTests.fixture(self)
        self.server=http.app.make_server(0,self.root)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start();self.addCleanup(self.close)
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.mock=patch.object(chat_api,'OllamaProvider');self.provider=self.mock.start().return_value;self.addCleanup(self.mock.stop)
        self.provider.model='synthetic';self.provider.supports_tools=True
        self.provider.inspect_identity.return_value={'model':'synthetic','digest':'a'*64}
        self.provider.last_metrics={'done_reason':'stop'}
        self.provider.complete.return_value={'role':'assistant','content':'An original synthetic answer.'}

    def create(self,**extra):
        self.sid=self.request('/api/chats',{'person_id':'kongzi','preview':True,'model':'synthetic','confirm_local_service':True,**extra})['session_id']

    def action(self,name,**extra):
        v=self.request('/api/chats/'+self.sid)
        return self.request(f'/api/chats/{self.sid}/{name}',{'version':v['version'],**extra})

    def wait(self):
        for _ in range(150):
            v=self.request('/api/chats/'+self.sid)
            if v['job']['status']!='running':return v
            time.sleep(.02)
        self.fail('Synthetic job timed out')

    def test_chat_and_audit_no_transcript_files(self):
        self.create();self.action('send',text='An original synthetic learning question')
        v=self.wait();self.assertEqual(v['job']['status'],'done');self.assertEqual(len(v['turns']),1)
        self.action('audit');v=self.wait();self.assertEqual(v['job']['status'],'done')
        self.assertIn('post_hoc_audit',v['job']['audit']);self.assertNotIn('context',v)
        self.assertFalse((self.root/'直接对话/sessions').exists())
        self.action('close')
        with self.assertRaises(HTTPError) as missing:self.request('/api/chats/'+self.sid)
        self.assertEqual(missing.exception.code,404)

    def test_failed_turn_keeps_previous_answer_and_generic_error(self):
        self.create();self.action('send',text='First synthetic question');self.wait()
        self.provider.complete.side_effect=ValueError('RAW_INTERNAL_SYNTHETIC')
        self.action('send',text='Second synthetic question');v=self.wait()
        self.assertEqual(v['job']['status'],'failed');self.assertEqual(len(v['turns']),1)
        self.assertNotIn('RAW_INTERNAL',json.dumps(v))
        self.assertEqual(self.server.app.chats.records[self.sid]['runtime'].last_question,'First synthetic question')

    def test_drift_does_not_commit_and_preview_is_required(self):
        with self.assertRaises(HTTPError):self.create(preview=False)
        self.create()
        self.provider.inspect_identity.side_effect=[{'digest':'a'},{'digest':'b'}]
        self.action('send',text='Synthetic question');v=self.wait()
        self.assertEqual(v['job']['status'],'failed');self.assertEqual(v['turns'],[])

    def test_close_invalidates_inflight_and_duplicate_send_is_rejected(self):
        self.create();started=threading.Event();release=threading.Event()
        def complete(*args):
            started.set();release.wait(3)
            return {'role':'assistant','content':'Late synthetic answer'}
        self.provider.complete.side_effect=complete
        self.action('send',text='Synthetic question');self.assertTrue(started.wait(2))
        with self.assertRaises(HTTPError):self.action('send',text='Duplicate')
        self.action('close');release.set()
        with self.assertRaises(HTTPError) as missing:self.request('/api/chats/'+self.sid)
        self.assertEqual(missing.exception.code,404)

if __name__=='__main__':unittest.main()
