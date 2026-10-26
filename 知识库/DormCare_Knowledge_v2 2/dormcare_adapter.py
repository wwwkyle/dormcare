"""我将检索放在后端会话验证之后，联系方式单独读取。"""
import json, threading
from retriever import KnowledgeBase,ROOT

_kb=KnowledgeBase()
_lock=threading.Lock()
_sources={a['source_id']:a for a in json.loads((ROOT/'data/sources.json').read_text())}
_contacts=json.loads((ROOT/'data/service_contacts.json').read_text())

def retrieve_context(query,user,category=None,device_model=None):
    """我只接受服务端会话提供的角色与已确认装置标识。"""
    role=user.get('role')
    if role not in ('student','worker','admin'):raise PermissionError('Authenticated role required')
    with _lock:
        result=_kb.search(query,role=role,category=category,device_model=device_model,college=user.get('college'),installation_id=user.get('heater_installation_id'))
    evidence=[]
    for r in result['results']:
        evidence.append(dict(chunk_id=r['chunk_id'],title=r['title'],text=r['text'],
            kind=r['knowledge_type'],scope=r['applicability_scope'],review_status=r['review_status'],
            sources=[dict(source_id=sid,title=_sources[sid]['title'],url=_sources[sid].get('url')) for sid in r['source_ids']],
            locators=r['source_locators'],narrative_voice=r.get('narrative_voice'),media_paths=r.get('media_paths',[])))
    # JSON is data to quote in an evidence message, not executable prompt instructions.
    result['context_json']=json.dumps(evidence,ensure_ascii=False)
    result['service_contacts']=[c for c in _contacts if role in c['allowed_roles'] and c['installation_id']==user.get('heater_installation_id') and result['status']=='prototype_evidence' and any(r.get('case_id')==c['case_id'] for r in result['results'])]
    return result
