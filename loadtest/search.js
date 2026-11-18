// k6 load test of the retrieval path: POST /api/v1/search (query embedding + hybrid SQL search).
//
//   k6 run -e BASE_URL=http://web:8080 -e EMAIL=agent@larkspur.example -e WORKSPACE=larkspur \
//          -e PASSWORD_FILE=/run/secrets/eval_password -e VUS=10 -e DURATION=60s search.js
//
// One account is used by every virtual user, so the per-user search rate limit
// (3 x RAG_RATE_LIMIT_CHAT_PER_MINUTE) must be raised for this test; see docs/evaluation.
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE_URL || "http://web:8080";
const PASSWORD = open(__ENV.PASSWORD_FILE).trim();

export const options = {
  scenarios: {
    search: {
      executor: "constant-vus",
      vus: Number(__ENV.VUS || 5),
      duration: __ENV.DURATION || "60s",
    },
  },
  // The tagged threshold also makes k6 report the search requests without the setup login.
  thresholds: { http_req_failed: ["rate<0.01"], "http_req_duration{name:search}": ["p(95)<5000"] },
  summaryTrendStats: ["min", "med", "avg", "p(90)", "p(95)", "p(99)", "max"],
};

const QUESTIONS = [
  "How many days do I have to return an item?",
  "What is the hotel limit in Seattle?",
  "How do I wash a down sleeping bag?",
  "When are performance reviews held?",
  "What does Summit Care cost?",
  "How long is an online pickup order held?",
  "What is the mileage reimbursement rate?",
  "Which items are final sale?",
  "How far in advance can I reserve rental gear?",
  "What second factor do finance staff use?",
];

export function setup() {
  const res = http.post(
    `${BASE}/api/v1/auth/login`,
    JSON.stringify({ email: __ENV.EMAIL, password: PASSWORD, workspace: __ENV.WORKSPACE }),
    { headers: { "Content-Type": "application/json" }, tags: { name: "login" } },
  );
  check(res, { "login 200": (r) => r.status === 200 });
  return { token: res.json("access_token") };
}

export default function (data) {
  const query = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
  const res = http.post(`${BASE}/api/v1/search`, JSON.stringify({ query, top_k: 5 }), {
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${data.token}` },
    tags: { name: "search" },
  });
  check(res, {
    "status 200": (r) => r.status === 200,
    "has hits": (r) => r.status === 200 && r.json("hits").length > 0,
  });
}
