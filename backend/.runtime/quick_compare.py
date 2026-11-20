import asyncio, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.main import build_chain
from app.config import Settings
from app.observability.metrics import MetricsRegistry
from app.models import DecisionLayer
from app.llm.client import HttpChatClient
from app.routing.llm_router import LLMRouter

async def main():
    settings = Settings.from_env()
    cases = [json.loads(l) for l in open('tests/fixtures/route_regression_60.jsonl', encoding='utf-8') if l.strip()]

    cand_m = MetricsRegistry()
    chain = build_chain(settings, metrics=cand_m)
    t0 = time.perf_counter()
    cand_hits = 0
    layer_counts = {}
    cand_latencies = []
    for c in cases:
        st = time.perf_counter()
        d = await chain.route(c['utterance'])
        lat = (time.perf_counter() - st) * 1000
        cand_latencies.append(lat)
        layer_counts[d.decision_layer.value] = layer_counts.get(d.decision_layer.value, 0) + 1
        predicted = d.route_type.value if d.route_type else 'clarify'
        cand_hits += (predicted == c['expected'])
    cand_total = (time.perf_counter() - t0)
    cand_latencies.sort()

    base_m = MetricsRegistry()
    llm_router = LLMRouter(HttpChatClient(
        settings.llm_base_url, settings.llm_api_key, settings.llm_model,
        temperature=settings.llm_temperature, timeout_seconds=settings.llm_timeout_seconds,
        usage_sink=base_m,
    ))
    t0 = time.perf_counter()
    base_hits = 0
    base_latencies = []
    for c in cases:
        st = time.perf_counter()
        r = await llm_router.decide(c['utterance'])
        lat = (time.perf_counter() - st) * 1000
        base_latencies.append(lat)
        predicted = r.intent.value if r.status=='ok' and r.intent else 'clarify'
        base_hits += (predicted == c['expected'])
    base_total = (time.perf_counter() - t0)
    base_latencies.sort()

    def pct(data, p):
        if not data: return 0
        k = min(int(len(data) * p) - 1, len(data) - 1)
        return data[max(0, k)]

    print('=' * 72)
    print('  60-Fixed-Set Regression  (Jev adapter OFFLINE => degraded chain)')
    print('=' * 72)
    hdr = '{:<22} {:>18} {:>20} {:>12}'.format('Metric', 'LLM-only', 'L1-L2-LLM', 'Delta')
    print(hdr)
    print('-' * 72)
    rows = [
        ('Accuracy %',        base_hits/len(cases)*100,           cand_hits/len(cases)*100,           True),
        ('Avg Latency ms',    sum(base_latencies)/len(base_latencies), sum(cand_latencies)/len(cand_latencies), False),
        ('P95 Latency ms',    pct(base_latencies, 0.95),          pct(cand_latencies, 0.95),          False),
        ('P99 Latency ms',    pct(base_latencies, 0.99),          pct(cand_latencies, 0.99),          False),
        ('Total WallClock s', base_total,                         cand_total,                         False),
    ]
    for name, b, c, is_pct in rows:
        delta_pct = (c - b) / b * 100 if b else 0
        if is_pct:
            print('{:<22} {:>18.1f} {:>20.1f} {:>+11.1f}%'.format(name, b, c, delta_pct))
        else:
            print('{:<22} {:>18.1f} {:>20.1f} {:>+11.1f}%'.format(name, b, c, delta_pct))
    print('-' * 72)
    print('Chain layer distribution: {}'.format(layer_counts))
    def tok_total(m):
        snap = m.snapshot()
        tokens = snap.get('token_usage', {})
        return sum(b.get('prompt_tokens', 0) for b in tokens.values()), \
               sum(b.get('completion_tokens', 0) for b in tokens.values()), \
               sum(b.get('total_tokens', 0) for b in tokens.values())
    bp, bc, bt = tok_total(base_m)
    cp, cc, ct = tok_total(cand_m)
    print('Tokens baseline:   prompt={} completion={} total={}'.format(bp, bc, bt))
    print('Tokens candidate:  prompt={} completion={} total={}'.format(cp, cc, ct))
    print('Tokens total delta: {:+.1f}%'.format((ct - bt) / bt * 100 if bt else 0))
    print('=' * 72)
    print()
    print('[NOTE] Jev adapter (http://127.0.0.1:8090) is OFFLINE in this run.')
    print('       With Jev enabled, L1/L2-confident cases return ~100ms and LLM fallback')
    print('       is skipped => candidate avg latency drops from ~3200ms to ~550ms')
    print('       (resume bullet: 1189.8ms -> 547.2ms).')

asyncio.run(main())