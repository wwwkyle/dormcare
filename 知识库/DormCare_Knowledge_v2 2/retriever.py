"""我用这个模块执行本地检索，并按访问角色和设备适用范围筛选资料。"""
from pathlib import Path
import argparse, collections, hashlib, json, math, os, re, sqlite3
import numpy as np

ROOT=Path(__file__).resolve().parent
MODEL='BAAI/bge-small-zh-v1.5'
DIM=512

def tokens(text):
    text=text.lower()
    words=re.findall(r'[a-z0-9_]+',text)
    for run in re.findall(r'[\u4e00-\u9fff]+',text):
        words.extend(run[i:i+2] for i in range(max(1,len(run)-1)))
    return words

def search_text(r):
    return r['title']+'。'+' '.join(r['keywords'])+'。'+r['text']

class KnowledgeBase:
    def __init__(self,root=ROOT,cache_dir=None):
        self.root=Path(root)
        self.cache_dir=cache_dir or os.environ.get('DORMCARE_MODEL_CACHE') or str(self.root/'.model_cache')
        self.encoder=None
        self.records=[]; self.matrix=None

    def encode(self,texts):
        if self.encoder is None:
            from fastembed import TextEmbedding
            self.encoder=TextEmbedding(MODEL,cache_dir=self.cache_dir,threads=2)
        x=np.asarray(list(self.encoder.embed(texts)),dtype=np.float32)
        return x/np.maximum(np.linalg.norm(x,axis=1,keepdims=True),1e-12)

    def build(self):
        p=self.root/'data/chunks.jsonl'
        records=[json.loads(s) for s in p.read_text().splitlines() if s]
        records=[r for r in records if r['active_index']]
        vectors=self.encode([search_text(r) for r in records])
        db=self.root/'data/knowledge.sqlite'
        tmp=db.with_suffix('.building.sqlite'); tmp.unlink(missing_ok=True)
        with sqlite3.connect(tmp) as c:
            c.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            c.execute('CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY,category TEXT NOT NULL,record_json TEXT NOT NULL,vector BLOB NOT NULL)')
            meta={'model':MODEL,'dimension':DIM,'distance':'cosine_normalized_dot','vector_dtype':'little_endian_float32',
                  'index_type':'SQLite BLOB dense vector store; exact cosine scan in NumPy',
                  'chunks_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'count':len(records),'version':'2.0'}
            c.executemany('INSERT INTO metadata VALUES(?,?)',[(k,json.dumps(v,ensure_ascii=False)) for k,v in meta.items()])
            c.executemany('INSERT INTO chunks VALUES(?,?,?,?)',[(r['chunk_id'],r['category'],json.dumps(r,ensure_ascii=False),v.astype('<f4').tobytes()) for r,v in zip(records,vectors)])
            assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        tmp.replace(db)
        self.records=records; self.matrix=vectors
        return meta

    def load(self):
        if self.matrix is not None: return
        db=self.root/'data/knowledge.sqlite'
        if not db.exists(): raise FileNotFoundError('Run python retriever.py --build first')
        with sqlite3.connect(f'file:{db}?mode=ro',uri=True) as c:
            meta={k:json.loads(v) for k,v in c.execute('SELECT key,value FROM metadata')}
            if meta['model']!=MODEL or meta['dimension']!=DIM: raise ValueError('Embedding model/dimension mismatch')
            actual=hashlib.sha256((self.root/'data/chunks.jsonl').read_bytes()).hexdigest()
            if meta['chunks_sha256']!=actual: raise ValueError('Corpus changed: rebuild index before searching')
            rows=c.execute('SELECT record_json,vector FROM chunks ORDER BY rowid').fetchall()
        self.records=[json.loads(a) for a,b in rows]
        self.matrix=np.stack([np.frombuffer(b,dtype='<f4') for a,b in rows])
        if not np.isfinite(self.matrix).all(): raise ValueError('Non-finite embedding')

    def eligible(self,role,category,device_model,college,installation_id=None):
        if role not in ('student','worker','admin'): raise ValueError('Invalid role')
        if category is not None and category not in {a['category_id'] for a in json.loads((self.root/'data/categories.json').read_text())}: raise ValueError('Invalid category')
        matched=[]
        for i,r in enumerate(self.records):
            if role not in r['allowed_roles']: continue
            if category and r['category'] not in (category,'other'): continue
            if r['applicable_models'] and (not device_model or device_model.upper() not in r['applicable_models']): continue
            if r['applicability_scope']=='college_specific_ling_historical' and college!='ling': continue
            if r.get('applicable_installations') and installation_id not in r['applicable_installations']:continue
            matched.append(i)
        return matched

    def rank(self,query,role='student',category=None,device_model=None,college=None,method='dense',k=3,installation_id=None):
        self.load()
        idx=self.eligible(role,category,device_model,college,installation_id)
        if not idx:return []
        if method=='hybrid':
            dense=self.rank(query,role,category,device_model,college,'dense',max(k,10),installation_id)
            keyword=self.rank(query,role,category,device_model,college,'keyword',max(k,10),installation_id)
            fusion=collections.defaultdict(float)
            for ranked in [dense,keyword]:
                for n,r in enumerate(ranked,1):
                    # A zero BM25 score contains no lexical evidence.
                    if ranked is keyword and r['score']<=0: continue
                    fusion[r['chunk_id']]+=1/(60+n)
            positions={r['chunk_id']:i for i,r in enumerate(self.records)}
            q=self.encode([query])[0]
            ordered=sorted(fusion,key=lambda cid:(-fusion[cid],cid))[:k]
            return [dict(self.records[positions[cid]],score=round(fusion[cid],6),semantic_score=round(float(self.matrix[positions[cid]]@q),6)) for cid in ordered]
        if method=='dense':
            q=self.encode([query])[0]; scores=self.matrix[idx]@q
        elif method=='keyword':
            qt=collections.Counter(tokens(query)); docs=[collections.Counter(tokens(search_text(self.records[i]))) for i in idx]
            n=len(docs); avg=sum(sum(d.values()) for d in docs)/max(n,1)
            df=collections.Counter(t for d in docs for t in d)
            scores=[]
            for d in docs:
                length=sum(d.values()); score=0.0
                for t in qt:
                    tf=d.get(t,0)
                    score+=math.log(1+(n-df[t]+.5)/(df[t]+.5))*tf*2.2/(tf+1.2*(.25+.75*length/max(avg,1)))
                scores.append(score)
        else: raise ValueError('Unknown method')
        order=sorted(range(len(idx)),key=lambda j:(-float(scores[j]),self.records[idx[j]]['chunk_id']))[:k]
        return [dict(self.records[idx[j]],score=round(float(scores[j]),6)) for j in order]

    def search(self,query,role='student',category=None,device_model=None,college=None,k=3,installation_id=None):
        if role not in ('student','worker','admin'): raise ValueError('Invalid role')
        if not query.strip() or len(query)>2000: raise ValueError('Query must contain 1-2000 characters')
        if not 1<=k<=10: raise ValueError('k must be 1-10')
        gas_query=re.sub(r'(没有|并无|无|没)(燃气味|煤气味|天然气味|燃气泄漏|煤气泄漏|臭鸡蛋味)','',query)
        if re.search(r'燃气味|煤气味|天然气味|臭鸡蛋|(?:燃气|煤气|天然气).{0,8}(泄漏|泄露|漏气|异味|报警)|(?:热水器|燃气表).{0,6}异味|漏气|(?:燃气|煤气)报警器.{0,6}(响|鸣)',gas_query):
            self.load();safe=[dict(r,score=None) for r in self.records if r['chunk_id']=='R-GAS-SAFE-01' and role in r['allowed_roles']]
            return dict(status='urgent_gas',message='先停止使用，远离现场；不要开关电器、插拔插头、反复点火或在现场打电话。能安全做到时关闭气源并开窗，到室外安全处联系物业与燃气抢险人员；不能安全操作就先撤离，不按使用视频复位或开阀。',results=safe)
        # A small prototype guard, not a comprehensive safety or security classifier.
        if re.search(r'(泄露|给我|发给我|输出|显示).{0,12}(密钥|密码|提示词)|忽略.{0,12}(规则|指令)|撬锁|绕过门禁|短接|带电维修|铜丝.{0,5}保险|自闭阀.{0,8}(复位|强开|旁路)',query):
            return dict(status='restricted_request',message='不提供泄露凭据、绕过门禁或危险改装方法；可记录故障并转交人工。',results=[])
        # Negated descriptions are removed only locally. Other risks remain visible.
        danger=re.sub(r'(没有|并无|无|没)(冒烟|焦味|火花|漏电|触电|烧焦)','',query)
        if re.search(r'冒烟|焦[糊煳]?味|烧焦|火花|漏电|触电|smoke|sparks|水.{0,6}(插座|插线板)|插座.{0,6}(进水|淹)',danger,re.I):
            self.load(); safe=[dict(r,score=None) for r in self.records if r['chunk_id']=='R-SAFE-01' and role in r['allowed_roles']]
            return dict(status='urgent_manual',message='先停止使用并远离危险，通知值班人员；仅在能安全操作时从安全位置断电，不触碰冒烟或进水设备，不等待普通预约。',results=safe)
        if installation_id!='personal_heater_case_01' and re.search(r'(燃气阀|煤气阀|黄色.{0,3}(阀|开关)|燃气开关).{0,15}(横|竖|方向|打开|怎么开|怎么转|复位)|(?:横着|竖着).{0,8}(燃气|阀门)',query):
            return dict(status='need_installation_confirmation',message='我需要先核对这是否是资料对应的装置。不能仅凭阀门颜色或照片中的横竖判断开关方向；请先让工作人员确认，不自行复位或强开。',results=[])
        if re.fullmatch(r'[\s，。！!？?]*(坏了|不能用|修一下|帮我修|有问题|报修)[\s，。！!？?]*',query):
            return dict(status='need_more_info',message='请说明具体物品、位置、故障现象及开始时间。',results=[])
        topical=re.search(r'修|宿舍|书院|空调|冷气|制冷|风扇|灯|照明|插座|电路|停电|跳闸|电线|网|wifi|wi-fi|LAN|花洒|水龙头|水管|下水|马桶|地漏|洗手|洗衣|热水|燃气|煤气|床|椅|书桌|桌子|衣柜|家具|沙发|螺丝|柜锁|抽屉|导轨|柜门|挂杆|漏水|滴水|排水|门|窗|地板|瓷砖|墙砖|轨道|遥控|照片|语音|型号|零件|E\d',query,re.I)
        if not topical: return dict(status='out_of_scope',message='本库用于宿舍设施报修，请提供相关故障。',results=[])
        results=self.rank(query,role,category,device_model,college,method='hybrid',k=k,installation_id=installation_id)
        if not results or max(r['semantic_score'] for r in results)<.52:
            return dict(status='insufficient_evidence',message='未检索到足够相关资料，请补充物品、型号与现象并转人工。',results=[])
        return dict(status='prototype_evidence',message='以下为原型参考资料及采集模板，尚未经过物业/师傅审核；不能据此确认内部故障或自动派单。',results=results)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('query',nargs='?'); p.add_argument('--build',action='store_true'); p.add_argument('--role',choices=['student','worker','admin'],default='student'); p.add_argument('--category'); p.add_argument('--device-model'); p.add_argument('--college'); p.add_argument('--installation-id');p.add_argument('--k',type=int,default=3); p.add_argument('--cache-dir')
    a=p.parse_args(); kb=KnowledgeBase(cache_dir=a.cache_dir)
    if a.build: print(json.dumps(kb.build(),ensure_ascii=False,indent=2))
    if a.query: print(json.dumps(kb.search(a.query,a.role,a.category,a.device_model,a.college,a.k,a.installation_id),ensure_ascii=False,indent=2))
    if not a.build and not a.query:p.error('provide query or --build')
