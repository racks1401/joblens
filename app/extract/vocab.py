"""Normalization vocabularies: skill synonyms, degree codes, specializations, Indian city names."""

from __future__ import annotations

import re

# alias -> canonical skill (all lowercase)
SKILL_SYNONYMS: dict[str, str] = {
    "js": "javascript", "ecmascript": "javascript", "ts": "typescript", "node": "node.js", "nodejs": "node.js",
    "node js": "node.js", "reactjs": "react", "react.js": "react", "react js": "react", "vuejs": "vue", "vue.js": "vue",
    "angularjs": "angular", "nextjs": "next.js", "next js": "next.js", "postgres": "postgresql", "psql": "postgresql",
    "mongo": "mongodb", "golang": "go", "py": "python", "python3": "python", "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn", "tf": "tensorflow", "k8s": "kubernetes", "ml": "machine learning",
    "dl": "deep learning", "nlp": "natural language processing", "cv": "computer vision", "llms": "llm",
    "large language models": "llm", "gen ai": "generative ai", "genai": "generative ai", "rest": "rest apis",
    "rest api": "rest apis", "restful": "rest apis", "restful apis": "rest apis", "aws cloud": "aws",
    "amazon web services": "aws", "gcp": "google cloud", "google cloud platform": "google cloud",
    "ms excel": "excel", "microsoft excel": "excel", "power bi": "power bi", "powerbi": "power bi",
    "c sharp": "c#", "csharp": "c#", "cpp": "c++", "dotnet": ".net", "html5": "html", "css3": "css",
    "springboot": "spring boot", "spring-boot": "spring boot", "ci cd": "ci/cd", "cicd": "ci/cd",
    "github actions": "ci/cd", "fast api": "fastapi", "huggingface": "hugging face", "pyspark": "spark",
    "apache spark": "spark", "apache kafka": "kafka", "objective c": "objective-c", "rag pipelines": "rag",
    "retrieval augmented generation": "rag", "vector databases": "vector database", "vector db": "vector database",
    "embedding": "embeddings", "dsa": "data structures", "data structures and algorithms": "data structures",
    "oops": "oop", "object oriented programming": "oop", "unix": "linux", "shell scripting": "bash",
}

# Skills the offline heuristic extractor looks for (canonical names + common aliases above).
SKILL_VOCAB: list[str] = [
    "python", "java", "javascript", "typescript", "go", "rust", "c++", "c#", "kotlin", "swift", "scala", "ruby",
    "php", "r", "sql", "nosql", "bash", "dart", "objective-c", "matlab",
    "react", "angular", "vue", "next.js", "node.js", "express", "django", "flask", "fastapi", "spring boot",
    "spring", "rails", ".net", "html", "css", "tailwind", "redux", "graphql", "rest apis", "grpc", "microservices",
    "android", "ios", "flutter", "react native", "jetpack compose",
    "postgresql", "mysql", "mongodb", "redis", "elasticsearch", "cassandra", "dynamodb", "sqlite", "firebase",
    "snowflake", "bigquery", "kafka", "spark", "hadoop", "airflow", "dbt", "etl", "data warehousing",
    "aws", "azure", "google cloud", "docker", "kubernetes", "terraform", "ci/cd", "linux", "git", "jenkins",
    "machine learning", "deep learning", "natural language processing", "computer vision", "pytorch",
    "tensorflow", "keras", "scikit-learn", "pandas", "numpy", "llm", "generative ai", "rag", "embeddings",
    "vector database", "langchain", "hugging face", "mlops", "statistics", "data analysis", "data visualization",
    "excel", "tableau", "power bi", "looker", "streamlit", "selenium", "cypress", "jest", "pytest",
    "data structures", "algorithms", "oop", "system design", "distributed systems", "figma", "seo", "salesforce",
    "unity", "iot", "embedded c", "verilog", "networking", "security",
]

# Patterns that should not count as skill mentions (too ambiguous as plain words).
_AMBIGUOUS = {"go", "r", "rust", "swift", "spring", "express", "security", "networking", "excel", "dart", "unity"}

DEGREE_PATTERNS: list[tuple[str, str, int]] = [
    # (code, regex, flags)
    ("BTECH", r"\bB\.?\s?Tech\b", re.I),
    ("BE", r"\bB\.E\.?(?=\W)|\bBE\b(?=\s*[/(,]|\s+(?:in|degree|or)\b)", 0),
    ("MTECH", r"\bM\.?\s?Tech\b", re.I),
    ("MCA", r"\bMCA\b", 0),
    ("BCA", r"\bBCA\b", 0),
    ("BSC", r"\bB\.?\s?Sc\b", re.I),
    ("MSC", r"\bM\.?\s?Sc\b", re.I),
    ("MBA", r"\bMBA\b", 0),
    ("PHD", r"\bPh\.?\s?D\b", re.I),
    ("ANY_GRADUATE", r"\bany (?:graduate|degree|stream graduate)s?\b|\bgraduates? (?:from|in) any\b|\ball graduates\b", re.I),
    ("BACHELORS", r"\bbachelor'?s?\b|\bbachelors\b|\bundergraduate degree\b|\bBS\b(?=\s*(?:/|or|in|degree))|\bB\.S\.", re.I),
    ("MASTERS", r"\bmaster'?s (?:degree|in)\b|\bmasters degree\b|\bMS\b(?=\s*(?:/|or|in|degree))|\bM\.S\.", 0),
]

# free-text degree alias -> code (used to normalize LLM output and user input)
DEGREE_ALIASES: dict[str, str] = {
    "btech": "BTECH", "b.tech": "BTECH", "b tech": "BTECH", "bachelor of technology": "BTECH",
    "be": "BE", "b.e": "BE", "b.e.": "BE", "bachelor of engineering": "BE",
    "mtech": "MTECH", "m.tech": "MTECH", "master of technology": "MTECH",
    "mca": "MCA", "master of computer applications": "MCA", "bca": "BCA", "bachelor of computer applications": "BCA",
    "bsc": "BSC", "b.sc": "BSC", "b.sc.": "BSC", "bachelor of science": "BSC", "msc": "MSC", "m.sc": "MSC",
    "master of science": "MSC", "mba": "MBA", "phd": "PHD", "ph.d": "PHD", "ph.d.": "PHD",
    "any graduate": "ANY_GRADUATE", "any_graduate": "ANY_GRADUATE", "graduate": "ANY_GRADUATE",
    "bachelors": "BACHELORS", "bachelor's": "BACHELORS", "bachelor": "BACHELORS", "bs": "BACHELORS",
    "masters": "MASTERS", "master's": "MASTERS", "ms": "MASTERS", "other": "OTHER",
}
BACHELOR_LEVEL = {"BTECH", "BE", "BCA", "BSC", "BACHELORS"}
MASTER_LEVEL = {"MTECH", "MCA", "MSC", "MBA", "MASTERS", "PHD"}

SPECIALIZATION_PATTERNS: list[tuple[str, str, int]] = [
    ("all", r"\ball (?:branches|streams|disciplines)\b|\bany (?:branch|stream|discipline|specialization)\b", re.I),
    ("Computer Science", r"\bcomputer science\b|\bCSE\b|\bCS\b(?=\s*(?:/|,|or|degree|graduates?|branch))", 0),
    ("Information Technology", r"\binformation technology\b|\bIT\b(?=\s*(?:/|,|or|branch|graduates?))", 0),
    ("Electronics", r"\belectronics\b|\bECE\b|\bE&TC\b", re.I),
    ("Electrical", r"\belectrical\b|\bEEE\b", re.I),
    ("Mechanical", r"\bmechanical\b", re.I),
    ("Civil", r"\bcivil engineering\b", re.I),
    ("Data Science", r"\bdata science\b", re.I),
    ("Mathematics", r"\bmathematics\b|\bmaths?\b(?=\s*(?:/|,|or|degree))", re.I),
    ("Statistics", r"\bstatistics\b(?=\s*(?:/|,|or|degree|graduates?))", re.I),
]

SPECIALIZATION_SYNONYMS: dict[str, str] = {
    "cse": "computer science", "cs": "computer science", "computer science and engineering": "computer science",
    "ee": "electrical engineering", "electrical": "electrical engineering", "mnc": "mathematics and computing",
    "m&c": "mathematics and computing", "math and computing": "mathematics and computing",
    "computer engineering": "computer science", "it": "information technology", "ece": "electronics",
    "electronics and communication": "electronics", "eee": "electrical engineering", "ai": "artificial intelligence",
    "ai & ml": "artificial intelligence", "aiml": "artificial intelligence", "ai & iot": "artificial intelligence",
    "data science": "data science", "ds": "data science",
}
# Specializations that count as computing for a "Computer Science" requirement.
COMPUTING_FAMILY = {"computer science", "information technology", "artificial intelligence", "data science",
                    "computer applications", "software engineering"}

CITY_ALIASES: dict[str, str] = {
    "bangalore": "Bengaluru", "bengaluru": "Bengaluru", "gurgaon": "Gurugram", "gurugram": "Gurugram",
    "bombay": "Mumbai", "mumbai": "Mumbai", "new delhi": "Delhi", "delhi": "Delhi", "delhi ncr": "Delhi NCR",
    "ncr": "Delhi NCR", "noida": "Noida", "hyderabad": "Hyderabad", "pune": "Pune", "chennai": "Chennai",
    "madras": "Chennai", "kolkata": "Kolkata", "calcutta": "Kolkata", "ahmedabad": "Ahmedabad", "jaipur": "Jaipur",
    "kochi": "Kochi", "cochin": "Kochi", "coimbatore": "Coimbatore", "indore": "Indore", "chandigarh": "Chandigarh",
    "trivandrum": "Thiruvananthapuram", "thiruvananthapuram": "Thiruvananthapuram", "mysore": "Mysuru",
    "mysuru": "Mysuru",
}


def norm_skill(s: str) -> str:
    """Lowercase, trim, apply synonym map."""
    k = re.sub(r"\s+", " ", s.strip().lower())
    return SKILL_SYNONYMS.get(k, k)


def norm_degree(s: str) -> str:
    """Map a degree string to a fixed code; unknown strings become OTHER."""
    k = re.sub(r"\s+", " ", s.strip().lower()).rstrip(".")
    if s.strip().upper() in {c for c, _, _ in DEGREE_PATTERNS} | {"OTHER"}:
        return s.strip().upper()
    return DEGREE_ALIASES.get(k, DEGREE_ALIASES.get(k.replace(".", ""), "OTHER"))


_CITY_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, CITY_ALIASES), key=len, reverse=True)) + r")\b", re.I)


def norm_location(s: str) -> str:
    """Canonical Indian city name if one appears in the phrase; else the first comma part, title-cased."""
    m = _CITY_RE.search(s)
    if m:
        return CITY_ALIASES[m.group(1).lower()]
    k = re.sub(r"^(?:on-?site|hybrid|remote|office)\s+(?:in|-)\s+", "", s.strip(), flags=re.I)
    k = k.split(",")[0].strip()
    if re.fullmatch(r"(?i)india(?: locations?)?|pan[- ]india|multiple locations", k):
        return "India"
    return k.title()


def norm_specialization(s: str) -> str:
    k = re.sub(r"\s+", " ", s.strip().lower())
    if k in {"all", "any", "all branches", "any branch"}:
        return "all"
    return SPECIALIZATION_SYNONYMS.get(k, k)


def skill_regex(skill: str) -> str:
    """Word-boundary regex for a vocab skill and its aliases."""
    aliases = [skill] + [a for a, c in SKILL_SYNONYMS.items() if c == skill and len(a) > 2]
    parts = [re.escape(a).replace(r"\ ", r"[\s-]?") for a in aliases]
    return r"(?<![\w.+#])(?:" + "|".join(parts) + r")(?![\w+#])"


SKILL_RES: list[tuple[str, re.Pattern[str]]] = [
    (s, re.compile(skill_regex(s), 0 if s in _AMBIGUOUS else re.I)) for s in SKILL_VOCAB
]
# Ambiguous words only count with their usual capitalization (e.g. "Go", "R", "Rust", "Excel").
_CASED = {"go": "Go", "r": "R", "rust": "Rust", "swift": "Swift", "spring": "Spring", "express": "Express",
          "security": "Security", "networking": "Networking", "excel": "Excel", "dart": "Dart", "unity": "Unity"}
SKILL_RES = [(s, re.compile(r"(?<![\w.+#])" + re.escape(_CASED[s]) + r"(?![\w+#])") if s in _CASED else p)
             for s, p in SKILL_RES]
# "Go" and "R" as words are still risky; require a programming context for them.
CONTEXT_REQUIRED = {"go", "r", "security", "networking"}
