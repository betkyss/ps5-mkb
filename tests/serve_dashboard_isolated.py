"""Loopback-only preview with fake physical ports. Never starts or controls Chiaki."""
import argparse
import json
import threading
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dashboard.schema import loads, Problem, require
from dashboard.store import Store
from dashboard.service import Service
from dashboard.server import Server, Handler
from test_dashboard import Fake, BASE, PRESET

class PreviewFake(Fake):
    def __init__(self):
        super().__init__();self.capture='off';self.mode='ready';self.current=PRESET.copy()
        self.client_present=True;self.response_mode='ready';self.feedback='unknown'
    def status(self):
        value=super().status()
        value.update(adapter='native',running=True,healthy=True,cleanupFinished=self.capture=='off',
                     permission='allowed',device='fixture',protocolVersion=2,pid=999999,
                     busy=self.mode=='busy',reason='Isolated fake physical ports. No PS5 or OS input.')
        value.update(stateFeedbackHealth=self.feedback,stateFeedbackAttemptAgeMs=5,ticks=112,skipped=2)
        if self.mode=='stale':value['sampleAgeMs']=5000
        if not self.client_present:value.update(processAlive=False,connected=False,running=False)
        return value
    def control(self,command,expected=None,operation_id=None,cancelled=None):
        return super().control(command,expected,operation_id,cancelled)

    def launch(self, *args, **kwargs):
        self.client_present=True
        return super().launch(*args, **kwargs)

class PreviewHandler(Handler):
    def do_GET(self):
        if self.path not in ('/', '/app.js', '/style.css'):return super().do_GET()
        try:
            self.security()
            name={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[self.path]
            kind={'/':'text/html','/app.js':'text/javascript','/style.css':'text/css'}[self.path]
            self.send(200,(self.server.preview_assets/name).read_bytes(),kind+'; charset=utf-8')
        except Problem as exc:self.send(exc.status,{'error':str(exc)})
        except OSError:self.send(503,{'error':'Служба не смогла прочитать данные'})

    def send(self, status, value, kind='application/json; charset=utf-8'):
        if status==200 and self.path in ('/api/arm','/api/release','/api/launch') and self.server.service.adapter.response_mode=='pending':
            time.sleep(getattr(self.server,'preview_delay',3))
        return super().send(status,value,kind)

    def do_POST(self):
        try:
            if self.path in ('/api/arm','/api/launch') and self.server.service.adapter.response_mode=='failure':
                self.security(True)
                self.send(503,{'error':'Тестовый отказ. Команда не выполнена.'})
                return
            if self.path!='/_fixture':return super().do_POST()
            self.security(True)
            data=loads(self.rfile.read(int(self.headers['Content-Length'])))
            require(isinstance(data,dict),'Нужен объект fixture')
            mode=data.get('mode','ready');require(mode in ('ready','busy','stale','restart','other-editor','pending','failure','absent'),'Неизвестный режим fixture')
            adapter=self.server.service.adapter
            if 'feedback' in data:
                require(data['feedback'] in ('unknown','ok','error'),'Invalid feedback fixture')
                adapter.feedback=data['feedback']
            response_mode=data.get('responseMode',mode if mode in ('pending','failure') else 'ready')
            require(response_mode in ('ready','pending','failure'),'Неизвестный режим ответа')
            require('clientPresent' not in data or type(data['clientPresent']) is bool,'clientPresent должен быть boolean')
            adapter.response_mode=response_mode
            if 'clientPresent' in data:adapter.client_present=data['clientPresent']
            elif mode=='absent':adapter.client_present=False
            if mode=='restart':
                import secrets
                self.server.csrf=secrets.token_urlsafe(32)
                self.server.service.instance='preview-restart'
            elif mode=='other-editor':
                store=self.server.service.store.snapshot();store['profiles']['second']=dict(PRESET,name='Other editor')
                store['selected']='second';self.server.service._commit(store)
            else:adapter.mode=mode if mode in ('busy','stale') else 'ready'
            self.server.service.sampled=0
            self.send(200,{'ok':True})
        except Problem as exc:self.send(exc.status,{'error':str(exc)})
        except (OSError,TimeoutError):self.send(503,{'error':'Ошибка ввода/вывода или timeout'})

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--assets',choices=('web-isolated',),default='web-isolated');args=parser.parse_args()
    directory=args.directory.resolve()
    own=(Path(__file__).resolve().parents[1]/'build/preview').resolve()
    if not directory.is_relative_to(own):parser.error('Use the isolated build directory')
    service=Service(Store(directory,PRESET,BASE),PreviewFake())
    server=Server(('127.0.0.1',0),service);server.RequestHandlerClass=PreviewHandler
    server.preview_assets=Path(__file__).resolve().parents[1]/'dashboard'/args.assets
    (directory/'preview-port').write_text(str(server.server_address[1]))
    print('http://127.0.0.1:'+str(server.server_address[1]),flush=True)
    server.serve_forever()
if __name__=='__main__':main()
