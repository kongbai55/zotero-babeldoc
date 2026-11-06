"""Offline real-engine check: one blank answer cannot remove the source paragraph."""
from pathlib import Path
import asyncio
import io
import json
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'addon/content'))
import worker
import pymupdf
import httpx
import openai
from babeldoc.translator import translator as module
from babeldoc.format.pdf import translation_config as config_module

def main():
    out=ROOT/'.tmp/partial-engine';out.mkdir(parents=True,exist_ok=True)
    source=out/'fixture-source.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page()
        page.insert_textbox((60,100,540,180),'Failfixture This paragraph must remain readable in its original language when the translation provider returns an empty answer.',fontsize=12)
        page.insert_textbox((60,240,540,330),'Successfixture This separate paragraph should still be translated even after another paragraph has failed.',fontsize=12)
        doc.save(source)
    output=out/'output';output.mkdir(exist_ok=True)
    calls=[]
    def respond(request):
        body=json.loads(request.content)
        prompt=body['messages'][-1]['content']
        calls.append('batch' if body.get('response_format') else 'single')
        # Find only the final actual JSON input list, never the template examples.
        batches=[]
        decoder=json.JSONDecoder()
        for index,ch in enumerate(prompt):
            if ch!='[':continue
            try:value,_=decoder.raw_decode(prompt[index:])
            except ValueError:continue
            if isinstance(value,list) and value and all(isinstance(x,dict) and 'id' in x and 'input' in x for x in value):batches.append(value)
        if batches:
            content=json.dumps([{'id':x['id'],'output':'' if 'Failfixture' in x['input'] else '这一个段落已经成功翻译，前面段落失败不会阻止后面的翻译。'} for x in batches[-1]],ensure_ascii=False)
        else:
            # Single-paragraph fallback must also fail, so the engine retains source.
            actual=prompt.rsplit('Now translate the following text:',1)[-1]
            content='' if 'Failfixture' in actual else '这一个段落已经成功翻译，前面段落失败不会阻止后面的翻译。'
        return httpx.Response(200,request=request,json={'id':'offline-fixture','object':'chat.completion','model':'fixture','choices':[{'index':0,'message':{'role':'assistant','content':content},'finish_reason':'stop'}]})
    
    real_translator=module.OpenAITranslator
    def translator(**kwargs):
        obj=real_translator(**kwargs);obj.ignore_cache=True;obj.client.close()
        obj.client=openai.OpenAI(api_key='synthetic-unused',base_url='http://offline.fixture/v1',http_client=httpx.Client(transport=httpx.MockTransport(respond)),max_retries=0)
        return obj
    real_config=config_module.TranslationConfig
    def config(**kwargs):
        kwargs.update(auto_extract_glossary=False,pool_max_workers=1)
        return real_config(**kwargs)
    protocol=io.StringIO()
    request={'input':str(source),'output':str(output),'backend':'api','baseURL':'http://offline.fixture/v1','model':'partial-fixture-024','langIn':'en','langOut':'zh','qps':20}
    with patch.object(module,'OpenAITranslator',translator),patch.object(config_module,'TranslationConfig',config),patch.object(worker,'PROTOCOL',protocol):
        asyncio.run(worker.translate(request))
    events=[json.loads(line) for line in protocol.getvalue().splitlines()]
    result=events[-1]
    assert result['type']=='result',result
    with pymupdf.open(result['mono']) as mono:
        text=''.join(page.get_text() for page in mono)
        assert 'Failfixture' in text,'Untranslated original was lost'
        assert '成功翻译' in text,'Later paragraph was not translated'
        assert any(page.first_annot for page in mono),'Partial result has no review comment'
        mono[0].get_pixmap(matrix=pymupdf.Matrix(1.4,1.4)).save(out/'mono-preview.png')
    with pymupdf.open(result['dual']) as dual:
        dual[0].get_pixmap(matrix=pymupdf.Matrix(1,1)).save(out/'dual-preview.png')
    print(json.dumps({'calls':len(calls),'warnings':result['warnings'],'mono':result['mono'],'dual':result['dual']},ensure_ascii=False))

if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
