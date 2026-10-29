// Standalone trigger harness (fallback for `skill_eval`).
//
// Why this exists: `skill_eval` treats any non-zero `opencode run` exit as an
// error, and a child run that solves a task can end non-zero for unrelated
// reasons (a tool failure after the skill was already loaded). This harness
// runs the same experiment but scores a run as a success as soon as the
// synthetic skill file is opened, regardless of the child's exit code.
//
// Usage:
//   node trigger_harness.js [evalSet.json] [runsPerQuery] [concurrency] [timeoutMs] [out.json]
//
// Run it from a project root that does NOT have this skill installed, otherwise
// the synthetic skill competes with the real one for triggers.

const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");

const SKILL_DIR = path.resolve(__dirname, "..");
const EVAL_SET = process.argv[2] || path.join(SKILL_DIR, "evals", "trigger_eval_set.json");
const RUNS = Number(process.argv[3] || 2);
const CONCURRENCY = Number(process.argv[4] || 6);
const TIMEOUT_MS = Number(process.argv[5] || 90000);
const OUT = process.argv[6] || path.join(process.cwd(), "trigger-results.json");

function readDescription() {
  const raw = fs.readFileSync(path.join(SKILL_DIR, "SKILL.md"), "utf8");
  const m = raw.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!m) throw new Error("no frontmatter in SKILL.md");
  const dm = m[1].match(/^description:\s*([\s\S]*)$/m);
  if (!dm) throw new Error("no description in frontmatter");
  return dm[1].replace(/\r?\n[a-zA-Z_]+:[\s\S]*$/, "").trim();
}

function runOnce(query, description, skillName) {
  return new Promise((resolve) => {
    const cleanName = `${skillName}-skill-${crypto.randomBytes(4).toString("hex")}`;
    const evalRoot = fs.mkdtempSync(path.join(os.tmpdir(), "psa-trigger-"));
    const dir = path.join(evalRoot, ".opencode", "skills", cleanName);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(
      path.join(dir, "SKILL.md"),
      ["---", `name: ${cleanName}`, "description: |",
        ...description.split("\n").map((line) => `  ${line}`),
        "---", "", "# synthetic", ""].join("\n")
    );

    const started = Date.now();
    const proc = spawn("opencode", ["run", "--format", "json", "--agent", "build", query], {
      cwd: evalRoot,
      env: { ...process.env, PWD: evalRoot },
      stdio: ["ignore", "pipe", "pipe"],
    });

    let buf = "";
    let triggered = false;
    let stderr = "";
    let settled = false;
    const finish = (extra) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { proc.kill(); } catch {}
      try { fs.rmSync(evalRoot, { recursive: true, force: true }); } catch {}
      resolve({ triggered, ms: Date.now() - started, stderr: stderr.slice(-400), ...extra });
    };
    const timer = setTimeout(() => finish({ timeout: true }), TIMEOUT_MS);

    proc.stdout.on("data", (chunk) => {
      buf += chunk.toString();
      let idx;
      while ((idx = buf.indexOf("\n")) !== -1) {
        const line = buf.slice(0, idx);
        buf = buf.slice(idx + 1);
        if (!line.trim()) continue;
        try {
          const event = JSON.parse(line);
          if (event.type !== "tool_use") continue;
          const part = event.part || {};
          if (part.tool !== "skill" && part.tool !== "read") continue;
          if (JSON.stringify(part).includes(cleanName)) {
            triggered = true;
            finish({});
            return;
          }
        } catch {}
      }
    });
    proc.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    proc.on("error", (error) => finish({ spawnError: String(error && error.code) }));
    proc.on("close", (code) => finish({ exitCode: code }));
  });
}

async function main() {
  const description = readDescription();
  const skillName = JSON.parse(fs.readFileSync(path.join(SKILL_DIR, "evals", "evals.json"), "utf8")).skill_name;
  const set = JSON.parse(fs.readFileSync(EVAL_SET, "utf8"));

  const jobs = [];
  for (const item of set) {
    for (let i = 0; i < RUNS; i++) jobs.push({ item });
  }
  const buckets = new Map(set.map((_, idx) => [idx, { triggers: 0, runs: 0, timeouts: 0 }]));
  let cursor = 0;

  async function worker() {
    while (cursor < jobs.length) {
      const job = jobs[cursor++];
      const idx = set.indexOf(job.item);
      const result = await runOnce(job.item.query, description, skillName);
      const bucket = buckets.get(idx);
      bucket.runs += 1;
      if (result.triggered) bucket.triggers += 1;
      if (result.timeout) bucket.timeouts += 1;
      console.log(`[${result.triggered ? "TRIGGER" : "no"}] ${job.item.query} (${result.ms}ms)`);
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, jobs.length) }, worker));

  const results = set.map((item, idx) => {
    const bucket = buckets.get(idx);
    const rate = bucket.runs ? bucket.triggers / bucket.runs : 0;
    return {
      query: item.query,
      should_trigger: item.should_trigger,
      trigger_rate: rate,
      runs: bucket.runs,
      triggers: bucket.triggers,
      timeouts: bucket.timeouts,
      pass: item.should_trigger ? rate >= 0.5 : rate < 0.5,
    };
  });
  const summary = {
    total: results.length,
    passed: results.filter((r) => r.pass).length,
    failed: results.filter((r) => !r.pass).length,
  };
  fs.writeFileSync(OUT, JSON.stringify({ description, results, summary }, null, 2), "utf8");
  console.log("SUMMARY", JSON.stringify(summary));
  for (const r of results.filter((x) => !x.pass)) {
    console.log("FAIL", r.query, "rate=", r.trigger_rate, "expected", r.should_trigger);
  }
}

main();
