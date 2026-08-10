"""
Phase 1 dashboard consolidation tests.

Verifies the new hierarchy:
  1. Current Summary  (with scope + condition folded in)
  2. Likely Issue     (with Why Upstream collapsed inside)
  3. DNS & Web Health (with resolver paths merged in)
  4. Historical Patterns (combined similarity + learnings)
  5. Internet Conditions  — primary row, no collapse
  6. Power Infrastructure — primary row, no collapse
  7. DNS Activity         — primary row, no collapse

Standalone cards removed: userImpact, affectedScope, technicalCondition,
  evidenceQuality, dependencyStateCard (standalone), refinedAttributionCard
  (standalone), similarityCard, operationalLearningCard.

Router Path moved to top status strip (statusRouterPath).
"""

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "viz" / "index.html"


def extract_function(source: str, signature: str) -> str:
    start = source.find(signature)
    if start == -1:
        raise AssertionError(f"Could not find {signature}")
    paren_start = source.find("(", start)
    depth = 0
    brace_start = None
    for idx in range(paren_start, len(source)):
        char = source[idx]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                brace_start = source.find("{", idx)
                break
    if brace_start is None:
        raise AssertionError(f"Could not find opening brace for {signature}")
    depth = 0
    for idx in range(brace_start, len(source)):
        char = source[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:idx + 1]
    raise AssertionError(f"Could not find closing brace for {signature}")


class DashboardConsolidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text()

    # ── Removed standalone cards ──────────────────────────────────────────────

    def test_removed_standalone_cards_absent(self):
        """Cards that were folded, merged, or eliminated must not be present."""
        for removed_id in (
            "userImpactCard",
            "affectedScopeCard",
            "technicalConditionCard",
            "evidenceQualityCard",
            "dependencyStateCard",
            "refinedAttributionCard",
            "similarityCard",
            "operationalLearningCard",
        ):
            self.assertNotIn(f'id="{removed_id}"', self.html,
                             f"Removed card still present: {removed_id}")

    def test_local_evidence_row_removed(self):
        self.assertNotIn('class="row dashboard-overview local-evidence-row"', self.html)

    def test_supporting_context_details_removed(self):
        self.assertNotIn('id="supportingContextDetails"', self.html)
        self.assertNotIn("supportingContextDetails", self.html)

    # ── New consolidated cards present ────────────────────────────────────────

    def test_historical_patterns_card_present(self):
        self.assertIn('id="historicalPatternsCard"', self.html)
        self.assertIn('id="historicalPatternsTitle"', self.html)
        self.assertIn('id="historicalPatternsPill"', self.html)
        self.assertIn('id="historicalPatternsValue"', self.html)
        self.assertIn('id="historicalPatternsMeta"', self.html)

    def test_historical_patterns_mobile_card_present(self):
        self.assertIn('id="mobileHistoricalPatternsCard"', self.html)
        self.assertIn('id="mobileHistoricalPatternsValue"', self.html)
        self.assertIn('id="mobileHistoricalPatternsDetail"', self.html)

    def test_why_upstream_disclosure_inside_likely_cause(self):
        # Why Upstream is now a collapsed disclosure inside likelyCauseCard
        self.assertIn('id="whyUpstreamDisclosure"', self.html)
        # Its elements are preserved with the same IDs
        self.assertIn('id="refinedAttributionValue"', self.html)
        self.assertIn('id="refinedAttributionDetail"', self.html)
        self.assertIn('id="refinedAttributionConfidence"', self.html)
        self.assertIn('id="refinedAttributionUncertainty"', self.html)
        # Disclosure must appear after likelyCauseCard and before applicationExperienceCard
        likely_idx = self.html.index('id="likelyCauseCard"')
        upstream_idx = self.html.index('id="whyUpstreamDisclosure"')
        app_idx = self.html.index('id="applicationExperienceCard"')
        self.assertLess(likely_idx, upstream_idx)
        self.assertLess(upstream_idx, app_idx)

    def test_dns_web_health_card_heading(self):
        self.assertIn("DNS &amp; Web Health", self.html)
        self.assertIn('id="applicationExperienceCard"', self.html)

    def test_dns_web_health_has_resolver_path_elements(self):
        # Resolver path elements previously in dependencyStateCard are now
        # embedded inside applicationExperienceCard
        for el_id in (
            "dependencyStateValue",
            "dependencyPrimaryValue",
            "dependencySecondaryValue",
            "dependencyActiveMemberValue",
            "dependencyRedundancyValue",
            "dependencyMembersChips",
        ):
            self.assertIn(f'id="{el_id}"', self.html, f"Missing resolver element: {el_id}")
        # These elements must appear AFTER applicationExperienceCard
        app_idx = self.html.index('id="applicationExperienceCard"')
        for el_id in ("dependencyStateValue", "dependencyPrimaryValue"):
            el_idx = self.html.index(f'id="{el_id}"')
            self.assertGreater(el_idx, app_idx,
                               f"{el_id} must be inside applicationExperienceCard section")

    def test_dns_web_health_status_uses_artifact_states_not_latency_thresholds(self):
        fn = extract_function(self.html, "function transactionStatus")
        self.assertIn('const status = String(transaction.status || "").toLowerCase();', fn)
        self.assertIn('if (status === "ok")', fn)
        self.assertIn('["failed", "timeout", "http_error"].includes(status)', fn)
        self.assertNotIn("slowThreshold", fn)
        self.assertNotIn("latency >", fn)
        self.assertNotIn("renderStatusLatencyStrip", self.html)
        self.assertNotIn("applicationExperienceVisual", self.html)
        self.assertIn("Primary resolver", self.html)
        self.assertIn("Secondary resolver", self.html)
        self.assertIn('id="mobileApplicationPrimaryDnsValue"', self.html)
        self.assertIn('id="mobileApplicationSecondaryDnsValue"', self.html)

    # ── Current Summary expanded rows ─────────────────────────────────────────

    def test_current_summary_has_scope_and_condition(self):
        self.assertIn('id="timeContextScope"', self.html)
        self.assertIn('id="timeContextCondition"', self.html)
        # Both must be inside the operator assessment card
        card_idx = self.html.index('id="operatorAssessmentCard"')
        scope_idx = self.html.index('id="timeContextScope"')
        cond_idx = self.html.index('id="timeContextCondition"')
        # Must come after the card opening and before any other major card
        self.assertGreater(scope_idx, card_idx)
        self.assertGreater(cond_idx, card_idx)

    # ── Router Path in status strip ──────────────────────────────────────────

    def test_router_path_in_status_strip(self):
        self.assertIn('id="statusRouterPath"', self.html)
        # statusRouterPath must appear within the #status element
        status_idx = self.html.index('id="status"')
        router_idx = self.html.index('id="statusRouterPath"')
        # There must be no new row between them
        self.assertGreater(router_idx, status_idx)
        segment = self.html[status_idx:router_idx]
        self.assertNotIn('class="row dashboard-overview"', segment)

    def test_router_path_written_in_update_connection_card(self):
        fn = extract_function(self.html, "function updateConnectionCard")
        self.assertIn("statusRouterPath", fn)
        self.assertIn("pathTagLabel", fn)

    # ── Internet / Power / DNS promoted to primary ────────────────────────────

    def test_internet_conditions_not_behind_details(self):
        # internetConditionsCard must NOT be a descendant of supportingContextDetails
        # (which is now gone); verify it appears in a plain row
        self.assertNotIn("supportingContextDetails", self.html)
        self.assertIn('id="internetConditionsCard"', self.html)
        # The card's parent should be a row, not a details element
        ic_idx = self.html.index('id="internetConditionsCard"')
        preceding = self.html[:ic_idx]
        # The last <details that wasn't closed before ic_idx must be legacyCompatibilityDetails
        # (which is display:none) — internet card is NOT inside it
        last_open_details = preceding.rfind("<details")
        last_close_details = preceding.rfind("</details>")
        # Either no unclosed details, or the last one was legacyCompatibilityDetails
        if last_open_details > last_close_details:
            open_tag = self.html[last_open_details:last_open_details + 120]
            self.assertIn("legacyCompatibilityDetails", open_tag)

    def test_power_infrastructure_not_behind_details(self):
        self.assertIn('id="powerInfrastructureCard"', self.html)

    def test_dns_activity_not_behind_details(self):
        self.assertIn('id="dnsCard"', self.html)

    # ── JS function presence ──────────────────────────────────────────────────

    def test_render_historical_patterns_function_present(self):
        self.assertIn("function renderHistoricalPatterns", self.html)

    def test_removed_similarity_and_learnings_functions_absent(self):
        self.assertNotIn("function renderSimilarityForTimeContext", self.html)
        self.assertNotIn("function renderLearningsForTimeContext", self.html)

    def test_why_upstream_disclosure_controlled_by_js(self):
        fn = extract_function(self.html, "function renderDashboardHealthDimensions")
        self.assertIn("whyUpstreamDisclosure", fn)
        self.assertIn("attributionSubstantive", fn)

    def test_scope_and_condition_written_by_render_dimensions(self):
        fn = extract_function(self.html, "function renderDashboardHealthDimensions")
        self.assertIn("timeContextScope", fn)
        self.assertIn("timeContextCondition", fn)

    def test_selected_mode_clears_scope_and_condition(self):
        fn = extract_function(self.html, "function renderSelectedTimeContext")
        self.assertIn("timeContextScope", fn)
        self.assertIn("timeContextCondition", fn)
        # When a bucket is selected, scope/condition should say "Selected interval..."
        self.assertIn("Selected interval", fn)
        self.assertIn("if (selected)", fn)

    def test_render_historical_patterns_not_browser_inference(self):
        fn = extract_function(self.html, "function renderHistoricalPatterns")
        # Must not compute new similarity scores or pattern strings from raw data
        self.assertNotIn("intervalOverlap", fn)
        self.assertNotIn("computeAttribution", fn)
        # Must read from artifact-provided payload fields only
        self.assertIn("similarityPayload", fn)
        self.assertIn("learningsPayload", fn)
        self.assertIn("strong_match_count", fn)
        self.assertNotIn("renderEventStrip", fn)
        self.assertNotIn("current.pattern", fn)

    def test_historical_patterns_strip_and_internal_language_removed(self):
        self.assertNotIn("historicalPatternsVisual", self.html)
        self.assertNotIn("mobileHistoricalPatternsVisual", self.html)
        self.assertNotIn("event-strip", self.html)
        self.assertNotIn("Adaptive Baseline Event", self.html)
        self.assertNotIn("adaptive_baseline_event", self.html)
        fn = extract_function(self.html, "function operatorHistoricalText")
        self.assertIn('replace(/adaptive baseline event/ig, "similar network behavior")', fn)

    def test_sync_dashboard_time_context_calls_historical_patterns(self):
        fn = extract_function(self.html, "function syncDashboardTimeContext")
        self.assertIn("renderHistoricalPatterns", fn)
        self.assertNotIn("renderSimilarityForTimeContext", fn)
        self.assertNotIn("renderLearningsForTimeContext", fn)

    # ── Legacy artifact compatibility ─────────────────────────────────────────

    def test_legacy_compatibility_mode_still_works(self):
        # setLegacyCompatibilityMode must still exist and reference legacyCompatibilityDetails
        fn = extract_function(self.html, "function setLegacyCompatibilityMode")
        self.assertIn("legacyCompatibilityDetails", fn)

    def test_mobile_sync_simplified(self):
        fn = extract_function(self.html, "function updateMobileCurrentState")
        # Simplified: removes old elements, keeps likelyCause + historicalPatterns
        self.assertIn("mobileLikelyCauseValue", fn)
        self.assertIn("mobileHistoricalPatternsCard", fn)
        self.assertNotIn("mobileUserImpactValue", fn)
        self.assertNotIn("mobileAffectedScopeValue", fn)
        self.assertNotIn("mobileTechnicalConditionValue", fn)

    def test_micro_visuals_do_not_create_new_cards_or_touch_narrative_cards(self):
        primary = self.html[self.html.index('id="operatorAssessmentCard"'):self.html.index('id="compareWrap"')]
        self.assertIn('id="dnsActivityBars"', primary)
        current_summary = self.html[self.html.index('id="operatorAssessmentCard"'):self.html.index('<div class="viz-panel primary mobile-only">')]
        likely_issue = self.html[self.html.index('id="likelyCauseCard"'):self.html.index('id="applicationExperienceCard"')]
        for marker in ("micro-strip", "event-strip", "micro-bars"):
            self.assertNotIn(marker, current_summary)
            self.assertNotIn(marker, likely_issue)
        self.assertNotIn('id="dnsAndWebVisualCard"', self.html)
        self.assertNotIn('id="externalEventStripCard"', self.html)

    def test_micro_visual_markup_is_mobile_safe(self):
        self.assertIn('@media (max-width: 767px)', self.html)
        self.assertIn('id="mobilePrimaryDnsBars"', self.html)
        self.assertNotIn('id="mobileInternetConditionsEventStrip"', self.html)
        self.assertNotIn('id="mobilePowerInfrastructureEventStrip"', self.html)

    def test_lan_tooltip_has_selected_bucket_context_parity(self):
        fn = extract_function(self.html, "function selectedBucketEvidenceHtml")
        self.assertIn("LAN elevation is isolated from sustained WAN bad-sample counts.", fn)
        render_fn = extract_function(self.html, "function renderLine")
        self.assertIn("selectedBucketEvidenceHtml(opts.selectedEvidence)", render_fn)

    # ── No browser-side semantic inference introduced ──────────────────────────

    def test_no_new_browser_inference_in_historical_patterns(self):
        fn = extract_function(self.html, "function renderHistoricalPatterns")
        # Must not call intervalsOverlap or any attribution computation
        self.assertNotIn("intervalsOverlap", fn)
        self.assertNotIn("computeAttribution(", fn)
        self.assertNotIn("markPersistentWanBad", fn)

    def test_no_external_overlap_inference_in_browser(self):
        self.assertNotIn("function externalEventsForContext", self.html)
        self.assertNotIn("function itemTimeWindow", self.html)
        self.assertIn("overlapping_external_event_sources", self.html)
        selected_fn = extract_function(self.html, "function renderExternalContextTimeNote")
        self.assertIn("overlapping_external_event_sources", selected_fn)
        self.assertNotIn("intervalsOverlap", selected_fn)


if __name__ == "__main__":
    unittest.main()
