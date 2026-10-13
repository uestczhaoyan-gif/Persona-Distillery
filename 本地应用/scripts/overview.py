"""Read-only overview of historical personas and existing local projects."""
from pathlib import Path
from urllib.parse import urlencode


def overview(app):
    import roundtable as storage
    people=[]
    available={p['person_id'] for p in app.library()}
    for person in sorted(set(storage.registry(app.root).values())):
        package=app.root/'personas'/person
        m=storage.read_json(storage.checked_file(package,'manifest.json'))
        if m.get('schema_version')!='1.0' or m.get('subject_kind')!='historical_public':
            continue
        coverage=m.get('coverage',{})
        people.append({'person_id':person,'display_name':m['display_name'],
                       'readiness':m['readiness'],'visibility':m['visibility'],
                       'topics':coverage.get('topics',[])[:6],
                       'period':coverage.get('period',''),
                       'modes':{'roundtable':person in available,'lecture':True,'story':True},
                       'preview':m['visibility']!='public' or m['readiness']=='draft'})
    recent=[]
    for kind,route,values in [('roundtable','/',app.sessions()),('lecture','/lecture',app.lectures.sessions()),('story','/story',app.stories.sessions())]:
        for value in values:
            recent.append({'kind':kind,'session_id':value['session_id'],'title':value['title'],
                           'status':value['status'],'url':route+'?'+urlencode({'session':value['session_id']})})
    recent.sort(key=lambda r:r['session_id'].split('-')[1],reverse=True)
    return {'personas':people,'projects':recent,'notice':'人物与内容质量仍待独立验收；项目记录只保存在本机。'}
