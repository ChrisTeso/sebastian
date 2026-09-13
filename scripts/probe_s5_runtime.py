#!/usr/bin/env python3
"""Harmless native-runtime probe; no channel delivery or private computer reads."""
import base64,json,struct,sys,threading,time,zlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sebastian.session_runtime import SessionRuntime
from sebastian.runtime_policy import make_runtime_policy,load_user_config

def image():
    def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    pixels=b''.join(b'\0'+b'\xff\0\0'*64 for _ in range(64))
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',64,64,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')
    return 'data:image/png;base64,'+base64.b64encode(png).decode()

def main():
    out=Path('.private/s5-runtime');out.mkdir(parents=True,exist_ok=True,mode=0o700)
    profile=make_runtime_policy('conversation',Path('/tmp'),load_user_config())
    evidence={};r=SessionRuntime(out/'sessions.json')
    try:
        r.stage_images([image()]);a=r.respond('probe-image',profile,'What single color fills this image? One word.',timeout=120)
        evidence['image_red']='red' in a.lower()
        marker='cerulean-83419'
        r.respond('continuity',profile,'Remember this synthetic test token: '+marker+'. Reply OK.',timeout=120)
        r._connection.process.kill();r._connection.process.wait()
        a=r.respond('continuity',profile,'What exact synthetic test token did I ask you to remember?',timeout=120)
        evidence['process_reconnect_continuity']=marker in a
        r.close();r=SessionRuntime(out/'sessions.json')
        a=r.respond('continuity',profile,'What exact synthetic test token did I ask you to remember?',timeout=120)
        evidence['restart_continuity']=marker in a
        r.respond('cancel',profile,'Remember this synthetic test token: '+marker+'. Reply OK.',timeout=120)
        errors=[]
        def cancel_request():
            try:r.respond('cancel',profile,'Write the integers from 1 to 20000, each on its own line.',timeout=120)
            except Exception as e:errors.append(type(e).__name__)
        worker=threading.Thread(target=cancel_request);worker.start()
        until=time.monotonic()+60
        while time.monotonic()<until and (r._connection is None or r._connection._active is None):time.sleep(.05)
        evidence['active_turn_observed']=r._connection is not None and r._connection._active is not None
        evidence['cancel_accepted']=r.cancel();worker.join(20)
        evidence['cancellation_completed']=not worker.is_alive() and errors==['RuntimeFailure']
        a=r.respond('cancel',profile,'What exact synthetic test token did I ask you to remember?',timeout=120)
        evidence['post_cancel_continuity']=marker in a
        answers=[]
        def concurrent(n):
            try:answers.append(r.respond('concurrent-'+str(n),profile,'Reply exactly READY.',timeout=120).strip()=='READY')
            except Exception:answers.append(False)
        workers=[threading.Thread(target=concurrent,args=(n,)) for n in range(2)]
        for w in workers:w.start()
        for w in workers:w.join()
        evidence['concurrent_requests_complete']=answers==[True,True]
        evidence['last_timings']=r.last_timings
    finally:
        r.close()
        target=out/'probe-results.json';target.write_text(json.dumps(evidence,indent=2));target.chmod(0o600)
        print(json.dumps(evidence,indent=2))
if __name__=='__main__':main()
