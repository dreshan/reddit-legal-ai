"""Local human-review policy applied on top of Claude's output.

Claude's own ``requires_manual_review`` is never trusted to be the final word:
these deterministic rules can only *add* a review requirement, never remove one.
"""

from __future__ import annotations

from app.models.schemas import UNKNOWN_JURISDICTION, AIAnalysis, ClaudeAnalysisResponse

_FLAG_REASONS = {
    "criminal_matter": "Criminal matter",
    "immigration_matter": "Immigration matter",
    "serious_injury": "Serious injury",
    "high_financial_impact": "High financial impact",
    "active_litigation": "Active litigation",
    "threats_or_safety_risk": "Threats or safety risk",
    "unclear_facts": "Unclear facts",
    "sensitive_personal_information": "Potentially sensitive personal information",
}

# Category-text backstops in case Claude omits the corresponding flag.
_CATEGORY_TERMS = {
    "criminal_matter": ("criminal", "crime", "police", "arrest", "dui", "dwi", "assault", "theft", "fraud"),
    "immigration_matter": ("immigration", "visa", "asylum", "deport", "citizenship", "residency", "green card"),
    "serious_injury": ("personal injury", "wrongful death", "medical negligence", "clinical negligence"),
    "active_litigation": ("litigation", "court proceedings"),
}


def apply_review_policy(
    analysis: ClaudeAnalysisResponse,
    confidence_threshold: float,
    model: str | None = None,
) -> AIAnalysis:
    flags = set(analysis.review_flags)
    category_text = f"{analysis.legal_category} {analysis.sub_category}".lower()
    for flag, terms in _CATEGORY_TERMS.items():
        if any(term in category_text for term in terms):
            flags.add(flag)

    reasons: list[str] = []
    if analysis.requires_manual_review:
        reasons.append("Flagged for review by AI")
    if analysis.relevant and (not analysis.jurisdiction_known or analysis.jurisdiction == UNKNOWN_JURISDICTION):
        reasons.append("Unknown jurisdiction")
    reasons.extend(label for flag, label in _FLAG_REASONS.items() if flag in flags)
    if analysis.risk_level == "High":
        reasons.append("High risk level")
    if analysis.confidence < confidence_threshold:
        reasons.append(f"Low AI confidence ({analysis.confidence:.2f} < {confidence_threshold:.2f})")

    data = analysis.model_dump()
    data.update(
        review_flags=sorted(flags),
        requires_manual_review=bool(reasons),
        manual_review_reasons=reasons,
        model=model,
    )
    return AIAnalysis.model_validate(data)
