import importlib.util
import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "viz" / "investigate.html"
INPUT_MODULE_PATH = ROOT / "bin" / "build_operator_assistant_input.py"
OUTPUT_MODULE_PATH = ROOT / "bin" / "build_operator_assistant_output.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def page_script():
    html = HTML_PATH.read_text()
    start = html.index("<script>") + len("<script>")
    end = html.index("</script>", start)
    script = html[start:end]
    return script.replace(
        textwrap.dedent(
            """
            main().catch(err => {
              const status = document.getElementById("status");
              status.classList.add("error");
              status.textContent = investigationLoadErrorMessage(err);
            });
            """
        ).strip(),
        "",
    )


class InvestigationOperatorAssistantContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = page_script()
        cls.input_module = load_module(INPUT_MODULE_PATH, "build_operator_assistant_input")
        cls.output_module = load_module(OUTPUT_MODULE_PATH, "build_operator_assistant_output")

    def run_node(self, body):
        script = f"""
globalThis.window = {{location: {{protocol: "http:"}}, search: ""}};
const __bootstrapElements = new Map();
globalThis.document = {{
  getElementById(id) {{
    if (!__bootstrapElements.has(id)) {{
      __bootstrapElements.set(id, {{
        innerHTML: "",
        textContent: "",
        style: {{}},
        classList: {{
          add() {{}},
          remove() {{}},
          contains() {{ return false; }},
        }},
      }});
    }}
    return __bootstrapElements.get(id);
  }},
}};
globalThis.fetch = async () => ({{ok: false, status: 404, json: async () => ({{}})}});
{self.script}
{body}
"""
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            self.fail(f"Node script failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        return result.stdout.strip()

    def investigation_payload(self):
        return {
            "schema_version": 1,
            "generated_at": "2026-07-06T23:16:14+00:00",
            "id": "investigation-1",
            "status": "available",
            "requested_window": {
                "start": "2026-07-06T22:59:19+00:00",
                "end": "2026-07-06T23:14:19+00:00",
                "duration_minutes": 15.0,
            },
            "periods": {
                "during": {
                    "total_samples": 97,
                    "wan": {
                        "target_groups": {
                            "internet_probe": {"sample_count": 39, "raw_bad_count": 0, "sustained_bad_count": 0},
                            "resolver_probe": {"sample_count": 38, "raw_bad_count": 8, "sustained_bad_count": 2},
                        },
                    },
                    "lan": {
                        "sample_count": 20,
                        "elevated_p95_count": 0,
                        "max_p95_ms": 109.4,
                        "max_loss_pct": 0.0,
                        "target_groups": {
                            "gateway_probe": {"sample_count": 20},
                        },
                    },
                    "wan_buckets": [
                        {
                            "target_class": "internet_probe",
                            "max_p95_ms": 114.1,
                            "max_loss_pct": 0.0,
                        },
                        {
                            "target_class": "resolver_probe",
                            "max_p95_ms": 173.6,
                            "max_loss_pct": 0.0,
                        },
                    ],
                },
                "after": {"total_samples": 12},
            },
            "observation_references": [
                {
                    "id": "obs-window",
                    "type": "attribution",
                    "scope": {"view": "window_attribution"},
                    "interval": {
                        "start": "2026-07-06T07:00:01+00:00",
                        "end": "2026-07-06T23:14:19+00:00",
                    },
                    "state": {"status": "inconclusive", "label": "Inconclusive"},
                },
                {
                    "id": "obs-current",
                    "type": "attribution",
                    "scope": {"view": "current_attribution"},
                    "interval": {
                        "start": "2026-07-06T23:01:03+00:00",
                        "end": "2026-07-06T23:16:03+00:00",
                    },
                    "state": {
                        "status": "likely_upstream",
                        "label": "Likely upstream (ISP / path)",
                    },
                },
                {
                    "id": "obs-episode",
                    "type": "episode",
                    "scope": {"view": "episode", "target_class": "resolver_probe"},
                    "interval": {
                        "start": "2026-07-06T23:07:18+00:00",
                        "end": "2026-07-06T23:08:42+00:00",
                    },
                    "state": {
                        "status": "sustained_degradation",
                        "label": "Sustained degradation",
                    },
                },
            ],
            "dns_context": {
                "available": True,
                "status": "ok",
                "window": "-24h",
                "summary": {
                    "total_queries": 189448,
                    "blocked_queries": 7217,
                    "block_rate_pct": 3.8,
                },
            },
            "internet_conditions_context": {
                "available": True,
                "status": "normal",
                "summary": "No United States Internet outages or traffic anomalies detected.",
                "provider_display_name": "US Radar",
                "fallback_used": False,
            },
            "power_infrastructure_context": {
                "available": True,
                "status": "normal",
                "summary": "No APS outages or PSPS events reported.",
            },
            "notes": [
                "Prime Observer investigation output is factual telemetry evidence, not interpretation.",
            ],
        }

    def browser_hash(self, payload):
        body = f"""
globalThis.crypto = require("node:crypto").webcrypto;
(async () => {{
  const value = await operatorAssistantInputHashForInvestigation({json.dumps(payload)});
  console.log(value);
}})().catch(err => {{
  console.error(err);
  process.exit(1);
}});
"""
        return self.run_node(body)

    def dom_harness(self, review_js, current_hash_js):
        return f"""
const elements = new Map();
function makeElement() {{
  return {{
    innerHTML: "",
    textContent: "",
    classList: {{
      values: new Set(),
      add(value) {{ this.values.add(value); }},
      remove(value) {{ this.values.delete(value); }},
      contains(value) {{ return this.values.has(value); }},
    }},
  }};
}}
globalThis.document = {{
  getElementById(id) {{
    if (!elements.has(id)) elements.set(id, makeElement());
    return elements.get(id);
  }},
}};
renderAssistantReview({review_js}, {current_hash_js});
const section = document.getElementById("assistantReviewSection");
console.log(JSON.stringify({{
  visible: section.classList.contains("visible"),
  pills: document.getElementById("assistantReviewPills").innerHTML,
  assessment: document.getElementById("assistantReviewAssessment").textContent,
  evidence: document.getElementById("assistantReviewEvidence").innerHTML,
  limitations: document.getElementById("assistantReviewLimitations").innerHTML,
  nextSteps: document.getElementById("assistantReviewNextSteps").innerHTML,
}}));
"""

    def test_browser_hash_matches_python_hash_contract(self):
        payload = self.investigation_payload()
        input_payload = self.input_module.build_package(payload, "viz/investigation.json")
        expected_hash = self.output_module.input_hash_for_payload(input_payload)

        self.assertEqual(self.browser_hash(payload), expected_hash)

    def test_missing_assistant_artifact_keeps_section_hidden(self):
        rendered = json.loads(self.run_node(self.dom_harness("null", json.dumps("abc123"))))

        self.assertFalse(rendered["visible"])

    def test_malformed_assistant_artifact_keeps_section_hidden(self):
        rendered = json.loads(self.run_node(self.dom_harness(json.dumps({"reason": "missing status"}), json.dumps("abc123"))))

        self.assertFalse(rendered["visible"])

    def test_matching_assistant_hash_renders_assessment(self):
        rendered = json.loads(
            self.run_node(
                self.dom_harness(
                    json.dumps(
                        {
                            "status": "ok",
                            "input_hash": "match-hash",
                            "requested_model": "openrouter/auto",
                            "provider_model": "openai/gpt-5",
                            "assessment": "Grounded review",
                            "confidence": "medium",
                            "evidence": ["Evidence item"],
                            "limitations": ["Limitation item"],
                            "next_steps": [{"id": "CHECK_GATEWAY", "label": "Check gateway", "reason": "Validate local path"}],
                            "note": "Derived review only.",
                        }
                    ),
                    json.dumps("match-hash"),
                )
            )
        )

        self.assertTrue(rendered["visible"])
        self.assertIn("Grounded review", rendered["assessment"])
        self.assertIn("Evidence item", rendered["evidence"])
        self.assertIn("Provider model", rendered["pills"])

    def test_stale_assistant_hash_hides_old_assessment_content(self):
        rendered = json.loads(
            self.run_node(
                self.dom_harness(
                    json.dumps(
                        {
                            "status": "ok",
                            "input_hash": "old-hash",
                            "requested_model": "openrouter/auto",
                            "provider_model": "openai/gpt-5",
                            "assessment": "Old assessment that should be hidden",
                            "confidence": "high",
                            "evidence": ["Old evidence"],
                            "limitations": ["Old limitation"],
                            "next_steps": [{"id": "OLD", "label": "Old step", "reason": "Old reason"}],
                            "note": "Derived review only.",
                        }
                    ),
                    json.dumps("new-hash"),
                )
            )
        )

        self.assertTrue(rendered["visible"])
        self.assertIn("does not match the current evidence package", rendered["assessment"])
        self.assertNotIn("Old assessment", rendered["assessment"])
        self.assertNotIn("Old evidence", rendered["evidence"])
        self.assertIn("Stale", rendered["pills"])


if __name__ == "__main__":
    unittest.main()
