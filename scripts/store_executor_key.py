#!/usr/bin/env python3
"""One-use loopback form to store an executor key without tool-output disclosure."""
import http.server, os, pathlib, secrets, threading

ROOT=pathlib.Path(__file__).resolve().parents[1]
DEST=ROOT/'.private/probes/executor-key'
os.umask(0o077)
route='/'+secrets.token_urlsafe(32)
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path!=route:self.send_error(404);return
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Security-Policy',"default-src 'none'; form-action 'self'; frame-ancestors 'none'");self.end_headers()
        self.wfile.write(b'<!doctype html><title>Store Sebastian executor key</title><h1>Store restricted executor key locally</h1><p>The key is saved in an ignored owner-only local file. It is never printed.</p><form method="post"><label>Executor key <input type="password" name="key" autocomplete="off" required></label><button>Store key locally</button></form>')
    def do_POST(self):
        from urllib.parse import parse_qs
        origin='http://'+self.headers.get('Host','')
        if self.path!=route or self.headers.get('Origin')!=origin or self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':self.send_error(403);return
        n=int(self.headers.get('Content-Length','0'))
        if n<1 or n>10000:self.send_error(400);return
        body=parse_qs(self.rfile.read(n).decode());value=body.get('key',[''])[0].strip()
        if not value.startswith('sk-') or len(value)<30:self.send_error(400,'Expected an OpenAI secret key');return
        DEST.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(DEST,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(value+'\n')
        self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(b'<title>Key stored</title><p>Restricted executor key stored locally. No key displayed.</p>')
        print('Restricted executor key saved with mode 0600.',flush=True)
        threading.Thread(target=self.server.shutdown,daemon=True).start()
if __name__=='__main__':
    with http.server.HTTPServer(('127.0.0.1',0),Handler) as server:
        print(f'http://127.0.0.1:{server.server_port}{route}',flush=True)
        timer=threading.Timer(600,server.shutdown);timer.daemon=True;timer.start()
        try:server.serve_forever()
        finally:timer.cancel()
