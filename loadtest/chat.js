// k6 test of concurrent questions: POST /api/v1/chat with N users asking at the same time.
// On a CPU-only model host generation is the bottleneck, so this measures queueing, not
// throughput of the API.
//
//   k6 run -e BASE_URL=http://web:8080 -e EMAIL=agent@larkspur.example -e WORKSPACE=larkspur \
//          -e PASSWORD_FILE=/run/secrets/eval_password -e VUS=3 -e ITERATIONS=6 chat.js
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE_URL || "http://web:8080";
const PASSWORD = open(__ENV.PASSWORD_FILE).trim();

export const options = {
  scenarios: {
    chat: {
      executor: "shared-iterations",
      vus: Number(__ENV.VUS || 3),
      iterations: Number(__ENV.ITERATIONS || 6),
      maxDuration: "40m",
    },
  },
  // Listed so k6 reports the chat requests on their own, without the setup login.
  thresholds: { "http_req_duration{name:chat}": ["max>=0"] },
  summaryTrendStats: ["min", "med", "avg", "p(90)", "p(95)", "max"],
};

const QUESTIONS = [
  "How many days do I have to return an item?",
  "What is the nightly hotel limit in Seattle?",
  "How often should I re-apply seam sealer to my tent?",
  "How much does Summit Care cost?",
  "What is the minimum password length?",
  "How long is an online pickup order held at the store?",
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
  const question = QUESTIONS[__ITER % QUESTIONS.length];
  const res = http.post(`${BASE}/api/v1/chat`, JSON.stringify({ question }), {
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${data.token}` },
    tags: { name: "chat" },
    timeout: "600s",
  });
  check(res, {
    "status 200": (r) => r.status === 200,
    "answered with a citation": (r) => r.status === 200 && r.json("answer.citations").length > 0,
  });
}
