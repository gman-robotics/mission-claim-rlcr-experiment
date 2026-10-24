import json,glob,os,collections
import sys
R=sys.argv[1] if len(sys.argv)>1 else '/Users/Shared/gman-sandbox/mission-claim-rlcr/runs/'
rows=collections.defaultdict(list)
for d in sorted(glob.glob(R+'[pd]*-agent-20261024T19[2-9]*')+glob.glob(R+'*-agent-20261024T2*')):
    if os.path.basename(d) < 'z' and d.split('-agent-')[1] < '20261024T192500Z': continue
    try: r=json.load(open(d+'/result.json'))
    except Exception: continue
    if r.get('layout')!='team2': continue
    s=r.get('llm_stats',{})
    rows[r['instance']].append((r['status'],r['timings'].get('total_sec'),s.get('valid_tool_calls',0),s.get('invalid_tool_calls',0),s.get('cost_usd') or 0,s.get('rate_limit_hits',0),r.get('guard_stop'),s.get('stop_reason'),s.get('handoffs'),s.get('forced_handoffs'),os.path.basename(d)))
tot=[0,0,0,0,0,0,0]
for k,v in rows.items():
    res=sum(x[0]=='resolved' for x in v); vt=sum(x[2] for x in v); it=sum(x[3] for x in v)
    wall=[x[1] or 0 for x in v]
    print(f"| {k} | {res}/{len(v)} | {sum(wall)/len(v):.0f}s (max {max(wall):.0f}) | {vt}/{vt+it} = {vt/max(1,vt+it):.3f} | ${sum(x[4] for x in v):.4f} | {sum(x[5] for x in v)} | {[x[6] for x in v if x[6]]} | {[x[7] for x in v]} |")
    tot=[tot[0]+res,tot[1]+len(v),tot[2]+sum(wall),tot[3]+vt,tot[4]+it,tot[5]+sum(x[4] for x in v),tot[6]+sum(x[5] for x in v)]
print("TOTAL",tot)
