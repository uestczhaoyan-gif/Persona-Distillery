import json
import unittest
from unittest.mock import patch
import test_story_render as fixture
import story_editor as editor

class StoryEditorTests(unittest.TestCase):
    setUp=fixture.StoryRenderTests.setUp
    state=fixture.StoryRenderTests.state
    apply=fixture.StoryRenderTests.apply
    finish=fixture.StoryRenderTests.finish

    def raw(self):
        return {'content':json.dumps({'paragraphs':[{'step':i,'text':f'Original synthetic prose {i}.'} for i in (1,2,3)]})}

    def generate(self,model):
        model.inspect_identity.return_value={'model':'synthetic','digest':'a'*64}
        model.last_metrics={'done_reason':'stop'}
        model.complete.return_value=self.raw()
        return editor.edit(self.sid,'synthetic',root=self.root,confirm_local_service=True)

    def test_selected_path_only_and_outputs_preserved(self):
        self.finish()
        original=fixture.renderer.render(self.sid,root=self.root)
        with patch.object(editor,'OllamaProvider') as provider:
            result=self.generate(provider.return_value)
            sent=provider.return_value.complete.call_args.args[0][1]['content']
            self.assertNotIn('SYNTHETIC_HIDDEN',sent)
            self.assertNotIn('Try an experiment',sent)
            self.assertNotIn('cause',json.loads(sent)['events'][0])
            with self.assertRaises(ValueError):editor.edit(self.sid,'synthetic',root=self.root,confirm_local_service=True)
            self.assertEqual(provider.return_value.complete.call_count,1)
        self.assertEqual(editor.read_edition(self.sid,root=self.root),result)
        path=fixture.fixtures.story.directory(self.sid,self.root)
        self.assertTrue((path/original['directory']/'story.md').exists())
        self.assertIn('决策 3',(path/result['directory']/'provenance.md').read_text(encoding='utf-8'))
        (path/result['directory']/'story.md').write_text('manual',encoding='utf-8')
        with self.assertRaises(ValueError):editor.read_edition(self.sid,root=self.root)

    def test_missing_out_of_order_unknown_decisions_rejected(self):
        self.finish()
        for steps in ([1,1,2],[1,3,2],[1,2,4],[True,2,3]):
            raw={'content':json.dumps({'paragraphs':[{'step':i,'text':'Synthetic paragraph'} for i in steps]})}
            with self.assertRaises(ValueError):editor.normalize(raw,self.state())

    def test_failure_does_not_publish_edition(self):
        self.finish()
        with patch.object(editor,'OllamaProvider') as provider:
            provider.return_value.inspect_identity.return_value={'model':'synthetic'}
            provider.return_value.last_metrics={'done_reason':'length'}
            provider.return_value.complete.return_value=self.raw()
            with self.assertRaises(ValueError):editor.edit(self.sid,'synthetic',root=self.root,confirm_local_service=True)
        path=fixture.fixtures.story.directory(self.sid,self.root)
        self.assertFalse((path/'branches/main/edition-current.json').exists())
        self.assertEqual(self.state()['status'],'ended')

    def test_unfinished_and_unconfirmed_refused_before_model(self):
        with patch.object(editor,'OllamaProvider') as provider:
            with self.assertRaises(ValueError):editor.edit(self.sid,'synthetic',root=self.root)
            with self.assertRaises(ValueError):editor.edit(self.sid,'synthetic',root=self.root,confirm_local_service=True)
            provider.assert_not_called()

if __name__=='__main__':unittest.main()
