"""Explicit metadata-only check of the default loopback model service."""
from ollama_provider import OllamaProvider


def model_status():
    provider=OllamaProvider('local-inventory',timeout=2)
    try:
        inventory=provider._request('/api/tags').get('models')
        if not isinstance(inventory,list):raise ValueError('Invalid inventory')
        models=[]
        for item in inventory:
            if not isinstance(item,dict):raise ValueError('Invalid inventory row')
            name=item.get('name')
            if item.get('remote_model') or item.get('remote_host') or not isinstance(name,str) or len(name)>200:
                continue
            try:OllamaProvider(name,timeout=2)
            except ValueError:continue
            models.append(name)
        return {'status':'available' if models else 'empty','models':sorted(set(models)),
                'message':'发现本机模型；生成前仍会核验模型身份。' if models else '服务可达，但未发现可用的本机模型。'}
    except (ValueError,OSError):
        return {'status':'unavailable','models':[],'message':'暂时无法读取本机 Ollama 模型列表。请先启动服务，再重试；已有作品不受影响。'}
