"""
Advanced AI Slop Detection System v3
Multi-layer detection: heuristics + statistical analysis + provenance.

Detection layers:
  1. Linguistic fingerprint analysis (AI phrase detection)
  2. Statistical structure analysis (entropy, uniformity, repetition)
  3. Syntactic pattern analysis (sentence structure, paragraph flow)
  4. Content depth verification (factual density, citation presence)
  5. Source trust scoring (domain reputation, content patterns)
  6. Cross-reference validation (internal consistency checks)
  7. Provenance gate (author, date, citations, outbound links)
  8. Compression analysis (repetitive text compresses unnaturally well)
  9. Burstiness analysis (AI sentences are uniformly long)

No neural models — everything is deterministic/statistical.
"""

import re
import os
import json
import math
import zlib
import hashlib
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import Counter
from urllib.parse import urlparse

logger = logging.getLogger("capybara.slop_detector")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# ============================================================
# EXPANDED AI DETECTION PATTERNS (Layer 1)
# ============================================================

# Strong, high-precision AI artifacts — conversational self-reference and
# explicit AI authorship claims. These alone justify suspicion.
AI_STRONG_PHRASES = [
    r"as an? (?:ai|artificial intelligence|language model|assistant|llm)",
    r"i(?:'m| am) (?:an?|a) (?:ai|language model|assistant|chatbot)",
    r"i (?:cannot|can't|am unable to) (?:browse|access the internet|view images|see images)",
    r"my (?:knowledge|training data) (?:cutoff|ends|is limited|goes up to)",
    r"let me (?:break this down|explain|help you|tell you|share|provide)",
    r"here(?:'s| is) (?:a|an|the) (?:comprehensive |quick |detailed |complete )?(?:summary|breakdown|overview|explanation|guide)",
    r"i (?:hope|would be happy) to (?:help|assist|elaborate|explain|clarify)",
    r"i (?:would|can) (?:be happy|love|glad) to (?:help|assist|elaborate)",
    r"please (?:let me know|feel free|don't hesitate)",
    r"(?:great|excellent|good) question[!.]?",
    r"to (?:summarize|summarise|wrap up|recap)\b",
    r"without further ado",
]

# Weak stylistic signals — normal encyclopedic and journalistic prose contains
# these too, so they only count when unusually dense (several unique hits at
# high frequency) and never alone.
AI_WEAK_PHRASES = [
    r"it(?:'s| is) important to (?:note|mention|consider|remember)",
    r"it(?:'s| is) worth noting that",
    r"here are (?:some|a few|the|several) (?:key|important|essential|notable|tips|ways|steps)",
    r"harness(?:ing)? the (?:power|potential|capabilities)",
    r"(?:leverage|utilize|optimize|maximize) (?:the|this|that|your|our|its)",
    r"(?:robust|comprehensive|holistic|streamlined|seamless|intuitive|scalable) (?:approach|solution|framework|experience|interface|strategy|method)",
    r"(?:empower|enable|facilitate|enhance|streamline) (?:users|individuals|teams|businesses|your)",
    r"(?:delve|dive) (?:deeper|further|into|beneath)",
    r"(?:paint|shed) (?:a )?(?:better|clearer|more complete|a fuller) picture",
    r"the (?:realm|domain|landscape|frontier|paradigm) of",
    r"let(?:'s| us) (?:explore|dive|delve|uncover|examine)",
    r"(?:journey|exploration) (?:into|through|of)",
    r"in today(?:'s|s) (?:fast-paced|digital|modern)",
    r"(?:unlock(?:ing)?|discover(?:ing)?|reveal(?:ing)?) (?:the|your|hidden|untapped)",
    r"(?:stay tuned|keep reading|read on|scroll down|continue reading|don't miss)",
    r"the (?:key takeaway|bottom line) is",
    r"when it comes to",
    r"it(?:'s| is) (?:not only|not just) about",
]

AI_STRUCTURE_PATTERNS = [
    r"(?:^|\n)#{1,3}\s+\d+[\.\)]\s+",           # Numbered headers
    r"(?:^|\n)\*\*Step \d+",                      # Step-by-step bold markers
    r"(?:^|\n)[✅🔹📌💡🚀✨⭐🏷️📋📝🔍🎯📊📈⬇️➡️] ",  # Emoji bullets
    r"(?:^|\n)[-—] Step \d+",                     # Dash step markers
    r"(?:^|\n)\*\*\d+[\.\)]\s+\*\*",             # Bold numbered points
    r"(?:^|\n)[⚠️❗‼️] ",                          # Warning emoji patterns
    r"(?:^|\n)[📱💻🔧🛠️⚙️🔑🔐💳] ",              # Tool/tech emoji patterns
]

# Explicit AI authorship / platform disclosure only. Bare model names are NOT
# signals: encyclopedias discuss ChatGPT, Claude and Gemini legitimately.
AI_PLATFORM_PATTERNS = [
    r"(?:generated|created|written|produced|drafted) by (?:openai|chatgpt|gpt-4o?|gpt-3\.5|claude|gemini|copilot)",
    r"powered by (?:openai|chatgpt|gpt-4o?|claude|gemini|copilot)",
    r"(?:ai|artificial intelligence)[ -]generated",
    r"this (?:content|text|response|answer|article) was (?:generated|created|written|produced) by",
    r"(?:written|composed|drafted) by an? (?:ai|language model|llm|chatbot)",
    r"(?:an? )?(?:ai|chatbot|language model|llm) (?:wrote|composed|generated|produced|drafted) (?:this|the following)",
]

AI_FORMATTING_PATTERNS = [
    r"(?:^|\n)#{1,2}\s+(?:Step|Phase|Stage|Part) \d+",  # Phase headers
    r"(?:^|\n)#{1,2}\s+(?:Introduction|Conclusion|Summary|Overview|FAQ|Key Takeaways)",  # AI template sections
    r"(?:^|\n)---+\s*$",                                 # Horizontal rules (AI filler)
    r"(?:^|\n)>\s+\*\*Note:\*\*|>\s+\*\*Important:\*\*|>\s+\*\*Tip:\*\*",  # AI callout boxes
    r"(?:^|\n)\|\s+.*\s+\|\s+.*\s+\|",                  # Markdown tables (AI-generated)
]


@dataclass
class SlopScore:
    """Result of AI slop detection"""
    is_slop: bool
    confidence: float  # 0-1, higher = more likely AI-generated
    signals: List[str]
    boilerplate_count: int = 0
    structure_score: float = 0.0
    entropy_score: float = 0.0
    source_trusted: bool = False
    factual_density: float = 0.0
    syntactic_uniformity: float = 0.0
    repetition_score: float = 0.0
    vocabulary_richness: float = 0.0
    provenance_score: float = 0.0
    compression_ratio: float = 0.0
    burstiness: float = 0.0


class SlopDetector:
    """
    Multi-layer AI content detection system.
    Only genuine human knowledge passes through.
    """

    # Domains that are known AI content farms or low-quality content
    SPAM_DOMAINS = {
        # Social platforms — never index (user policy)
        "facebook.com", "fb.com", "fb.watch", "fbsbx.com",
        "instagram.com", "cdninstagram.com",
        "tiktok.com",
        # Essay mills
        "essaypro.com", "essaywriter.net", "cheapessay.net", "grademiners.com",
        "studybay.com", "speedyessay.com", "essaybox.com", "essayoneday.com",
        "penmypaper.com", "papercoach.net", "writemypapers.org",
        # Spam blogs
        "jennifertimothyblog.com", "myblog-u.com", "blogbazooka.com",
        "masstamilan.in", "techybois.com", "techhub-news.com",
        # AI writer / content farm sites
        "aiwritershub.com", "chatgptwriter.com", "aicontentpro.com",
        "contentbot.ai", "writesonic.com", "jasper.ai",
        "rytr.me", "copy.ai", "peppertype.ai",
    }

    # Domains known for quality human content (bonus trust)
    TRUSTED_DOMAINS = {
        "wikipedia.org": 1.0, "britannica.com": 0.98, "stanford.edu": 0.97,
        "arxiv.org": 0.96, "nature.com": 0.98, "science.org": 0.98,
        "mit.edu": 0.97, "harvard.edu": 0.97, "yale.edu": 0.96,
        "github.com": 0.90, "stackoverflow.com": 0.92,
        "mozilla.org": 0.95, "developer.mozilla.org": 0.96,
        "loc.gov": 0.98, "nasa.gov": 0.97,
        "gutenberg.org": 0.95, "archive.org": 0.95,
        "ourworldindata.org": 0.96, "worldbank.org": 0.95,
        "khanacademy.org": 0.95, "ocw.mit.edu": 0.97,
        "mayoclinic.org": 0.97, "nhs.uk": 0.96,
        "scholarpedia.org": 0.95, "plato.stanford.edu": 0.97,
        "imslp.org": 0.93, "oeis.org": 0.94,
        "pubchem.ncbi.nlm.nih.gov": 0.96, "eol.org": 0.94,
        "free.law": 0.93, "law.cornell.edu": 0.95,
        "instructables.com": 0.88, "permies.com": 0.87,
        "openstove.org": 0.85, "freecodecamp.org": 0.91,
        "thespruce.com": 0.86, "familyhandyman.com": 0.86,
        "investopedia.com": 0.88, "zerotwothree.org": 0.92,
    }

    def __init__(self):
        self.strong_patterns = [
            re.compile(p, re.IGNORECASE) for p in AI_STRONG_PHRASES
        ]
        self.weak_patterns = [
            re.compile(p, re.IGNORECASE) for p in AI_WEAK_PHRASES
        ]
        self.structure_patterns = [
            re.compile(p, re.IGNORECASE | re.MULTILINE) for p in AI_STRUCTURE_PATTERNS
        ]
        self.platform_patterns = [
            re.compile(p, re.IGNORECASE) for p in AI_PLATFORM_PATTERNS
        ]
        self.formatting_patterns = [
            re.compile(p, re.IGNORECASE | re.MULTILINE) for p in AI_FORMATTING_PATTERNS
        ]

        self.approved_sources = self._load_approved_sources()

    def _load_approved_sources(self) -> set:
        """Load approved human knowledge sources"""
        try:
            from ..sources import ALL_SOURCES
            return {s.name for s in ALL_SOURCES}
        except ImportError:
            return set()

    def _get_domain(self, url_or_source: str) -> str:
        """Extract domain from URL or source name"""
        if "://" in url_or_source:
            return urlparse(url_or_source).netloc.lower()
        return url_or_source.lower()

    def analyze(self, content_text: str, source_name: str = "",
                title: str = "", url: str = "",
                item: Optional[Dict] = None) -> SlopScore:
        """
        Multi-layer AI content analysis.
        `item` (optional) enables provenance checks from crawl metadata.
        Returns SlopScore with detection results.
        """
        signals = []
        score = 0.0

        if not content_text or len(content_text.strip()) < 20:
            return SlopScore(
                is_slop=True, confidence=0.7,
                signals=["Content too short"],
                source_trusted=False
            )

        # Clean text for analysis
        clean_text = content_text.strip()
        words = clean_text.split()
        word_count = len(words)

        # ── Layer 1: Source Trust ──
        domain = self._get_domain(url or source_name)
        source_trusted = source_name in self.approved_sources
        is_spam_domain = any(spam in domain for spam in self.SPAM_DOMAINS)
        domain_trust = 0.0
        for trusted_domain, trust_score in self.TRUSTED_DOMAINS.items():
            if trusted_domain in domain:
                domain_trust = trust_score
                break

        if is_spam_domain:
            signals.append(f"Known AI content farm domain: {domain}")
            score += 0.4
        elif not source_trusted and domain_trust == 0:
            signals.append(f"Source '{source_name}' not in approved list")
            score += 0.05  # Minor penalty — untrusted doesn't mean AI

        # ── Layer 2: AI phrase analysis (strong vs weak) ──
        boilerplate_count = 0
        strong_hits = 0
        weak_hits = 0

        for pattern in self.strong_patterns:
            matches = pattern.findall(clean_text)
            if matches:
                boilerplate_count += len(matches)
                strong_hits += 1
                signals.append(f"AI phrase: {pattern.pattern[:50]}")

        if strong_hits:
            score += min(0.15 * strong_hits, 0.50)

        for pattern in self.weak_patterns:
            matches = pattern.findall(clean_text)
            if matches:
                boilerplate_count += len(matches)
                weak_hits += 1

        # Weak phrases count only when dense (several unique hits at high frequency)
        weak_density = weak_hits / max(word_count / 1000.0, 0.2)
        if weak_hits >= 4 and weak_density >= 3.0:
            score += min(0.04 * weak_hits, 0.15)
            signals.append(f"AI marketing phrases ({weak_hits} unique, dense)")

        # ── Layer 3: Structure Patterns ──
        structure_score = 0.0
        for pattern in self.structure_patterns:
            matches = pattern.findall(clean_text)
            if matches:
                structure_score += len(matches) * 0.08
                signals.append(f"AI structure: {pattern.pattern[:40]}")
        score += min(structure_score, 0.25)

        # ── Layer 4: AI Platform Mentions ──
        for pattern in self.platform_patterns:
            if pattern.search(clean_text) or pattern.search(title):
                signals.append(f"AI platform ref: {pattern.pattern[:40]}")
                score += 0.35

        # ── Layer 5: AI Formatting Patterns ──
        formatting_hits = 0
        for pattern in self.formatting_patterns:
            matches = pattern.findall(clean_text)
            if matches:
                formatting_hits += len(matches)
        if formatting_hits >= 3:
            signals.append(f"AI formatting patterns ({formatting_hits} hits)")
            score += 0.15

        # ── Layer 6: Statistical Analysis ──

        # Token entropy (Shannon entropy)
        entropy_score = self._calculate_entropy(clean_text)
        if entropy_score < 3.0:
            signals.append(f"Low entropy ({entropy_score:.2f}) - repetitive")
            score += 0.2
        elif entropy_score > 5.5:
            signals.append(f"Very high entropy ({entropy_score:.2f}) - scrambled")
            score += 0.15

        # Sentence length uniformity (AI produces uniform sentences)
        sentence_uniformity = self._sentence_uniformity(clean_text)
        if sentence_uniformity > 0.85:
            signals.append(f"Uniform sentence lengths ({sentence_uniformity:.2f})")
            score += 0.18

        # Paragraph length uniformity
        para_uniformity = self._paragraph_uniformity(clean_text)
        if para_uniformity > 0.8:
            signals.append(f"Uniform paragraph lengths ({para_uniformity:.2f})")
            score += 0.12

        # Repetition analysis (n-gram repetition)
        repetition = self._repetition_score(clean_text)
        if repetition > 0.3:
            signals.append(f"High repetition ({repetition:.2f})")
            score += 0.15

        # Vocabulary richness (type-token ratio)
        vocab_richness = self._vocabulary_richness(clean_text)
        if vocab_richness < 0.3:
            signals.append(f"Low vocabulary richness ({vocab_richness:.2f})")
            score += 0.12

        # ── Layer 7: Syntactic Uniformity ──
        syntactic_uniformity = self._syntactic_uniformity(clean_text)
        if syntactic_uniformity > 0.85:
            signals.append(f"Repetitive sentence structures ({syntactic_uniformity:.2f})")
            score += 0.15

        # ── Layer 8: Factual Density (inverse — low = suspicious for AI) ──
        factual_density = self._factual_density(clean_text)
        if factual_density < 0.05 and word_count > 200:
            signals.append(f"Low factual density ({factual_density:.3f}) - vague/filler")
            score += 0.1

        # ── Layer 9: Transition Word Analysis ──
        transition_density = self._transition_density(clean_text)
        if transition_density > 0.08:
            signals.append(f"Excessive transition words ({transition_density:.3f})")
            score += 0.12

        # ── Layer 10: Consistency Checks ──
        # Check for excessive hedging
        hedging = self._hedging_density(clean_text)
        if hedging > 0.06:
            signals.append(f"Excessive hedging language ({hedging:.3f})")
            score += 0.1

        # ── Layer 11: Provenance gate ──
        provenance = self._provenance_score(item or {})
        if provenance >= 0.5:
            score = max(0, score - 0.10)
            signals.append(f"Provenance present ({provenance:.2f})")
        elif provenance == 0 and word_count > 200:
            signals.append("No provenance signals (no author/date/citations/links)")
            score += 0.06

        # ── Layer 12: Compression analysis ──
        compression = self._compression_ratio(clean_text)
        if word_count > 150 and compression < 0.30:
            signals.append(f"Unnaturally compressible ({compression:.2f}) - repetitive")
            score += 0.15

        # ── Layer 13: Burstiness (sentence-length variance) ──
        burstiness = self._burstiness(clean_text)
        if word_count > 150 and burstiness < 0.20:
            signals.append(f"Low burstiness ({burstiness:.2f}) - uniform sentences")
            score += 0.12

        # ── Bonus: Trusted domain ──
        if domain_trust >= 0.9:
            score = max(0, score - 0.15)
            signals.append(f"Trusted domain bonus ({domain_trust:.2f})")

        # Calibration dampener: heuristics are tuned for open-web spam, not for
        # vetted knowledge bases (Wikipedia, SEP, MDN, journals...).
        if source_trusted or domain_trust >= 0.85:
            score *= 0.6

        # Clamp
        score = min(score, 1.0)

        # Decision: slop threshold
        # Aggressive: reject anything above 0.30 unless from trusted domain
        if is_spam_domain:
            is_slop = True
        elif source_trusted or domain_trust >= 0.85:
            is_slop = score > 0.55  # Lenient for trusted sources
        else:
            is_slop = score > 0.30  # Strict for untrusted

        signals = signals[:25]

        return SlopScore(
            is_slop=is_slop,
            confidence=round(score, 4),
            signals=signals,
            boilerplate_count=boilerplate_count,
            structure_score=round(structure_score, 4),
            entropy_score=round(entropy_score, 4),
            source_trusted=source_trusted,
            factual_density=round(factual_density, 4),
            syntactic_uniformity=round(syntactic_uniformity, 4),
            repetition_score=round(repetition, 4),
            vocabulary_richness=round(vocab_richness, 4),
            provenance_score=round(provenance, 4),
            compression_ratio=round(compression, 4),
            burstiness=round(burstiness, 4),
        )

    # ── Statistical Methods ──

    def _calculate_entropy(self, text: str) -> float:
        """Shannon entropy of character frequencies"""
        if not text:
            return 0.0
        freq = Counter(c for c in text.lower() if c.isalnum())
        total = sum(freq.values())
        if total == 0:
            return 0.0
        return -sum((c / total) * math.log2(c / total) for c in freq.values())

    def _sentence_uniformity(self, text: str) -> float:
        """How uniform sentence lengths are (1.0 = perfectly uniform)"""
        sentences = [s.strip() for s in re.split(r'[.!?]+', text) if len(s.strip()) > 5]
        if len(sentences) < 3:
            return 0.0
        lengths = [len(s.split()) for s in sentences]
        mean = sum(lengths) / len(lengths)
        if mean == 0:
            return 0.0
        variance = sum((l - mean) ** 2 for l in lengths) / len(lengths)
        cv = (variance ** 0.5) / mean
        return max(0, 1 - cv)

    def _paragraph_uniformity(self, text: str) -> float:
        """How uniform paragraph lengths are"""
        paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 20]
        if len(paragraphs) < 3:
            return 0.0
        lengths = [len(p.split()) for p in paragraphs]
        mean = sum(lengths) / len(lengths)
        if mean == 0:
            return 0.0
        variance = sum((l - mean) ** 2 for l in lengths) / len(lengths)
        cv = (variance ** 0.5) / mean
        return max(0, 1 - cv)

    def _repetition_score(self, text: str) -> float:
        """3-gram phrase repetition analysis"""
        words = text.lower().split()
        if len(words) < 20:
            return 0.0
        phrases = Counter(" ".join(words[i:i + 3]) for i in range(len(words) - 2))
        repeated = sum(1 for count in phrases.values() if count > 2)
        return min(repeated / max(len(phrases), 1), 1.0)

    def _vocabulary_richness(self, text: str) -> float:
        """Type-token ratio"""
        words = text.lower().split()
        if len(words) < 10:
            return 0.5
        return len(set(words)) / len(words)

    def _syntactic_uniformity(self, text: str) -> float:
        """Measure how similar sentence structures are (starts with same words)"""
        sentences = [s.strip() for s in re.split(r'[.!?]+', text) if len(s.strip()) > 10]
        if len(sentences) < 4:
            return 0.0

        # Check first 3 words of each sentence
        starts = []
        for s in sentences:
            words = s.split()[:3]
            if words:
                starts.append(" ".join(w.lower() for w in words))

        if not starts:
            return 0.0

        start_counts = Counter(starts)
        most_common = start_counts.most_common(1)[0][1]
        return most_common / len(starts)

    def _factual_density(self, text: str) -> float:
        """Ratio of factual indicators (numbers, dates, citations, proper nouns)"""
        if not text:
            return 0.0
        words = text.split()
        if len(words) < 50:
            return 0.0

        factual = 0
        for i, word in enumerate(words):
            # Numbers and dates
            if re.match(r'\d+', word):
                factual += 1
            # Bracketed citations [1], [2023]
            elif re.match(r'\[\d+\]', word):
                factual += 2
            # Parenthetical years (2023)
            elif re.match(r'\(\d{4}\)', word):
                factual += 2
            # Quoted text
            elif word.startswith('"') or word.startswith("'"):
                factual += 1

        # Check for URLs (sign of real content)
        url_count = len(re.findall(r'https?://\S+', text))
        factual += url_count * 3

        return factual / len(words)

    def _transition_density(self, text: str) -> float:
        """Ratio of transition/connector words (AI overuses these)"""
        transitions = [
            "furthermore", "moreover", "additionally", "consequently",
            "therefore", "thus", "however", "moreover", "additionally",
            "in addition", "as a result", "for instance", "for example",
            "in contrast", "on the other hand", "nevertheless",
            "notwithstanding", "subsequently", "accordingly",
            "hence", "consequently", "likewise", "similarly",
            "meanwhile", "conversely", "alternatively",
        ]
        words = text.lower().split()
        if len(words) < 50:
            return 0.0
        count = sum(1 for w in words if w.rstrip(",.") in transitions)
        return count / len(words)

    def _hedging_density(self, text: str) -> float:
        """Ratio of hedging/qualifier words"""
        hedges = [
            "perhaps", "possibly", "might", "could", "may",
            "arguably", "seemingly", "apparently", "presumably",
            "it seems", "it appears", "it could be argued",
            "one could argue", "it is worth noting",
            "it is important to note", "generally speaking",
            "broadly speaking", "in many cases", "in most cases",
        ]
        text_lower = text.lower()
        count = sum(text_lower.count(h) for h in hedges)
        words = len(text.split())
        if words < 50:
            return 0.0
        return count / words

    # ── v3 Statistical Methods ──

    GENERIC_AUTHORS = {
        "admin", "administrator", "editor", "editorial team", "staff",
        "webmaster", "unknown", "guest", "user", "author",
    }

    def _provenance_score(self, item: Dict) -> float:
        """Fraction of provenance signals present (author, date, citations, links)."""
        if not item:
            return 0.0

        checks = 0

        author = str(item.get("author") or "").strip().lower()
        if author and author not in self.GENERIC_AUTHORS and len(author) > 2:
            checks += 1

        if item.get("published_date"):
            checks += 1

        content = item.get("content_text") or ""
        if re.search(r"\[\d+\]|\(\d{4}\)|https?://", content):
            checks += 1

        links = item.get("external_links")
        if isinstance(links, list) and len(links) >= 2:
            checks += 1

        return checks / 4.0

    def _compression_ratio(self, text: str) -> float:
        """zlib ratio — repetitive/generated text compresses unusually well."""
        data = text.encode("utf-8", errors="ignore")
        if len(data) < 200:
            return 0.5
        return len(zlib.compress(data, 6)) / len(data)

    def _burstiness(self, text: str) -> float:
        """Coefficient of variation of sentence lengths (human prose is bursty)."""
        sentences = [s.strip() for s in re.split(r'[.!?]+', text) if len(s.strip()) > 5]
        if len(sentences) < 4:
            return 0.5

        lengths = [len(s.split()) for s in sentences]
        mean = sum(lengths) / len(lengths)
        if mean == 0:
            return 0.5
        variance = sum((l - mean) ** 2 for l in lengths) / len(lengths)
        return (variance ** 0.5) / mean

    def filter_content(self, contents: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """Filter a batch of content, separating human from AI"""
        human_content = []
        slop_content = []

        for item in contents:
            text = item.get("content_text", "")
            source = item.get("source_name", "")
            title = item.get("title", "")
            url = item.get("url", "")

            result = self.analyze(text, source, title, url, item)

            if result.is_slop:
                item["_slop_score"] = result.confidence
                item["_slop_signals"] = result.signals
                item["_slop_verdict"] = "rejected"
                slop_content.append(item)
            else:
                # Carry the verdict so the indexer's last-line gate can trust it
                item["_slop_score"] = result.confidence
                item["_slop_signals"] = result.signals
                item["_slop_verdict"] = "pass"
                item["_quality_score"] = 1 - result.confidence
                item["_source_trusted"] = result.source_trusted
                human_content.append(item)

        logger.info(
            f"Filtered: {len(human_content)} human, "
            f"{len(slop_content)} slop (out of {len(contents)})"
        )

        return human_content, slop_content


async def main():
    """Run slop detection on crawled data"""
    import glob

    data_files = sorted(glob.glob(os.path.join(DATA_DIR, "crawled_*.jsonl")))

    if not data_files:
        print("No crawled data found. Run crawler first.")
        return

    detector = SlopDetector()
    total_human = 0
    total_slop = 0

    for data_file in data_files:
        print(f"Processing {os.path.basename(data_file)}...")

        contents = []
        with open(data_file, "r") as f:
            for line in f:
                if line.strip():
                    contents.append(json.loads(line))

        human, slop = detector.filter_content(contents)
        total_human += len(human)
        total_slop += len(slop)

        filtered_file = data_file.replace("crawled_", "filtered_")
        with open(filtered_file, "w") as f:
            for item in human:
                f.write(json.dumps(item) + "\n")

        if slop:
            slop_file = data_file.replace("crawled_", "slop_")
            with open(slop_file, "w") as f:
                for item in slop:
                    f.write(json.dumps(item) + "\n")

    print(f"\n📊 Slop Detection Results")
    print(f"   Human content: {total_human}")
    print(f"   AI slop filtered: {total_slop}")
    print(f"   Pass rate: {total_human / max(total_human + total_slop, 1) * 100:.1f}%")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
