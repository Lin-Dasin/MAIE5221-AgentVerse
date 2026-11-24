"""Check the newsroom's detector evidence and visible fact-check article."""

import re
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

from harness.scenario_fund_sources import (
    ResearchClaim,
    _claim_tokens,
    check_citations,
    check_research,
)
from harness.scenario_newsroom import _read_text
from harness.search_evidence import visible_markdown_lines

EVIDENCE_ARTIFACT = "outputs/detector-evidence.json"
ARTICLE_ARTIFACT = "outputs/ai-detector-fact-check.md"
INITIAL_SEARCH = (
    "AI writing detectors academic integrity test student assignments false positives false "
    "negatives original peer-reviewed evaluation site:link.springer.com/article"
)
EVIDENCE_GUIDANCE = (
    "Use the system's initial research file if supplied, and search for additional evidence "
    "when needed. Find specific primary sources on the reliability of "
    "AI-writing detectors for student assignments. Look for published evaluations of both "
    "false positives and false negatives, and distinguish vendor guidance from independent "
    "measurements. Keep findings scoped to the tested tool, language, dataset and conditions. "
    "Use a small set of distinct claims from direct study or official guidance pages. Avoid "
    "duplicating the same result in several rows. For a percentage, copy a short finding "
    "verbatim from an original source excerpt, retaining the metric and tested population. "
    "Otherwise use a supported qualitative finding. Never turn a false-positive rate on one "
    "test sample into overall accuracy. Report what each study or review actually found; "
    "do not infer that no included study found a result from a review that reports uncertainty. "
    f"Write {EVIDENCE_ARTIFACT} as a JSON object with one key, claims, whose value is a list. "
    "Each claim has only claim, source_url, source_ref and source_date. Copy the exact HTTPS "
    "URL and the ID displayed at the start of the same result title (R7 for [R7]). Use an ISO "
    "publication date only when the same result establishes it, or null. Only state figures "
    "and years present in that result. Put the evidence file first. On repair, use the reported "
    "source defect to correct the specific claim and result instead of repeating broad research."
)
ARTICLE_GUIDANCE = (
    "Read inputs/shared/fact_checker/verified-claims.md when available. It contains the "
    "confirmed statements and citation links ready for your fact-check. "
    f"Write {ARTICLE_ARTIFACT} for university students and faculty. "
    "Give an explicit Verdict: followed by a clear conclusion about whether a detector can "
    "reliably determine authorship. Explain both false positives (human writing flagged as AI) "
    "and false negatives (AI writing missed), what a detector score can establish, and what "
    "remains uncertain. Give practical guidance separately to students and faculty. Tie each "
    "checked claim to its source. The system appends the verified evidence section to the "
    "article, so focus the prose on the verdict, caveats and guidance. Keep percentages in "
    "that section unless you repeat a complete checked claim next to its source link. "
    "Keep the verdict scoped to the reviewed evidence, avoiding claims that no study ever "
    "established reliability. "
    "Do not generalize a study's numbers beyond its tools and samples. When evidence changes, "
    "read the current shared ledger and update the article and links."
)

_TOPIC = re.compile(
    r"\bai[- ](?:writing[- ]|text[- ]|content[- ])?(?:detect\w*|classifi\w*)"
    r"|\bdetect\w* (?:ai[- ]generated|machine[- ]generated)"
    r"|\b(?:ai|machine)[- ]generated (?:text|writing|prose)"
    r"|\bfalse[- ](?:positive|negative)s?\b"
    r"|(?:ai|人工智能|人工智慧)\s*(?:写作|寫作|文本|生成)?\s*(?:检测|檢測|偵測)"
    r"|误报|誤報|漏报|漏報|假阳性|假陽性|假阴性|假陰性",
    re.IGNORECASE,
)
_RATE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?[ \t]*(?:[%％](?!\w)|percent\b)", re.I)
_ABSOLUTE_STUDIES = re.compile(
    r"\b(?:no|none of the) (?:\w+ ){0,3}stud(?:y|ies) "
    r"(?:in the review )?(?:(?:has|have|ever) )*(?:establish|show|demonstrat)\w*\b",
    re.I,
)
_VERDICT = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+)?(?:\*\*|__)?"
    r"(?:(?:verdict|conclusion)\b|結論|结论|判定)"
    r"(?:\*\*|__)?[ \t]*[:：](?:\*\*|__)?[ \t]*\S"
    r"|^[ \t]*#{1,6}[ \t]+(?:(?:verdict|conclusion)\b|結論|结论|判定)"
    r"[ \t]*\n(?:[ \t]*\n)*[ \t]*[^\s#]",
    re.IGNORECASE | re.MULTILINE,
)
_ARTICLE_CHECKS = (
    (
        re.compile(r"\bfalse[- ]positive(?:s| rate)?\b|误报|誤報|假阳性|假陽性", re.I),
        "Explain false positives, where detectors flag human writing as AI-generated.",
    ),
    (
        re.compile(r"\bfalse[- ]negative(?:s| rate)?\b|漏报|漏報|假阴性|假陰性", re.I),
        "Explain false negatives, where detectors miss AI-generated writing.",
    ),
    (
        re.compile(
            r"\b(?:uncertain\w*|limitations?|unknown|var(?:y|ies|ied|iable)|"
            r"not generaliz\w+)\b|不確定|不确定|局限|侷限|限制",
            re.I,
        ),
        "Explain what remains uncertain and where the findings may not generalize.",
    ),
    (re.compile(r"\bstudents?\b|學生|学生", re.I), "Give practical guidance for students."),
    (
        re.compile(r"\b(?:faculty|instructors?|teachers?)\b|教師|教师|教職員|教职员", re.I),
        "Give practical guidance for faculty.",
    ),
)


def check_evidence(
    root: Path,
    observations: Mapping[str, Sequence[str]],
    today: date,
    *,
    references: Mapping[str, tuple[str, str]],
) -> tuple[list[ResearchClaim], list[str]]:
    """Require at least one checked claim to address detector reliability."""
    claims, defects = check_research(
        root, observations, today, references=references, artifact=EVIDENCE_ARTIFACT
    )
    if defects:
        return claims, defects
    for index, claim in enumerate(claims, start=1):
        excerpt = (
            references[claim.source_ref][1] if claim.source_ref else claim.source_quote or ""
        )
        if f" {_claim_tokens(claim.claim)} " in f" {_claim_tokens(excerpt)} ":
            continue
        if _RATE.search(claim.claim):
            defects.append(
                f"Claim {index}: copy a complete supporting passage with the percentage, "
                "metric and tested population unchanged, or describe the finding qualitatively."
            )
        if _ABSOLUTE_STUDIES.search(claim.claim):
            defects.append(
                f"Claim {index}: report the review's actual finding and scope. Do not infer "
                "that no study found a result unless the cited passage explicitly says so."
            )
    if not defects and not any(_TOPIC.search(claim.claim) for claim in claims):
        defects.append("Add a sourced claim about AI-writing detector performance or errors.")
    return claims, defects


def check_article(root: Path, claims: Sequence[ResearchClaim]) -> list[str]:
    """Check provenance and the article's required coverage before editorial review."""
    article = _read_text(root, ARTICLE_ARTIFACT)
    if not article or not article.strip():
        return [f"Write a readable Markdown fact-check at {ARTICLE_ARTIFACT}."]
    visible = "\n".join(visible_markdown_lines(re.sub(r"<!--.*?-->", "", article, flags=re.S)))
    defects = check_citations(root, ARTICLE_ARTIFACT, claims)
    for line in visible.splitlines():
        remaining = line
        for claim in claims:
            if claim.source_url in line:
                remaining = remaining.replace(claim.claim, "")
        if _RATE.search(remaining):
            defects.append(
                "Keep percentages in their full verified claim with its source link. "
                "Remove or rephrase unsupported figures in the article prose."
            )
            break
        if _ABSOLUTE_STUDIES.search(remaining):
            defects.append(
                "Describe what the checked studies establish, without a universal negative."
            )
            break
    if not _VERDICT.search(visible):
        defects.append("Start with an explicit Verdict: answering the reliability claim.")
    defects.extend(message for pattern, message in _ARTICLE_CHECKS if not pattern.search(visible))
    return defects


def verified_claim_lines(claims: Sequence[ResearchClaim]) -> str:
    return "\n".join(f"- {item.claim} [Source]({item.source_url})" for item in claims)


def attach_verified_claims(article: str, claims: Sequence[ResearchClaim]) -> str:
    """Render citations from the verified ledger; replace the section on subsequent edits."""
    start = "<!-- checked-detector-evidence:start -->"
    end = "<!-- checked-detector-evidence:end -->"
    article = re.sub(re.escape(start) + r".*?" + re.escape(end), "", article, flags=re.S)
    return (
        f"{article.rstrip()}\n\n{start}\n## Checked evidence\n\n"
        f"{verified_claim_lines(claims)}\n{end}\n"
    )
