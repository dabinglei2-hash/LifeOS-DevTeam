import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lads_automation.project_registry import main
import json
if __name__=="__main__":
 try: print(json.dumps(main(),ensure_ascii=True,sort_keys=True))
 except Exception as exc:
  print(json.dumps({"status":"BLOCKED","error_type":type(exc).__name__}),file=sys.stderr)
  raise SystemExit(2)
