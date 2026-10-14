"""Original synthetic lecture UI server. Never a real-model quality evaluation.
Run: python tests/manual_lecture_browser.py --port 8773
Temporary artifacts are removed on normal shutdown.
"""
import argparse
import json
from unittest.mock import patch
import test_local_app as fixture
import lecture_api

class SyntheticProvider:
    def __init__(self,*args,**kwargs): self.last_metrics={'done_reason':'stop'}
    def inspect_identity(self): return {'model':'synthetic-ui','digest':'a'*64}
    def complete(self,messages,tools):
        context=json.loads(messages[1]['content'])
        if 'paragraphs' in messages[0]['content']:
            revised=bool(context.get('revision_request'))
            data={'paragraphs':[{'section_index':0,'text':'原创合成修订稿：先描述观察，再讨论解释。' if revised else '原创合成初稿：观察与解释需要区分。','evidence_ids':[],'fact_ids':[]}]}
        else:
            data={'title':'原创合成演讲','thesis':'区分观察与解释','scope':'edge','scope_reason':'仅用于工程流程测试','research_questions':[],
                  'sections':[{'heading':'从一个观察开始','purpose':'说明如何区分','minutes':context['minutes'],'evidence_ids':[],'fact_ids':[]}]}
        return {'content':json.dumps(data,ensure_ascii=False)}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8773)
    args=parser.parse_args()
    case=fixture.LocalAppTests();case.setUp()
    try:
        with fixture.app.make_server(args.port,case.root) as server:
            with patch.object(lecture_api.outlines,'OllamaProvider',SyntheticProvider):
                print(f'ORIGINAL SYNTHETIC UI FIXTURE http://127.0.0.1:{args.port}/lecture',flush=True)
                server.serve_forever()
    except KeyboardInterrupt: pass
    finally: case.doCleanups()
