"""Manual UI fixture. Original synthetic outputs only; never a real-model evaluation.
Run from repo root: python tests/manual_story_browser.py --port 8771
The temporary data is discarded when this foreground process exits.
"""
import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_local_app as fixture
import story_api

class SyntheticProvider:
    def __init__(self,*args,**kwargs):self.last_metrics={'done_reason':'stop'}
    def inspect_identity(self):return {'model':'synthetic-ui','digest':'a'*64}
    def complete(self,messages,tools):
        prompt=messages[0]['content']
        if 'paragraphs' in prompt:
            data={'paragraphs':[{'step':i,'text':f'原创合成测试第{i}段：工坊成员讨论了方案，仍有待解决的问题。'} for i in (1,2,3)]}
        elif 'options' in prompt:
            data={'situation':'原创合成测试：如何了解工坊分歧？','options':[
                {'label':'先听双方说明','reason':'先了解不同处境','cost':{'时间':1},'tradeoff':'减少试行时间','evidence_ids':[]},
                {'label':'先做小范围试行','reason':'用观察补充判断','cost':{'时间':1,'材料':1},'tradeoff':'耗用部分材料','evidence_ids':[]}]}
        else:
            data={'observation':'原创合成测试：成员提出了不同意见，尚未形成一致安排。','cause':'测试后果由固定夹具生成。','resource_delta':{},'reveal':[],'new_facts':[]}
        return {'content':json.dumps(data,ensure_ascii=False)}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8771);args=parser.parse_args()
    case=fixture.LocalAppTests();case.setUp()
    # Reuse its initialized original fixture root, but serve at the requested visible test port.
    try:
        with fixture.app.make_server(args.port,case.root) as server:
            with patch.object(story_api.story_runner,'OllamaProvider',SyntheticProvider), patch.object(story_api.story_editor,'OllamaProvider',SyntheticProvider):
                print(f'ORIGINAL SYNTHETIC UI FIXTURE http://127.0.0.1:{args.port}/story',flush=True)
                server.serve_forever()
    except KeyboardInterrupt:pass
    finally:case.doCleanups()
