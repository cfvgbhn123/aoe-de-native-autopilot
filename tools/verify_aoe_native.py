"""Bounded live takeover verification; restores human control in finally."""
import sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aoe_autopilot.backend import Controller

if __name__=='__main__':
    c=Controller()
    rows=[]
    try:
        c.connect();rows.append({'before':c.status()});print(json.dumps(rows[-1]),flush=True)
        rows.append({'on':c.on()});print(json.dumps(rows[-1]),flush=True)
        for n in range(10):
            time.sleep(1);c.tick();rows.append({'sample':c.status()});print(json.dumps(rows[-1]),flush=True)
        rows.append({'off':c.off()});print(json.dumps(rows[-1]),flush=True)
        rows.append({'after':c.status()});print(json.dumps(rows[-1]),flush=True)
    finally:
        c.close()
        out=Path(__file__).resolve().parents[1]/'aoe_ai_logs'/'live_verification.json'
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
