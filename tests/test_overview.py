"""Overview links only known historical public catalog and real local records."""
import json
import unittest
from urllib.error import HTTPError
import test_local_app as fixture

class OverviewTests(unittest.TestCase):
    setUp=fixture.LocalAppTests.setUp
    close=fixture.LocalAppTests.close
    request=fixture.LocalAppTests.request

    def test_catalog_and_three_project_links(self):
        self.assertIn('工作空间',self.request('/home'))
        rt=self.request('/api/sessions',{'participants':['kongzi','zhuangzi'],'topic':'Original roundtable','goal':'Explore','max_turns':2,'preview':True})
        lecture=self.request('/api/lectures',{'person_id':'kongzi','topic':'Original lecture','audience':'Learners','goal':'Explain','minutes':10,'as_of':'2026-10-13','facts':[],'preview':True})
        story=self.request('/api/stories',{'person_id':'kongzi','title':'Original story','premise':'Fictional workshop','rules':'No magic','goal':'Learn','resources':{'time':6},'facts':[{'id':'W-0001','text':'Synthetic hidden fact','known':False}],'preview':True})
        value=self.request('/api/overview')
        self.assertEqual(len(value['personas']),2)
        self.assertEqual({p['kind'] for p in value['projects']},{'roundtable','lecture','story'})
        for row in value['projects']:
            self.assertIn(row['session_id'],row['url'])
            self.assertIn('local-token',self.request(row['url']))
        self.assertNotIn('Synthetic hidden fact',json.dumps(value))
        self.assertNotIn('pins',json.dumps(value))
        self.assertTrue(all(p['preview'] for p in value['personas']))

    def test_overview_uses_existing_request_boundary(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/overview',headers={'X-Local-Token':'invalid'})
        self.assertEqual(error.exception.code,403)

if __name__=='__main__':unittest.main()
