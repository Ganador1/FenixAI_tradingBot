import glob, json
try:
    log = max(glob.glob("logs/live_slot_events_dreamteam_r19_BTCUSDT_15m_*.jsonl"))
    counts = {}
    for line in open(log):
        obj = json.loads(line)
        if obj.get("event_type") == "cycle_end":
            for agent, res in obj.get("metadata", {}).get("agent_outputs", {}).items():
                if agent not in counts: counts[agent] = {"S":{}, "C":0, "N":0}
                sig = res.get("signal")
                counts[agent]["S"][sig] = counts[agent]["S"].get(sig, 0) + 1
                counts[agent]["C"] += float(res.get("confidence", 0))
                counts[agent]["N"] += 1
    for a, s in counts.items():
        print(f"{a}: {s['S']} (Conf: {s['C']/s['N']:.2f})")
except Exception as e: print(e)
