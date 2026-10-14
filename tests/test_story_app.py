"""Story HTTP paths use committed synthetic states and preserve branch boundaries."""
import json
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import test_local_app as http
import test_story_state as fixture
import story_api

class StoryAppTests(unittest.TestCase):
    setUp = http.LocalAppTests.setUp
    close = http.LocalAppTests.close
    request = http.LocalAppTests.request

    def create(self):
        self.config = {"person_id":"kongzi", "title":"Synthetic workshop", "premise":"A fictional workshop", "rules":"No magic", "goal":"Compare approaches", "resources":{"days":6}, "facts":[{"id":"W-0001","text":"SYNTHETIC_SECRET","known":False}], "preview":True}
        self.sid = self.request('/api/stories', self.config)['session_id']
        self.proposal = {"situation":"Choose", "options":[{"label":name,"reason":"Learn", "cost":{"days":1}, "tradeoff":"Less time"} for name in ('Ask','Try')]}
        self.effect = {"observation":"A synthetic outcome", "cause":"INTERNAL_CAUSE", "resource_delta":{},"reveal":[],"new_facts":[]}

    def view(self, branch='main'):
        return self.request(f'/api/stories/{self.sid}/{branch}')

    def action(self, action, branch='main', **data):
        return self.request(f'/api/stories/{self.sid}/{branch}/{action}', data)

    def advance(self, action, payload, branch='main'):
        return fixture.story.mutate(self.sid,branch,self.view(branch)['version'],action,payload,root=self.root)

    def test_branch_selection_finish_render_restart_and_tamper(self):
        self.create()
        self.assertEqual(len(self.request('/api/story-library')['personas']),2)
        initial = self.request(f'/api/story-branches/{self.sid}')['fork_points'][0]['snapshot']
        branch = self.action('fork', snapshot=initial)['branch_id']
        for _ in range(3):
            self.advance('propose',self.proposal)
            self.action('choose',version=self.view()['version'],index=0)
            self.advance('effect',self.effect)
        self.assertEqual(self.view()['status'],'ended')
        self.assertFalse(self.view()['rendered'])
        self.assertEqual(self.view(branch)['actor']['resources']['days'],6)
        self.assertNotIn('SYNTHETIC_SECRET',json.dumps(self.view()))
        self.assertNotIn('INTERNAL_CAUSE',json.dumps(self.view()))
        result=self.action('render')
        self.assertTrue(self.view()['rendered'])
        self.assertFalse(self.view(branch)['rendered'])
        with fixture.story.store.locked(fixture.story.directory(self.sid,self.root)):
            self.assertTrue(self.view()['rendered'])
        route=f'/api/story-artifacts/{self.sid}/main/story.md'
        self.assertIn('Ask',self.request(route)['content'])
        self.assertEqual(story_api.Stories(self.root).view(self.sid)['status'],'ended')
        self.assertEqual(self.request('/api/stories')['sessions'][0]['session_id'],self.sid)
        file=fixture.story.directory(self.sid,self.root)/result['directory']/'story.md'
        file.write_text('Manual change retained',encoding='utf-8')
        with self.assertRaises(HTTPError):self.request(route)
        with self.assertRaises(HTTPError):self.view()
        self.assertEqual(file.read_text(encoding='utf-8'),'Manual change retained')

    def test_stale_version_preview_and_artifact_boundaries(self):
        self.create()
        self.config['preview']=False
        with self.assertRaises(HTTPError):self.request('/api/stories',self.config)
        version=self.view()['version']
        self.action('pause',version=version)
        with self.assertRaises(HTTPError):self.action('resume',version=version)
        self.action('resume',version=self.view()['version'])
        for name in ('story.md','current.json','input.json'):
            with self.assertRaises(HTTPError):self.request(f'/api/story-artifacts/{self.sid}/main/{name}')
        with self.assertRaises(HTTPError):self.action('generate',model='synthetic')

    def test_narrative_edit_job_and_download_preserve_chronological_draft(self):
        self.create()
        with self.assertRaises(HTTPError):
            self.action('edit', model='synthetic', confirm_local_service=True)
        for _ in range(3):
            self.advance('propose', self.proposal)
            self.action('choose', version=self.view()['version'], index=0)
            self.advance('effect', self.effect)
        self.action('render')
        original = self.request(f'/api/story-artifacts/{self.sid}/main/story.md')['content']
        with patch.object(story_api.story_editor, 'OllamaProvider') as provider:
            model = provider.return_value
            model.inspect_identity.return_value = {'model': 'synthetic', 'digest': 'a'*64}
            model.last_metrics = {'done_reason': 'stop'}
            model.complete.return_value = {'content': json.dumps({'paragraphs': [
                {'step': n, 'text': f'Original edited scene {n}.'} for n in (1, 2, 3)]})}
            self.action('edit', model='synthetic', confirm_local_service=True)
            for _ in range(100):
                value = self.view()
                if value['job']['status'] != 'running':
                    break
                time.sleep(.02)
        self.assertEqual(value['job']['status'], 'done')
        self.assertIsNotNone(value['edition'])
        self.assertIn('edited scene', self.request(f'/api/story-artifacts/{self.sid}/main/narrative.md')['content'])
        self.assertIn('决策 3', self.request(f'/api/story-artifacts/{self.sid}/main/narrative-provenance.md')['content'])
        self.assertEqual(original, self.request(f'/api/story-artifacts/{self.sid}/main/story.md')['content'])
        with self.assertRaises(HTTPError):
            self.action('edit', model='synthetic', confirm_local_service=True)
        self.assertIsNotNone(story_api.Stories(self.root).view(self.sid)['edition'])

    def test_async_failure_is_generic_and_state_retained(self):
        self.create()
        with patch.object(story_api.story_runner,'generate',side_effect=ValueError('SYNTHETIC_RAW_SECRET')):
            self.action('generate',model='synthetic',confirm_local_service=True)
            for _ in range(100):
                value=self.view()
                if value['job']['status']!='running':break
                time.sleep(.02)
        self.assertEqual(value['job']['status'],'failed')
        self.assertEqual(value['status'],'ready')
        self.assertNotIn('SYNTHETIC_RAW',json.dumps(value))

if __name__=='__main__':unittest.main()
