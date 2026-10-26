"""我用这个脚本提取PDF文字，并保留页码和待审核候选块。
文字提取依赖pypdf、pillow；macOS本地OCR另用pyobjc-framework-Vision。
我不在这里上传资料，也不自动将候选块放入活动知识库。
"""
import argparse,io,json
from pathlib import Path
from pypdf import PdfReader

def ocr_image(image):
    import Foundation,Vision
    im=image.copy().convert('RGB');im.thumbnail((2200,2200))
    b=io.BytesIO();im.save(b,format='PNG');raw=b.getvalue()
    data=Foundation.NSData.dataWithBytes_length_(raw,len(raw))
    req=Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(['zh-Hans','en-US']);req.setUsesLanguageCorrection_(False)
    handler=Vision.VNImageRequestHandler.alloc().initWithData_options_(data,{})
    ok,err=handler.performRequests_error_([req],None)
    if not ok:raise RuntimeError(str(err))
    obs=sorted(req.results() or [],key=lambda o:(-round(o.boundingBox().origin.y,2),o.boundingBox().origin.x))
    return '\n'.join(str(o.topCandidates_(1)[0].string()) for o in obs if o.topCandidates_(1))

def main():
    p=argparse.ArgumentParser();p.add_argument('pdf');p.add_argument('output_dir');p.add_argument('--source-id',required=True);p.add_argument('--ocr',action='store_true');a=p.parse_args()
    out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True);records=[];chunks=[]
    for n,page in enumerate(PdfReader(a.pdf).pages,1):
        text=page.extract_text() or '';method='pdf_text'
        if a.ocr and len(text.strip())<30:
            imgs=list(page.images)
            if imgs:text=ocr_image(max((i.image for i in imgs),key=lambda i:i.width*i.height));method='apple_vision'
        r=dict(source_id=a.source_id,pdf_page=n,text=text,method=method,active_index=False,review_status='machine_extracted_not_proofread',rights='user_supplied_permission_unverified')
        records.append(r)
        for start in range(0,len(text),650):
            if len(text[start:start+800].strip())>=30:chunks.append(dict(r,chunk_id=f'{a.source_id}-p{n:03d}-{start//650:02d}',text=text[start:start+800]))
        if n%10==0:print('Processed',n,flush=True)
    for name,rows in [('pages',records),('candidate_chunks',chunks)]:
        (out/(a.source_id+'_'+name+'.jsonl')).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    (out/(a.source_id+'_text.txt')).write_text('\n\n'.join(f'=== PDF页{r["pdf_page"]} ===\n'+r['text'] for r in records),encoding='utf-8')
    print('Candidates only; manually review text, diagrams, tables, units and usage rights before indexing.')
if __name__=='__main__':main()
