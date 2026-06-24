import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
import exec from 'k6/execution';

const BASE_URL = __ENV.BASE_URL || 'http://35.246.148.66';
const IMAGE_URL = __ENV.IMAGE_URL || 'https://voca-land.sgp1.cdn.digitaloceanspaces.com/43844/1649663961071/dc8db6b13c0558081c44b48b27e724f49c2d7742ab5974c6865d42d982409f65.jpg';
const CLIENT_PREFIX = __ENV.CLIENT_PREFIX || 'k6-client';
const THINK_TIME_SECONDS = Number(__ENV.THINK_TIME_SECONDS || '0.2');

export const tasksQueued = new Counter('tasks_queued_202');
export const tasksRejected = new Counter('tasks_rejected_429_503');
export const unexpectedResponses = new Counter('unexpected_responses');
export const acceptedRate = new Rate('accepted_rate');
export const classifyLatency = new Trend('classify_latency');

export const options = {
  scenarios: {
    create_image_tasks: {
      executor: 'ramping-arrival-rate',
      startRate: 2,
      timeUnit: '1s',
      preAllocatedVUs: 50,
      maxVUs: 200,
      stages: [
        { duration: '30s', target: 10 }, // Gehe schnell auf 10 Req/s (1 Node ist hier schon leicht überlastet)
        { duration: '1m', target: 40 },  // Vollgas auf 40 Req/s (Das überlastet selbst 3 Nodes massiv!)
        { duration: '1m', target: 40 },  // Halte die Überlast für 1 Minute
        { duration: '30s', target: 0 },  // Cooldown
      ],
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.20'],
    http_req_duration: ['p(95)<1000'],
    accepted_rate: ['rate>0.50'],
  },
};

function clientIdForVu() {
  const clientCount = Number(__ENV.CLIENTS || '5');
  const bucket = (__VU % clientCount) + 1;
  return `${CLIENT_PREFIX}-${bucket}`;
}

export default function () {
  // Idempotency Key generieren (Eindeutig für diesen Virtual User und diese Iteration)
  // manuell gültige Fake-UUID konstruiert
  const vuStr = exec.vu.idInTest.toString().padStart(8, '0');
  const iterStr = exec.vu.iterationInScenario.toString().padStart(4, '0');
  const taskId = `${vuStr}-${iterStr}-4000-8000-000000000000`;

  const payload = JSON.stringify({ 
      image_url: IMAGE_URL,
      task_id: taskId  // <-- HIER schicken wir die ID mit!
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
      'X-Client-ID': clientIdForVu(),
    },
    tags: {
      endpoint: 'POST /classify',
    },
  };

  let res;
  let retries = 3;
  let backoff = 1.0; // 1 Sekunde initialer Backoff

  // 2. Retry Loop mit Exponential Backoff & Jitter (Strategie aus dem Amazon Paper!)
  for (let i = 0; i < retries; i++) {
      res = http.post(`${BASE_URL}/classify`, payload, params);
      
      console.log(JSON.stringify({
        time: new Date().toISOString(),
        vu: __VU,
        iteration: __ITER,
        client_id: clientIdForVu(),
        method: 'POST',
        url: `${BASE_URL}/classify`,
        status: res.status,
        duration_ms: res.timings.duration,
        body: res.body,
      }));

      if (res.status === 202) {
          tasksQueued.add(1);
          acceptedRate.add(true);
          break; // Erfolg! Keine Retries mehr nötig.
      } else if (res.status === 429 || res.status === 503) {
          tasksRejected.add(1);
          acceptedRate.add(false);
          
          // Exponential Backoff + Jitter (Zufallswert zwischen 0 und 0.5 Sekunden)
          const jitter = Math.random() * 0.5;
          console.log(`[VU ${exec.vu.idInTest}] Load Shedding (Status ${res.status})! Retrying in ${backoff + jitter}s...`);
          sleep(backoff + jitter);
          
          backoff *= 2; // Verdopple die Wartezeit für den nächsten Versuch (Exponential)
      } else {
          unexpectedResponses.add(1);
          acceptedRate.add(false);
          break; // Anderer Fehler (z.B. 404, 500), hier retrien wir nicht.
      }
  }

  classifyLatency.add(res.timings.duration);
}