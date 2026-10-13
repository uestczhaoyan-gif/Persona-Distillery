"""Historical-public browser dialogue; memory-only sessions reuse ChatSession."""
from __future__ import annotations
import copy
from pathlib import Path
import sys
import threading
import uuid
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'直接对话/scripts'))
from chat_runtime import ChatSession
from ollama_provider import OllamaProvider
from persona_memory import compile_context
from resolve_persona import resolve_persona, registry, checked_file, read_json


class Chats:
    def __init__(self,root=ROOT):
        self.root=Path(root)
        self.records={}
        self.guard=threading.Lock()

    def admission(self,person,preview):
        if not isinstance(person,str) or registry(self.root).get(person)!=person:
            raise ValueError('Unknown person')
        manifest=read_json(checked_file(self.root/'personas'/person,'manifest.json'))
        if manifest.get('schema_version')!='1.0' or manifest.get('subject_kind')!='historical_public':
            raise ValueError('Historical public packages only')
        if (manifest.get('visibility')!='public' or manifest.get('readiness')=='draft') and not preview:
            raise ValueError('Confirm draft preview')
        return resolve_persona(person,self.root,preview)

    def create(self,data):
        person=data.get('person_id');preview=data.get('preview') is True
        plan=self.admission(person,preview)
        model=data.get('model')
        if data.get('confirm_local_service') is not True or not isinstance(model,str) or not model.strip() or len(model)>200:
            raise ValueError('Confirm installed local model')
        # Constructing the provider validates the loopback/local model name without a request.
        provider=OllamaProvider(model.strip())
        with self.guard:
            if len(self.records)>=32:raise ValueError('Close an old dialogue first')
            identifier='CH-'+uuid.uuid4().hex
            self.records[identifier]={'id':identifier,'person_id':person,'display_name':plan['display_name'],'preview':preview,
                                      'provider':provider,'runtime':None,'turns':[],'job':None,'version':0}
        return {'session_id':identifier}

    def view(self,identifier):
        with self.guard:
            r=self.records[identifier]
            return copy.deepcopy({k:r[k] for k in ('person_id','display_name','turns','job','version')}) | {
                'session_id':identifier,'storage':'memory_only','model':r['provider'].model}

    def action(self,identifier,action,data):
        if action=='close':
            with self.guard:
                r=self.records[identifier]
                # Removing the record invalidates any in-flight result.
                del self.records[identifier]
            return {'status':'closed'}
        if action not in {'send','audit'}:raise ValueError('Unknown chat action')
        with self.guard:
            r=self.records[identifier]
            if r['job'] and r['job']['status']=='running':raise ValueError('Dialogue request running')
            if sum(bool(v['job'] and v['job']['status']=='running') for v in self.records.values())>=2:
                raise ValueError('Local chat capacity reached')
            if action=='audit' and not r['turns']:raise ValueError('No answer to inspect')
            question=data.get('text','')
            if action=='send' and (not isinstance(question,str) or not question.strip() or len(question)>10000):
                raise ValueError('Question must contain 1-10000 characters')
            if data.get('version')!=r['version'] or type(data.get('version')) is not int:
                raise ValueError('Stale dialogue version')
            job={'id':uuid.uuid4().hex,'status':'running','action':action}
            r['job']=job
        def run():
            try:
                self.admission(r['person_id'],r['preview'])
                provider=r['provider']
                if action=='send':
                    identity=provider.inspect_identity()
                    original=r['runtime']
                    working=copy.copy(original) if original else ChatSession(r['person_id'],provider,self.root,r['preview'])
                    before=compile_context(r['person_id'],self.root,r['preview'])
                    if before['context_sha256']!=working.compiled['context_sha256']:
                        raise ValueError('Person material changed; start a new dialogue')
                    answer=working.send(question.strip())
                    after=compile_context(r['person_id'],self.root,r['preview'])
                    if after['input_sha256']!=before['input_sha256'] or provider.inspect_identity()!=identity or provider.last_metrics.get('done_reason')=='length':
                        raise ValueError('Material/model changed or response truncated')
                    with self.guard:
                        if self.records.get(identifier) is not r:return
                        r['runtime']=working
                        r['turns'].append({'question':question.strip(),'answer':answer})
                        r['version']+=1
                        job.update(status='done')
                else:
                    current=compile_context(r['person_id'],self.root,r['preview'])
                    if current['context_sha256']!=r['runtime'].compiled['context_sha256']:
                        raise ValueError('Person material changed')
                    result=r['runtime'].audit()
                    if compile_context(r['person_id'],self.root,r['preview'])['input_sha256']!=current['input_sha256']:
                        raise ValueError('Person material changed')
                    with self.guard:
                        if self.records.get(identifier) is not r:return
                        job.update(status='done',audit=result)
            except Exception:
                with self.guard:
                    if self.records.get(identifier) is r:
                        job.update(status='failed',error='本次请求未完成。请检查本机模型或人物材料；之前的对话已保留，可重试或新开对话。')
        threading.Thread(target=run,daemon=True).start()
        return {'job_id':job['id'],'status':'running'}
