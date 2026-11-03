"""Lance l'application locale avec le meme Python que le moteur OCR."""
import argparse
import threading
import sys
import webbrowser
from pathlib import Path
import uvicorn

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--sans-navigateur',action='store_true')
    args=parser.parse_args()
    if not args.sans_navigateur:
        threading.Timer(2,lambda:webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    uvicorn.run('app:app',host='127.0.0.1',port=args.port)
